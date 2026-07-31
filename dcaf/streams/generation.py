# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Generation stream module for physical energy quantities.

Provides Generation (immutable data point), GenerationStream (functional-style
container), and GenerationGroup (grouped container) for modeling MWh production.
"""

import datetime as dt
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Iterable, TypeVar, assert_never, cast, overload

from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.finance.escalation import (
    ConstantRateEscalation,
    EscalationPolicy,
    _resolve_escalation_policy_override,
)
from dcaf.shared.time import (
    elapsed_hours,
    period_window_event_date,
    period_windows,
)
from dcaf.shared.types import (
    DayCountConvention,
    Period,
    ProFormaCategory,
    SupportsLessThan,
    TaxTreatment,
    TimingConvention,
    normalize_cashflow_classification,
)
from dcaf.shared.validation import validate_finite, validate_non_negative
from dcaf.streams.base import BaseGroup, BaseStream
from dcaf.metrics.npv import npv

KeyType = TypeVar("KeyType")


@dataclass(frozen=True)
class Generation:
    """
    Immutable representation of a single generation data point.

    Attributes
    ----------
    amount_mwh : float
        Energy produced in MWh.
    date : date
        Recognition date used to place the entry in time for grouping, pricing,
        escalation, discounting, and conversion to cashflows. When
        ``period_start`` and ``period_end`` are provided, this is the booking
        date for generation accumulated over that period rather than the date
        on which all generation physically occurred. It is normally selected
        from the period according to a timing convention, such as its beginning,
        middle, or final day.
    label : str
        Descriptive label.
    period_start : date or None
        Inclusive start of the period represented by ``amount_mwh``. Must be
        provided together with ``period_end``.
    period_end : date or None
        Exclusive end of the period represented by ``amount_mwh``. Must be
        provided together with ``period_start``.

    Raises
    ------
    ValueError
        If only one period bound is provided or the period is empty or reversed.

    Notes
    -----
    ``period_start`` and ``period_end`` describe the physical generation interval
    using half-open ``[period_start, period_end)`` semantics. If they are omitted,
    the entry is treated as a point event occurring on ``date``. The recognition
    date remains the date used by stream operations and financial calculations in
    both cases.
    """

    amount_mwh: float
    date: date
    label: str = ""
    period_start: dt.date | None = None
    period_end: dt.date | None = None

    def __post_init__(self) -> None:
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("period_start and period_end must be provided together")
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_end <= self.period_start
        ):
            raise ValueError("period_end must be after period_start")

    def replace(
        self,
        amount_mwh: float | None = None,
        date: dt.date | None = None,
        label: str | None = None,
        period_start: dt.date | None = None,
        period_end: dt.date | None = None,
    ) -> "Generation":
        """
        Return a new version of this Generation with the specified changes to parameters.

        Parameters
        ----------
        amount_mwh: float | None = None
        date: date | None = None
            Replacement recognition date. For an interval-backed entry, changing
            this does not change its physical generation period.
        label: str | None = None
        period_start: date | None = None
        period_end: date | None = None

        Returns
        -------
        Generation
            A new Generation instance with the specified parameters updated to the new values provided.

        Examples
        --------
        >>> # Change the label
        >>> gen = Generation(1000, date(2026, 1, 1), label="old_gen")
        >>> new_gen = gen.replace(label="new_gen")

        >>> # Increase the amount
        >>> gen = Generation(500, date(2026, 1, 1))
        >>> larger_gen = gen.replace(amount_mwh=gen.amount_mwh*1.2)

        >>> # Perform multiple modifications
        >>> from dateutil.relativedelta import relativedelta
        >>> old_gen = Generation(300, date(2027, 6, 1))
        >>> new_gen = old_gen.replace(
        ...     amount_mwh = old_gen.amount_mwh - 50,
        ...     date = (old_gen.date - relativedelta(months=6)),
        ... )
        >>> # This decreases the generation amount by 50 and moves it forward 6 months
        """
        return Generation(
            amount_mwh=self.amount_mwh if amount_mwh is None else amount_mwh,
            date=self.date if date is None else date,
            label=self.label if label is None else label,
            period_start=self.period_start if period_start is None else period_start,
            period_end=self.period_end if period_end is None else period_end,
        )


def _generation_escalation(
    *,
    entries: list[Generation],
    escalation: float,
    escalation_period: Period,
    amount_reference_date: date | None,
    day_count_convention: DayCountConvention,
    escalation_policy: EscalationPolicy | None,
) -> EscalationPolicy:
    """Normalize generation escalation kwargs into a date-based policy."""
    policy_override = _resolve_escalation_policy_override(
        escalation=escalation,
        escalation_period=escalation_period,
        amount_reference_date=amount_reference_date,
        escalation_policy=escalation_policy,
        default_escalation_period="year",
    )
    if policy_override is not None:
        return policy_override
    reference_date = (
        min(entry.date for entry in entries)
        if amount_reference_date is None
        else amount_reference_date
    )
    return ConstantRateEscalation(
        reference_date=reference_date,
        rate=escalation,
        period=escalation_period,
        day_count_convention=day_count_convention,
    )


def _validate_outage_inputs(
    *, capacity_mw: float, capacity_factor: float, start: date, end: date, capacity_reduction: float
) -> None:
    """Validate common outage interval and capacity inputs."""
    if end <= start:
        raise ValueError("outage end must be after outage start")

    validate_finite(capacity_reduction, "capacity_reduction")
    validate_non_negative(capacity_mw, "capacity_mw")
    validate_non_negative(capacity_factor, "capacity_factor")
    if not 0.0 <= capacity_reduction <= 1.0:
        raise ValueError("capacity_reduction must be between 0 and 1")


def _outage_event_date(*, start: date, end: date, timing: TimingConvention) -> date:
    """Return the booking date for an inclusive-start, exclusive-end outage interval."""
    inclusive_end = end - timedelta(days=1)
    match timing:
        case "begin":
            return start
        case "middle":
            return start + timedelta(days=(inclusive_end - start).days // 2)
        case "end":
            return inclusive_end
        case _:
            assert_never(timing)


@dataclass
class GenerationGroup(BaseGroup[KeyType, Generation, "GenerationStream"]):
    """
    Dictionary-like container mapping group keys to ``GenerationStream`` objects.

    Produced by :meth:`GenerationStream.group_by`. Supports aggregation,
    selective group-wise transformation, group filtering, and flattening back
    to a single stream.

    Examples
    --------
    Group an annual stream by year and aggregate total MWh:

    >>> from datetime import date
    >>> from dcaf.streams import GenerationStream
    >>> stream = GenerationStream.from_capacity(500, 0.92, date(2030, 1, 1), 5)
    >>> by_year = stream.group_by(period="year")
    >>> mwh_totals = by_year.sum()

    Keep only groups above a MWh threshold:

    >>> large_groups = by_year.filter_groups(lambda key, s: s.sum() > 1_000_000)

    Flatten back to a single stream:

    >>> combined = by_year.ungroup()
    >>> combined.count() == stream.count()
    True
    """

    def _empty_stream(self) -> "GenerationStream":
        """Return an empty stream for internal regrouping helpers."""
        return GenerationStream()


@dataclass
class GenerationStream(BaseStream[Generation]):
    """
    Container for ``Generation`` entries with a fluent API.

    Mirrors the ``CashFlowStream`` pattern for physical energy quantities.
    Supports iteration, indexing, slicing, and ``len()``, alongside
    domain-specific helpers for building, filtering, transforming, and
    converting generation to revenue or cost cashflows. Mutating-style methods
    return a new ``GenerationStream`` without modifying the source stream. The
    container itself is not immutable: callers can mutate its public :attr:`entries`
    list directly.

    Attributes
    ----------
    entries : list[Generation]
        Public mutable list containing the stream's frozen ``Generation`` values.

    Examples
    --------
    Model annual generation and convert to revenue cashflows:

    >>> from datetime import date
    >>> from dcaf.streams import GenerationStream
    >>> gen = GenerationStream.from_capacity(
    ...     capacity_mw=1_200.0,
    ...     capacity_factor=0.92,
    ...     start=date(2030, 1, 1),
    ...     periods=5,
    ... )
    >>> revenue = gen.to_revenue(price_per_mwh=55.0, escalation=0.02)
    >>> revenue.count()
    5

    Index and slice like a sequence:

    >>> gen[1:3].count()
    2
    """

    def _amount(self, entry: Generation) -> float:
        """Return the numeric amount for internal shared helpers."""
        return entry.amount_mwh

    @classmethod
    def from_capacity(
        cls,
        capacity_mw: float,
        capacity_factor: float,
        start: date,
        periods: int | float,
        frequency: Period = "year",
        label: str = "Generation",
        day_count_convention: DayCountConvention = "actual/actual",
        *,
        timing: TimingConvention = "end",
    ) -> "GenerationStream":
        """
        Generate a stream of periodic generation from capacity parameters.

        Parameters
        ----------
        capacity_mw : float
            Installed capacity in MW.
        capacity_factor : float
            Capacity factor as a decimal (e.g. 0.92 for 92%).
        start : date
            Date of the first generation entry.
        periods : int or float
            Number of periods. Fractional periods include the final complete
            days that fit in the requested period count. If the requested end
            falls within a day, the incomplete day is omitted and a warning is
            raised.
        frequency : Period, optional
            Generation frequency. Default ``"year"``.
        label : str, optional
            Label applied to every generated entry. Default is ``"Generation"``.
        day_count_convention : DayCountConvention, optional
            Day-count convention used to compute elapsed capacity hours.
        timing : {"end", "begin", "middle"}, optional
            Booking-date convention for each generated period. Default is
            ``"end"``, which books on the final included date of the period.

        Returns
        -------
        GenerationStream
            New stream containing one generation entry per modeled period.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(
        ...     capacity_mw=100.0,
        ...     capacity_factor=0.9,
        ...     start=date(2030, 1, 1),
        ...     periods=2,
        ... )
        >>> stream.count()
        2
        """
        validate_non_negative(capacity_mw, "capacity_mw")
        validate_finite(capacity_factor, "capacity_factor")
        if not 0.0 <= capacity_factor <= 1.0:
            raise ValueError("capacity_factor must be between 0 and 1")

        entries: list[Generation] = []
        windows = period_windows(
            start,
            periods,
            frequency,
            day_count_convention,
            context="GenerationStream.from_capacity periods",
        )
        for window in windows:
            hours = elapsed_hours(window.start, window.end, day_count_convention)
            mwh = capacity_mw * capacity_factor * hours
            entries.append(
                Generation(
                    amount_mwh=mwh,
                    date=period_window_event_date(window, timing),
                    label=label,
                    period_start=window.start,
                    period_end=window.end,
                )
            )
        return cls(entries)

    @classmethod
    def from_outage(
        cls,
        *,
        capacity_mw: float,
        capacity_factor: float,
        start: date,
        end: date,
        capacity_reduction: float = 1.0,
        timing: TimingConvention = "end",
        label: str = "Generation Outage",
        day_count_convention: DayCountConvention = "actual/actual",
    ) -> "GenerationStream":
        """
        Create a negative generation stream for an explicit outage interval.

        The returned stream is a normal ``GenerationStream`` containing one
        ``Generation`` entry with negative MWh. It is intended for delta-style
        modeling where lost production can be passed through existing
        generation-to-cashflow methods such as :meth:`to_revenue` and
        :meth:`to_cost`.

        Parameters
        ----------
        capacity_mw : float
            Capacity affected by the outage in MW.
        capacity_factor : float
            Counterfactual capacity factor that would have applied during the
            outage interval.
        start : date
            Inclusive outage start date.
        end : date
            Exclusive outage end date. The outage duration is ``end - start``.
        capacity_reduction : float, optional
            Fraction of the affected capacity unavailable during the outage.
            ``1.0`` means fully offline and ``0.5`` means half output lost.
        timing : {"begin", "middle", "end"}, optional
            Date assigned to the negative generation entry. ``"begin"`` uses
            ``start``, ``"end"`` uses the final outage day, and ``"middle"``
            uses the midpoint of the inclusive outage dates.
        label : str, optional
            Label for the negative generation entry.
        day_count_convention : DayCountConvention, optional
            Day-count convention used to compute elapsed outage hours.

        Returns
        -------
        GenerationStream
            Stream containing one negative generation entry.

        Raises
        ------
        ValueError
            If the date range is empty or invalid, numeric inputs are not
            finite, capacity inputs are negative, or ``capacity_reduction`` is
            outside ``[0, 1]``.

        Examples
        --------
        >>> from datetime import date
        >>> outage = GenerationStream.from_outage(
        ...     capacity_mw=1000.0,
        ...     capacity_factor=0.92,
        ...     start=date(2030, 5, 1),
        ...     end=date(2030, 5, 11),
        ... )
        >>> outage.sum()
        -220800.0
        """
        _validate_outage_inputs(
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            start=start,
            end=end,
            capacity_reduction=capacity_reduction,
        )
        hours = elapsed_hours(start, end, day_count_convention)
        lost_mwh = capacity_mw * capacity_factor * capacity_reduction * hours
        return cls(
            [
                Generation(
                    amount_mwh=-lost_mwh,
                    date=_outage_event_date(start=start, end=end, timing=timing),
                    label=label,
                    period_start=start,
                    period_end=end,
                )
            ]
        )

    @classmethod
    def from_streams(
        cls, *iterables: "GenerationStream | Generation | Iterable[Generation]"
    ) -> "GenerationStream":
        """
        Create a ``GenerationStream`` from streams, entries, or iterables of entries.

        Parameters
        ----------
        *iterables : GenerationStream or Generation or Iterable[Generation]
            Sources whose entries should be concatenated in order.

        Returns
        -------
        GenerationStream
            New stream containing all provided generation entries.

        Examples
        --------
        >>> g1 = Generation(100.0, date(2030, 1, 1))
        >>> g2 = Generation(200.0, date(2031, 1, 1))
        >>> stream = GenerationStream.from_streams(g1, [g2])
        >>> stream.count()
        2
        """
        return super().from_streams(*iterables)

    def with_capacity(
        self,
        capacity_mw: float,
        capacity_factor: float,
        start: date,
        periods: int | float,
        frequency: Period = "year",
        label: str = "Generation",
        day_count_convention: DayCountConvention = "actual/actual",
        *,
        timing: TimingConvention = "end",
    ) -> "GenerationStream":
        """
        Generate additional capacity-based entries and append them to this stream.

        Parameters
        ----------
        capacity_mw : float
            Installed capacity in MW for the appended entries.
        capacity_factor : float
            Capacity factor as a decimal.
        start : date
            Date of the first appended generation entry.
        periods : int or float
            Number of periods to generate. Fractional periods include the final
            complete days that fit in the requested period count.
        frequency : Period, optional
            Generation frequency. Default ``"year"``.
        label : str, optional
            Label applied to every appended entry. Default is ``"Generation"``.
        day_count_convention : DayCountConvention, optional
            Day-count convention used to compute elapsed capacity hours.
        timing : {"end", "begin", "middle"}, optional
            Booking-date convention for each generated period. Default is
            ``"end"``.

        Returns
        -------
        GenerationStream
            New stream containing the original and appended entries.

        Examples
        --------
        >>> base = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2)
        >>> expanded = base.with_capacity(50, 0.8, date(2032, 1, 1), 1)
        >>> expanded.count()
        3
        """
        new = GenerationStream.from_capacity(
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            start=start,
            periods=periods,
            frequency=frequency,
            label=label,
            day_count_convention=day_count_convention,
            timing=timing,
        )
        return self.extend(new)

    def filter(self, fn: Callable[[Generation], bool]) -> "GenerationStream":
        """
        Return a new stream filtered by a predicate.

        Parameters
        ----------
        fn : Callable
            Predicate receiving a Generation; only entries where it returns
            True are kept.

        Returns
        -------
        GenerationStream
            New stream containing only entries that match the predicate.

        Examples
        --------
        >>> stream.filter(lambda g: g.amount_mwh > 1000)
        GenerationStream(...)
        """
        return self._filter_where(fn)

    @overload
    def group_by(self, fn: Callable[[Generation], KeyType]) -> "GenerationGroup[KeyType]": ...
    @overload
    def group_by(self, fn: None = None, *, period: Period) -> "GenerationGroup[date]": ...

    def group_by(  # type: ignore[misc]
        self, fn: Callable[[Generation], Any] | None = None, *, period: Period | None = None
    ) -> "GenerationGroup[Any]":
        """
        Group entries by a key function or by time period.

        Parameters
        ----------
        fn : Callable, optional
            Key function mapping each Generation to a hashable group key.
            Mutually exclusive with ``period``.
        period : Period, optional
            Group by time period (``"day"``, ``"month"``, ``"quarter"``, ``"year"``).

        Returns
        -------
        GenerationGroup[Any]
            Grouped container mapping each computed key to a ``GenerationStream``.

        Raises
        ------
        ValueError
            If both ``fn`` and ``period`` are supplied or neither is supplied.

        Examples
        --------
        >>> stream.group_by(lambda g: g.date.year)
        GenerationGroup(...)
        >>> stream.group_by(period="month")
        GenerationGroup(...)
        """
        if fn is not None and period is not None:
            raise ValueError("Cannot pass both a key function and 'period' to group_by()")
        if fn is None and period is None:
            raise ValueError("group_by() requires a key function or 'period' argument")

        if fn is not None:
            groups = self._grouped_entries_by_key(fn)
            return GenerationGroup(cast(dict[Any, GenerationStream], self._grouped_streams(groups)))

        assert period is not None
        per_groups = self._grouped_entries_by_period(period)
        return GenerationGroup(
            cast(dict[date, GenerationStream], self._grouped_streams(per_groups))
        )

    @overload
    def sort(
        self, fn: Callable[[Generation], SupportsLessThan], *, descending: bool = ...
    ) -> "GenerationStream": ...
    @overload
    def sort(self, *, attr: str, descending: bool = ...) -> "GenerationStream": ...
    @overload
    def sort(self) -> "GenerationStream": ...

    def sort(
        self,
        fn: Callable[[Generation], SupportsLessThan] | None = None,
        *,
        attr: str | None = None,
        descending: bool = False,
    ) -> "GenerationStream":
        """
        Return a new ``GenerationStream`` sorted by key function or attribute.

        Parameters
        ----------
        fn : Callable[[Generation], SupportsLessThan], optional
            Sort key function applied to each entry.
        attr : {"date", "amount_mwh", "label"}, optional
            Named ``Generation`` attribute to sort by.
        descending : bool, optional
            If ``True``, sort in descending order.

        Returns
        -------
        GenerationStream
            New sorted stream.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
        >>> stream.sort(attr="date")[0].date
        datetime.date(2030, 12, 31)
        >>> stream.sort(lambda g: g.amount_mwh, descending=True).count()
        3
        """
        if fn is not None and attr is not None:
            raise ValueError("Cannot pass both a key function and 'attr' to sort()")
        if attr not in (None, "date", "amount_mwh", "label"):
            raise AssertionError(f"Unexpected sort attribute: {attr!r}")
        if fn is not None:
            return super().sort(fn, descending=descending)
        if attr is None:
            return super().sort()
        return super().sort(attr=attr, descending=descending)

    def scale(self, factor: float) -> "GenerationStream":
        """
        Multiply all generation amounts by the provided factor.

        Parameters
        ----------
        factor: float
            The value by which to scale the generation amounts.

        Returns
        -------
        GenerationStream
            A new GenerationStream with scaled generation.

        Examples
        --------
        >>> # Add 20% to all generation amounts
        >>> scaled_gen_stream = gen_stream.scale(1.2)
        """
        return GenerationStream([e.replace(e.amount_mwh * factor) for e in self.entries])

    def discounted_sum(
        self, rate: float, valuation_date: date, convention: DayCountConvention = "actual/actual"
    ) -> float:
        """
        Compute the present-value-weighted sum of MWh (for LCOE denominator).

        Each generation entry is discounted by (1 + rate)^t where t is the
        year fraction from valuation_date to the entry's date.

        Parameters
        ----------
        rate : float
            Annual discount rate. Must be finite and greater than ``-1``.
        valuation_date : date
            Reference date.
        convention : DayCountConvention, optional
            Day count convention.

        Returns
        -------
        float
            Discounted total MWh.

        Raises
        ------
        ValueError
            If ``rate`` is not finite or is less than or equal to ``-1``.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2)
        >>> stream.discounted_sum(rate=0.08, valuation_date=date(2030, 1, 1)) > 0
        True
        """
        values = ((entry.amount_mwh, entry.date) for entry in self.entries)
        return npv(values, rate, valuation_date, convention)

    def to_revenue(
        self,
        price_per_mwh: float,
        escalation: float = 0.0,
        label: str = "Generation Revenue",
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE,
        tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE,
        *,
        escalation_period: Period = "year",
        amount_reference_date: date | None = None,
        day_count_convention: DayCountConvention = "actual/actual",
        escalation_policy: EscalationPolicy | None = None,
    ) -> CashFlowStream:
        """
        Convert generation entries to revenue cashflows.

        Parameters
        ----------
        price_per_mwh : float
            Base price per MWh.
        escalation : float, optional
            Compound escalation rate for the price, interpreted over
            ``escalation_period``. With the default
            ``escalation_period="year"``, ``0.02`` means 2% year-on-year
            escalation.
        escalation_period : Period, optional
            Compounding period associated with ``escalation``. Default is
            ``"year"``.
        amount_reference_date : date, optional
            Date at which ``price_per_mwh`` is known. Defaults to the earliest
            generation entry date.
        day_count_convention : DayCountConvention, optional
            Day-count convention used for annual price escalation.
        escalation_policy : EscalationPolicy, optional
            Advanced override for custom escalation behavior. When provided, it
            must not be combined with ``escalation``, ``escalation_period``, or
            ``amount_reference_date``.
        label : str, optional
            Label applied to every generated cashflow. Default is
            ``"Generation Revenue"``.
        pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category for the revenue flows. Default is ``"revenue"``.
        tax_treatment : TaxTreatment or str, optional
            Tax treatment for the revenue flows. Default is ``"taxable"``.

        Returns
        -------
        CashFlowStream
            Cashflow stream with positive revenue amounts for each generation entry.

        Examples
        --------
        >>> gen = GenerationStream.from_capacity(1000, 0.92, date(2025, 1, 1), 5)
        >>> revenue = gen.to_revenue(50.0, escalation=0.02)
        """
        validate_non_negative(price_per_mwh, "price_per_mwh")

        if not self.entries:
            return CashFlowStream()
        escalation_policy = _generation_escalation(
            entries=self.entries,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            day_count_convention=day_count_convention,
            escalation_policy=escalation_policy,
        )
        resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category, tax_treatment
        )
        entries: list[CashFlow] = []
        for entry in self.entries:
            price = price_per_mwh * escalation_policy.factor(entry.date)
            entries.append(
                CashFlow(
                    amount=entry.amount_mwh * price,
                    date=entry.date,
                    label=label,
                    is_cash=True,
                    pro_forma_category=resolved_category,
                    tax_treatment=resolved_tax_treatment,
                )
            )
        return CashFlowStream(entries)

    def to_cost(
        self,
        rate_per_mwh: float,
        escalation: float = 0.0,
        label: str = "Variable Cost",
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.OPERATING_COST,
        tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
        *,
        escalation_period: Period = "year",
        amount_reference_date: date | None = None,
        day_count_convention: DayCountConvention = "actual/actual",
        escalation_policy: EscalationPolicy | None = None,
    ) -> CashFlowStream:
        """
        Convert generation entries to variable cost cashflows (negative amounts).

        Parameters
        ----------
        rate_per_mwh : float
            Base cost rate per MWh (positive number; flows will be negative).
        escalation : float, optional
            Compound escalation rate, interpreted over ``escalation_period``.
            With the default ``escalation_period="year"``, this is an annual
            escalation rate.
        escalation_period : Period, optional
            Compounding period associated with ``escalation``. Default is
            ``"year"``.
        amount_reference_date : date, optional
            Date at which ``rate_per_mwh`` is known. Defaults to the earliest
            generation entry date.
        day_count_convention : DayCountConvention, optional
            Day-count convention used for annual cost escalation.
        escalation_policy : EscalationPolicy, optional
            Advanced override for custom escalation behavior. When provided, it
            must not be combined with ``escalation``, ``escalation_period``, or
            ``amount_reference_date``.
        label : str, optional
            Label applied to every generated cashflow. Default is
            ``"Variable Cost"``.
        pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category for the cost flows. Default is ``"operating_cost"``.
        tax_treatment : TaxTreatment or str, optional
            Tax treatment for the cost flows. Default is ``"deductible"``.

        Returns
        -------
        CashFlowStream
            Cashflow stream with negative variable cost amounts for each generation entry.

        Examples
        --------
        >>> gen = GenerationStream.from_capacity(1000, 0.92, date(2025, 1, 1), 5)
        >>> costs = gen.to_cost(5.0, escalation=0.02)
        """
        validate_non_negative(rate_per_mwh, "rate_per_mwh")

        if not self.entries:
            return CashFlowStream()
        escalation_policy = _generation_escalation(
            entries=self.entries,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            day_count_convention=day_count_convention,
            escalation_policy=escalation_policy,
        )
        resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category, tax_treatment
        )
        entries: list[CashFlow] = []
        for entry in self.entries:
            cost = rate_per_mwh * escalation_policy.factor(entry.date)
            entries.append(
                CashFlow(
                    amount=-entry.amount_mwh * cost,
                    date=entry.date,
                    label=label,
                    is_cash=True,
                    pro_forma_category=resolved_category,
                    tax_treatment=resolved_tax_treatment,
                )
            )
        return CashFlowStream(entries)
