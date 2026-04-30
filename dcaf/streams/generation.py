"""
Generation stream module for physical energy quantities.

Provides Generation (single data point), GenerationStream (container),
and GenerationGroup (grouped container) for modeling MWh production
from various sources and energy carriers.
"""

import datetime as dt
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from typing import (
    Any,
    Callable,
    Iterable,
    Literal,
    TypeVar,
    assert_never,
    overload,
)

from dcaf.streams.cashflows import (
    CashFlow,
    CashFlowStream,
)
from dcaf.finance.escalation import (
    ConstantRateEscalation,
    EscalationPolicy,
    _resolve_escalation_policy_override,
)
from dcaf.shared.formatting import format_label
from dcaf.shared.time import (
    hours_per_period,
    time_delta_per_period,
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
        Date of the generation.
    source : str
        Generation source identifier (e.g. "uprate", "unit_1").
    carrier : str
        Energy carrier type (e.g. "electricity", "hydrogen").
    label : str
        Descriptive label.
    """

    amount_mwh: float
    date: date
    source: str = ""
    carrier: str = "electricity"
    label: str = ""

    def replace(
        self,
        amount_mwh: float | None = None,
        date: dt.date | None = None,
        source: str | None = None,
        carrier: str | None = None,
        label: str | None = None,
    ) -> "Generation":
        """
        Return a new version of this Generation with the specified changes to parameters.

        Parameters
        ----------
        amount_mwh: float | None = None
        date: date | None = None
        source: str | None = None
        carrier: str | None = None
        label: str | None = None

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
            source=self.source if source is None else source,
            carrier=self.carrier if carrier is None else carrier,
            label=self.label if label is None else label,
        )


def _generation_escalation(
    *,
    entries: list[Generation],
    escalation: float,
    escalation_period: Period,
    amount_reference_date: date | None,
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
    )


def _validate_outage_inputs(
    *,
    capacity_mw: float,
    capacity_factor: float,
    start: date,
    end: date,
    capacity_reduction: float,
) -> None:
    """Validate common outage interval and capacity inputs."""
    if end <= start:
        raise ValueError("outage end must be after outage start")
    for name, value in (
        ("capacity_mw", capacity_mw),
        ("capacity_factor", capacity_factor),
        ("capacity_reduction", capacity_reduction),
    ):
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")
    if capacity_mw < 0.0:
        raise ValueError("capacity_mw must be non-negative")
    if capacity_factor < 0.0:
        raise ValueError("capacity_factor must be non-negative")
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
    Group a multi-source stream and aggregate total MWh per source:

    >>> from datetime import date
    >>> from dcaf.streams import GenerationStream
    >>> stream = GenerationStream.from_streams(
    ...     GenerationStream.from_capacity(500, 0.92, date(2030, 1, 1), 5, source="unit_1"),
    ...     GenerationStream.from_capacity(300, 0.88, date(2030, 1, 1), 5, source="unit_2"),
    ... )
    >>> by_source = stream.group_by(source=True)
    >>> mwh_totals = by_source.sum()

    Keep only groups above a MWh threshold:

    >>> large_groups = by_source.filter_groups(lambda key, s: s.sum() > 1_000_000)

    Flatten back to a single stream:

    >>> combined = by_source.ungroup()
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
    converting generation to revenue or cost cashflows. All mutating-style
    operations return a new ``GenerationStream``; the original is never modified.

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
    ...     source="uprate",
    ... )
    >>> revenue = gen.to_revenue(price_per_mwh=55.0, escalation=0.02)
    >>> revenue.count()
    5

    Combine two generation sources and group by source:

    >>> base = GenerationStream.from_capacity(1_000, 0.90, date(2030, 1, 1), 3, source="base")
    >>> uprate = GenerationStream.from_capacity(200, 0.92, date(2030, 1, 1), 3, source="uprate")
    >>> combined = GenerationStream.from_streams(base, uprate)
    >>> by_source = combined.group_by(source=True)
    >>> list(by_source.keys())
    ['base', 'uprate']

    Index and slice like a sequence:

    >>> gen[0].source
    'uprate'
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
        periods: int,
        frequency: Period = "year",
        source: str = "",
        carrier: str = "electricity",
        label: str = "Generation",
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
        periods : int
            Number of periods.
        frequency : Period, optional
            Generation frequency. Default ``"year"``.
        source : str, optional
            Source identifier.
        carrier : str, optional
            Energy carrier. Default ``"electricity"``.
        label : str, optional
            Label template. ``{n}`` is replaced with the 1-based period index.

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
        ...     source="unit_1",
        ... )
        >>> stream.count()
        2
        >>> stream[0].source
        'unit_1'
        """
        entries: list[Generation] = []
        hours = hours_per_period(frequency)
        time_delta = time_delta_per_period(frequency)
        current_date = start
        for i in range(periods):
            mwh = capacity_mw * capacity_factor * hours
            gen_label = format_label(label, i + 1)
            entries.append(
                Generation(
                    amount_mwh=mwh,
                    date=current_date,
                    source=source,
                    carrier=carrier,
                    label=gen_label,
                )
            )
            current_date += time_delta
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
        source: str = "",
        carrier: str = "electricity",
        label: str = "Generation Outage",
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
        source : str, optional
            Source identifier for the outage entry.
        carrier : str, optional
            Energy carrier. Default is ``"electricity"``.
        label : str, optional
            Label for the negative generation entry.

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
        days = (end - start).days
        lost_mwh = capacity_mw * capacity_factor * capacity_reduction * 24.0 * days
        return cls(
            [
                Generation(
                    amount_mwh=-lost_mwh,
                    date=_outage_event_date(start=start, end=end, timing=timing),
                    source=source,
                    carrier=carrier,
                    label=label,
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
        >>> g1 = Generation(100.0, date(2030, 1, 1), source="unit_1")
        >>> g2 = Generation(200.0, date(2031, 1, 1), source="unit_2")
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
        periods: int,
        frequency: Period = "year",
        source: str = "",
        carrier: str = "electricity",
        label: str = "Generation",
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
        periods : int
            Number of periods to generate.
        frequency : Period, optional
            Generation frequency. Default ``"year"``.
        source : str, optional
            Source identifier for the appended entries.
        carrier : str, optional
            Energy carrier for the appended entries.
        label : str, optional
            Label template for the appended entries. ``{n}`` is
            replaced with the 1-based period index.

        Returns
        -------
        GenerationStream
            New stream containing the original and appended entries.

        Examples
        --------
        >>> base = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a")
        >>> expanded = base.with_capacity(50, 0.8, date(2030, 1, 1), 1, source="b")
        >>> expanded.count()
        3
        """
        new = GenerationStream.from_capacity(
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            start=start,
            periods=periods,
            frequency=frequency,
            source=source,
            carrier=carrier,
            label=label,
        )
        return self.extend(new)

    def filter(
        self,
        fn: Callable[[Generation], bool] | None = None,
        *,
        source: str | None = None,
        carrier: str | None = None,
    ) -> "GenerationStream":
        """
        Return a new stream filtered by a predicate or keyword constraints.

        Parameters
        ----------
        fn : Callable, optional
            Predicate receiving a Generation; only entries where it returns True are kept.
            Mutually exclusive with keyword arguments.
        source : str, optional
            Keep only entries with exactly this source value.
        carrier : str, optional
            Keep only entries with exactly this carrier value.

        Returns
        -------
        GenerationStream
            New stream containing only entries that match the predicate or field filters.

        Raises
        ------
        ValueError
            If both a predicate and keyword filters are supplied, or if no filter
            criterion is supplied.

        Examples
        --------
        >>> stream.filter(lambda g: g.amount_mwh > 1000)
        GenerationStream(...)
        >>> stream.filter(source="uprate")
        GenerationStream(...)
        >>> stream.filter(source="unit_1", carrier="electricity")
        GenerationStream(...)

        Notes
        -----
        Multiple keyword filters are combined with AND semantics.
        """
        kwargs_given = source is not None or carrier is not None
        if fn is not None and kwargs_given:
            raise ValueError(
                "Cannot pass both a predicate function and keyword arguments to filter()"
            )
        if fn is None and not kwargs_given:
            raise ValueError("Provide either a predicate function or keyword arguments.")
        if fn is not None:
            return self._filter_where(fn)

        return self._filter_by_attrs(source=source, carrier=carrier)

    @overload
    def group_by(
        self, fn: Callable[[Generation], KeyType]
    ) -> "GenerationGroup[KeyType]": ...
    @overload
    def group_by(self, fn: None = None, *, source: Literal[True]) -> "GenerationGroup[str]": ...
    @overload
    def group_by(self, fn: None = None, *, carrier: Literal[True]) -> "GenerationGroup[str]": ...
    @overload
    def group_by(self, fn: None = None, *, period: Period) -> "GenerationGroup[date]": ...

    def group_by(  # type: ignore[misc]
        self,
        fn: Callable[[Generation], Any] | None = None,
        *,
        source: Literal[True] | None = None,
        carrier: Literal[True] | None = None,
        period: Period | None = None,
    ) -> "GenerationGroup[Any]":
        """
        Group entries by a key function or a named field.

        Parameters
        ----------
        fn : Callable, optional
            Key function mapping each Generation to a hashable group key.
            Mutually exclusive with keyword arguments.
        source : Literal[True], optional
            Group by the ``source`` field.
        carrier : Literal[True], optional
            Group by the ``carrier`` field.
        period : Period, optional
            Group by time period (``"day"``, ``"month"``, ``"quarter"``, ``"year"``).

        Returns
        -------
        GenerationGroup[Any]
            Grouped container mapping each computed key to a ``GenerationStream``.

        Raises
        ------
        ValueError
            If more than one grouping mode is requested, or if no grouping mode
            is provided.

        Examples
        --------
        >>> stream.group_by(lambda g: g.date.year)
        GenerationGroup(...)
        >>> stream.group_by(source=True)
        GenerationGroup(...)
        >>> stream.group_by(carrier=True)
        GenerationGroup(...)
        >>> stream.group_by(period="month")
        GenerationGroup(...)
        """
        # We blend two API styles in one function call:
        #   1. Provide a callable function which returns the value to group by
        #   2. Indicate a `Generation` field to group by
        # These are mutually exclusive, and we currently support only choosing one field by
        # which to group using the field keyword args.
        kwargs_given = source is not None or carrier is not None or period is not None
        if fn is not None and kwargs_given:
            raise ValueError("Cannot pass both a key function and keyword arguments to group_by()")
        n_kwargs = sum(x is not None for x in (source, carrier, period))
        if n_kwargs > 1:
            raise ValueError("group_by() accepts at most one keyword argument")
        if fn is None and not kwargs_given:
            raise ValueError("group_by() requires a key function or keyword argument")

        if fn is not None:
            groups = self._grouped_entries_by_key(fn)
            return GenerationGroup(self._grouped_streams(groups))

        if source is True:
            src_groups: dict[str, list[Generation]] = self._grouped_entries_by_attr("source")
            return GenerationGroup(self._grouped_streams(src_groups))

        if carrier is True:
            car_groups: dict[str, list[Generation]] = self._grouped_entries_by_attr("carrier")
            return GenerationGroup(self._grouped_streams(car_groups))

        # period is set
        assert period is not None
        per_groups = self._grouped_entries_by_period(period)
        return GenerationGroup(self._grouped_streams(per_groups))

    @overload
    def sort(
        self, fn: Callable[[Generation], SupportsLessThan], *, descending: bool = ...
    ) -> "GenerationStream": ...
    @overload
    def sort(
        self,
        *,
        attr: str,
        descending: bool = ...,
    ) -> "GenerationStream": ...
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
        attr : {"date", "amount_mwh", "source", "carrier", "label"}, optional
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
        datetime.date(2030, 1, 1)
        >>> stream.sort(lambda g: g.amount_mwh, descending=True).count()
        3
        """
        if fn is not None and attr is not None:
            raise ValueError("Cannot pass both a key function and 'attr' to sort()")
        if attr not in (None, "date", "amount_mwh", "source", "carrier", "label"):
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

        >>> # Reduce generation amounts from an uprate by 10%
        >>> uprate_stream = stream.filter(source="uprate")
        >>> non_uprate_stream = GenerationStream(
        ...     entries=list(set(stream.entries) - set(uprate_stream.entries))
        ... )
        >>> scaled_uprate_stream = uprate_stream.scale(0.9)
        >>> result_stream = GenerationStream.from_streams(scaled_uprate_stream, non_uprate_stream)
        """
        return GenerationStream([e.replace(e.amount_mwh * factor) for e in self.entries])

    def discounted_sum(
        self,
        rate: float,
        valuation_date: date,
        convention: DayCountConvention = "actual/365",
    ) -> float:
        """
        Compute the present-value-weighted sum of MWh (for LCOE denominator).

        Each generation entry is discounted by (1 + rate)^t where t is the
        year fraction from valuation_date to the entry's date.

        Parameters
        ----------
        rate : float
            Discount rate.
        valuation_date : date
            Reference date.
        convention : DayCountConvention, optional
            Day count convention.

        Returns
        -------
        float
            Discounted total MWh.

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
        escalation_policy : EscalationPolicy, optional
            Advanced override for custom escalation behavior. When provided, it
            must not be combined with ``escalation``, ``escalation_period``, or
            ``amount_reference_date``.
        label : str, optional
            Label template. ``{n}`` is replaced with the 1-based period index.
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
        if not self.entries:
            return CashFlowStream()
        escalation_policy = _generation_escalation(
            entries=self.entries,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            escalation_policy=escalation_policy,
        )
        resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category,
            tax_treatment,
        )
        entries: list[CashFlow] = []
        for i, entry in enumerate(self.entries):
            price = price_per_mwh * escalation_policy.factor(entry.date)
            flow_label = format_label(label, i + 1)
            entries.append(
                CashFlow(
                    amount=entry.amount_mwh * price,
                    date=entry.date,
                    label=flow_label,
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
        escalation_policy : EscalationPolicy, optional
            Advanced override for custom escalation behavior. When provided, it
            must not be combined with ``escalation``, ``escalation_period``, or
            ``amount_reference_date``.
        label : str, optional
            Label template. ``{n}`` is replaced with the 1-based period index.
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
        if not self.entries:
            return CashFlowStream()
        escalation_policy = _generation_escalation(
            entries=self.entries,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            escalation_policy=escalation_policy,
        )
        resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category,
            tax_treatment,
        )
        entries: list[CashFlow] = []
        for i, entry in enumerate(self.entries):
            cost = rate_per_mwh * escalation_policy.factor(entry.date)
            flow_label = format_label(label, i + 1)
            entries.append(
                CashFlow(
                    amount=-entry.amount_mwh * cost,
                    date=entry.date,
                    label=flow_label,
                    is_cash=True,
                    pro_forma_category=resolved_category,
                    tax_treatment=resolved_tax_treatment,
                )
            )
        return CashFlowStream(entries)
