"""
Core cashflow abstractions for discounted cash-flow analysis.

Provides CashFlow (immutable data point), CashFlowStream (functional container),
CashFlowGroup (grouped container), and structured cashflow classification fields.
"""

import datetime as dt
from dataclasses import dataclass
from datetime import date
from typing import (
    Any,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Literal,
    Optional,
    overload,
)

from dcaf.streams.base import BaseGroup, BaseStream
from dcaf.finance.escalation import (
    ConstantRateEscalation,
    EscalationPolicy,
    _constant_discount_policy,
    _resolve_escalation_policy_override,
)
from dcaf.shared.types import (
    DayCountConvention,
    Period,
    ProFormaCategory,
    SupportsLessThan,
    TaxTreatment,
    normalize_cashflow_classification,
    normalize_pro_forma_category,
    parse_pro_forma_category,
    parse_tax_treatment,
)
from dcaf.shared.formatting import format_label
from dcaf.shared.time import time_delta_per_period, timedelta_fractional_years


class _UnsetType:
    """Sentinel type for optional filter arguments."""


_UNSET = _UnsetType()


def _recurring_escalation(
    *,
    start: date,
    escalation: float,
    escalation_period: Period,
    amount_reference_date: date | None,
    escalation_policy: EscalationPolicy | None,
) -> EscalationPolicy:
    """Normalize recurring cashflow escalation kwargs into a date-based policy."""
    policy_override = _resolve_escalation_policy_override(
        escalation=escalation,
        escalation_period=escalation_period,
        amount_reference_date=amount_reference_date,
        escalation_policy=escalation_policy,
        default_escalation_period="year",
    )
    if policy_override is not None:
        return policy_override
    return ConstantRateEscalation(
        reference_date=start if amount_reference_date is None else amount_reference_date,
        rate=escalation,
        period=escalation_period,
    )


@dataclass(frozen=True)
class CashFlow:
    """
    Immutable representation of a single cashflow.

    Attributes
    ----------
    amount : float
        The monetary amount of the cashflow (positive for inflows, negative for outflows).
    date : date
        The date when the cashflow occurs.
    label : str
        Optional descriptive label for the cashflow.
    is_cash : bool
        True for cash-basis items, False for accrual-basis items (e.g., depreciation).
    pro_forma_category : ProFormaCategory or None
        Presentation category used for pro-forma grouping.
    tax_treatment : TaxTreatment
        Tax classification used for taxable-income assembly.
    """

    amount: float
    date: date
    label: str = ""
    is_cash: bool = True
    pro_forma_category: ProFormaCategory | None = ProFormaCategory.OTHER
    tax_treatment: TaxTreatment = TaxTreatment.NONE

    def __post_init__(self) -> None:
        if self.pro_forma_category is not None:
            object.__setattr__(
                self,
                "pro_forma_category",
                parse_pro_forma_category(self.pro_forma_category),
            )
        object.__setattr__(
            self,
            "tax_treatment",
            parse_tax_treatment(self.tax_treatment),
        )

    def replace(
        self,
        amount: float | None = None,
        date: dt.date | None = None,
        label: str | None = None,
        is_cash: bool | None = None,
        pro_forma_category: ProFormaCategory | str | None | _UnsetType = _UNSET,
        tax_treatment: TaxTreatment | str | _UnsetType = _UNSET,
    ) -> "CashFlow":
        """
        Return a new version of this CashFlow with the specified changes to parameters.

        Parameters
        ----------
        amount: float | None = None
        date: date | None = None
        label: str | None = None
        is_cash: bool | None = None
        pro_forma_category: ProFormaCategory | str | None, optional
            Updated pro-forma category. Pass ``None`` to clear the category.
        tax_treatment: TaxTreatment | str, optional
            Updated tax treatment.

        Returns
        -------
        CashFlow
            A new CashFlow instance with the specified parameters updated to the new values provided.

        Examples
        --------
        >>> # Change the label
        >>> cf = CashFlow(1000, date(2026, 1, 1), label="old_cf")
        >>> new_cf = cf.replace(label="new_cf")

        >>> # Increase the magnitude of the amount
        >>> cf = CashFlow(-500, date(2026, 1, 1))
        >>> new_amount = -100 if cf.amount < 0 else 100
        >>> larger_cf = cf.replace(amount=new_amount)

        >>> # Perform multiple modifications
        >>> old_cf = CashFlow(-3000, date(2027, 6, 1), pro_forma_category="operating_cost")
        >>> new_cf = old_cf.replace(
        ...     amount = old_cf.amount + 500,
        ...     date = (old_cf.date + relativedelta(months=6)),
        ... )
        >>> # This reduces the expense magnitude by 500 and moves it back 6 months
        """
        resolved_category = (
            self.pro_forma_category
            if isinstance(pro_forma_category, _UnsetType)
            else normalize_pro_forma_category(pro_forma_category)
        )
        resolved_tax_treatment = (
            self.tax_treatment
            if isinstance(tax_treatment, _UnsetType)
            else parse_tax_treatment(tax_treatment)
        )
        return CashFlow(
            amount=self.amount if amount is None else amount,
            date=self.date if date is None else date,
            label=self.label if label is None else label,
            is_cash=self.is_cash if is_cash is None else is_cash,
            pro_forma_category=resolved_category,
            tax_treatment=resolved_tax_treatment,
        )

    def to_stream(self) -> "CashFlowStream":
        """
        Wrap the CashFlow object in a CashFlowStream

        Returns
        -------
        CashFlowStream
            A CashFlowStream object with only the current CashFlow in the stream
        """
        return CashFlowStream([self])


@dataclass
class CashFlowGroup[KeyType](BaseGroup[KeyType, CashFlow, "CashFlowStream"]):
    """
    A dictionary-like container for grouped CashFlows.

    Maps group keys to CashFlowStream objects, enabling aggregation and analysis
    across groups.
    """

    def _empty_stream(self) -> "CashFlowStream":
        """Return an empty stream for internal regrouping helpers."""
        return CashFlowStream()

    def aggregate[T](self, fn: Callable[["CashFlowStream"], T]) -> dict[KeyType, T]:
        """
        Aggregate each group using a function.

        Applies the function to each group's CashFlowStream and returns a dictionary
        mapping each group key to its aggregated value.

        If wanting to chain operations within the `CashFlowGroup` object, use the
        `apply_to_groups` function instead.

        Parameters
        ----------
        fn : Callable
            A function that takes a CashFlowStream and returns an aggregated value
            (e.g., sum of amounts, count of flows, average, etc.).

        Returns
        -------
        dict
            A dictionary mapping group keys to aggregated values.

        Examples
        --------
        >>> # Group by date and get total amount per date
        >>> grouped = stream.group_by(lambda cf: cf.date)
        >>> totals = grouped.aggregate(lambda s: s.sum())
        >>> # Returns: {date1: 1000.0, date2: 2000.0, ...}

        >>> # Get count of cashflows per year
        >>> by_year = stream.group_by(lambda cf: cf.date.year)
        >>> counts = by_year.aggregate(lambda s: s.count())
        >>> # Returns: {2023: 15, 2024: 23, ...}
        """
        return super().aggregate(fn)

    def apply_to_groups(
        self,
        fn: Callable[["CashFlowStream"], "CashFlowStream"],
        keys: KeyType | Collection[KeyType] | None = None,
    ) -> "CashFlowGroup":
        """
        Apply a function to each group's CashFlowStream, optionally filtered by keys.

        Applies the provided function to groups within this CashFlowGroup,
        transforming each selected group's CashFlowStream and returning a new
        CashFlowGroup with the results. Groups not selected remain unchanged.
        This enables selective group-wise transformations while preserving
        the overall grouping structure.

        If wanting a return type from the transformation function that is *not* a
        `CashFlowStream`, use the `aggregate` function.

        Parameters
        ----------
        fn : Callable
            A function that takes a CashFlowStream and returns a transformed
            CashFlowStream. Applied to each selected group independently.
        keys : KeyType or Collection[KeyType], optional
            Group key(s) to which the transformation should be applied. Can be:
            - None (default): Apply transformation to all groups
            - A single key: Apply to only that group (works for any key type)
            - A collection of keys: Apply to those specific groups (list, tuple, set, etc.)

        Returns
        -------
        CashFlowGroup
            A new CashFlowGroup with the function applied to selected groups' streams.
            Non-selected groups remain unchanged.

        Raises
        ------
        ValueError
            If any provided key is not found in this CashFlowGroup.

        Examples
        --------
        >>> # Scale all amounts in each group by 1.1 (all groups)
        >>> by_category = stream.group_by_pro_forma_category()
        >>> scaled_groups = by_category.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: CashFlow(
        ...         cf.amount * 1.1,
        ...         cf.date,
        ...         cf.label,
        ...         cf.is_cash,
        ...         cf.pro_forma_category,
        ...         cf.tax_treatment,
        ...     ))
        ... )

        >>> # Filter each group to only include cashflows after a certain date
        >>> by_year = stream.group_by(period="year")
        >>> recent_groups = by_year.apply_to_groups(
        ...     lambda s: s.filter(lambda cf: cf.date >= date(2024, 1, 1))
        ... )

        >>> # Sort each group by date
        >>> sorted_groups = by_year.apply_to_groups(lambda s: s.sort())

        >>> # Apply transformation only to specific groups (sequence of keys)
        >>> by_category = stream.group_by_pro_forma_category()
        >>> scaled_revenue = by_category.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: cf.replace(amount=cf.amount * 1.05)),
        ...     keys=[ProFormaCategory.REVENUE]
        ... )

        >>> # Apply transformation to a single group
        >>> scaled_one_year = by_year.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: cf.replace(amount=cf.amount * 1.5)),
        ...     keys=date(2024, 1, 1)
        ... )
        """
        return super().apply_to_groups(fn, keys=keys)

    def filter_groups(
        self, fn: Callable[[KeyType, "CashFlowStream"], bool]
    ) -> "CashFlowGroup[KeyType]":
        """
        Return a new CashFlowGroup containing only groups where the predicate is True.

        Parameters
        ----------
        fn : Callable[[KeyType, CashFlowStream], bool]
            A predicate that receives each group's key and stream.

        Returns
        -------
        CashFlowGroup
            A new CashFlowGroup with only the matching groups.
        """
        return super().filter_groups(fn)

    def ungroup(self) -> "CashFlowStream":
        """
        Ungroup the cashflows and return them as a single CashFlowStream.

        Combines all cashflows from all groups into a single stream.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream containing all cashflows from all groups.

        Examples
        --------
        >>> # Group by period, then ungroup back to a single stream
        >>> by_month = stream.group_by(period="month")
        >>> ungrouped = by_month.ungroup()
        >>> # ungrouped contains all original cashflows

        >>> # Filter groups, then ungroup
        >>> by_year = stream.group_by(period="year")
        >>> recent_years = CashFlowGroup({
        ...     year: flows
        ...     for year, flows in by_year.items()
        ...     if year.year >= 2024
        ... })
        >>> recent_flows = recent_years.ungroup()

        >>> # Apply transformations to groups, then ungroup
        >>> by_category = stream.group_by_pro_forma_category()
        >>> revenue_scaled = by_category[ProFormaCategory.REVENUE].apply(
        ...     lambda cf: cf.replace(amount=cf.amount * 1.05)
        ... )
        >>> # Put back into a stream
        >>> scaled_stream = CashFlowGroup({ProFormaCategory.REVENUE: revenue_scaled}).ungroup()

        Notes
        -----
        - The resulting stream is not guaranteed to be in any particular order.
          Use `sort()` on the result if ordering is important.
        - If the same cashflow object appears in multiple groups of a manually
          constructed ``CashFlowGroup``, it will appear multiple times after
          ungrouping.
        """
        return super().ungroup()

    def keys(self) -> Iterable[KeyType]:
        """Return the group keys."""
        return super().keys()

    def values(self) -> Iterable["CashFlowStream"]:
        """Return the CashFlowStream values."""
        return super().values()

    def items(self) -> Iterable[tuple[KeyType, "CashFlowStream"]]:
        """Return key-value pairs of (key, CashFlowStream)."""
        return super().items()

    def __getitem__(self, key: KeyType) -> "CashFlowStream":
        """Access a specific group by key."""
        return super().__getitem__(key)

    def __len__(self) -> int:
        """Return the number of groups."""
        return super().__len__()

    def __iter__(self) -> Iterator[KeyType]:
        """Iterate over group keys."""
        return super().__iter__()

    def sum(self) -> dict[KeyType, float]:
        """
        Return the sum of amounts for each group.

        Convenience method equivalent to ``aggregate(lambda s: s.sum())``.

        Returns
        -------
        dict
            A dictionary mapping each group key to the sum of cashflow amounts
            in that group.

        Examples
        --------
        >>> # Get total amount per pro-forma category
        >>> by_category = stream.group_by_pro_forma_category()
        >>> totals = by_category.sum()
        >>> # Returns: {ProFormaCategory.REVENUE: 50000.0,
        >>> #           ProFormaCategory.OPERATING_COST: -20000.0, ...}

        >>> # Get monthly totals
        >>> by_month = stream.group_by(period="month")
        >>> monthly_totals = by_month.sum()
        >>> # Returns: {date(2024, 1, 1): 5000.0,
        >>> #           date(2024, 2, 1): 6000.0, ...}
        """
        return super().sum()

    def count(self) -> dict[KeyType, int]:
        """
        Return the count of cashflows for each group.

        Convenience method equivalent to ``aggregate(lambda s: s.count())``.

        Returns
        -------
        dict
            A dictionary mapping each group key to the number of cashflows
            in that group.

        Examples
        --------
        >>> # Get count per pro-forma category
        >>> by_category = stream.group_by_pro_forma_category()
        >>> counts = by_category.count()
        >>> # Returns: {ProFormaCategory.REVENUE: 15, ProFormaCategory.OPERATING_COST: 42, ...}

        >>> # Get cashflows per year
        >>> by_year = stream.group_by(period="year")
        >>> yearly_counts = by_year.count()
        >>> # Returns: {date(2023, 1, 1): 24, date(2024, 1, 1): 36, ...}
        """
        return super().count()


@dataclass
class CashFlowStream(BaseStream[CashFlow]):
    """
    Functional container for ``CashFlow`` entries.

    The stream preserves insertion order and supports common sequence-style
    operations such as iteration, indexing, slicing, and ``len()`` in addition
    to the domain-specific cashflow helpers defined on the class.
    """

    def _amount(self, entry: CashFlow) -> float:
        """Return the numeric amount for internal shared helpers."""
        return entry.amount

    @overload
    def __getitem__(self, index: int) -> CashFlow: ...

    @overload
    def __getitem__(self, index: slice) -> "CashFlowStream": ...

    def __getitem__(self, index: int | slice) -> "CashFlow | CashFlowStream":
        """
        Return a single cashflow or a sliced stream.

        Parameters
        ----------
        index : int or slice
            Integer position of a single cashflow, or a slice selecting a
            contiguous subset of the stream.

        Returns
        -------
        CashFlow or CashFlowStream
            A single ``CashFlow`` when *index* is an integer, or a new
            ``CashFlowStream`` containing the selected entries when *index* is
            a slice.

        Examples
        --------
        >>> stream = CashFlowStream.from_recurring(date(2026, 1, 1), 3, 100.0)
        >>> stream[0].amount
        100.0
        >>> stream[1:].count()
        2
        """
        return super().__getitem__(index)

    def __iter__(self) -> Iterator[CashFlow]:
        """
        Iterate over cashflows in insertion order.

        Returns
        -------
        Iterator[CashFlow]
            Iterator yielding each cashflow in the stream.

        Examples
        --------
        >>> stream = CashFlowStream.from_recurring(date(2026, 1, 1), 2, 100.0)
        >>> [cf.amount for cf in stream]
        [100.0, 100.0]
        """
        return super().__iter__()

    def __len__(self) -> int:
        """
        Return the number of cashflows in the stream.

        Returns
        -------
        int
            Number of cashflows stored in the stream.

        Examples
        --------
        >>> stream = CashFlowStream.from_recurring(date(2026, 1, 1), 4, 100.0)
        >>> len(stream)
        4
        """
        return super().__len__()

    @classmethod
    def from_recurring(
        cls,
        start: date,
        periods: int,
        amount: float,
        frequency: Period = "year",
        escalation: float = 0.0,
        label: str = "Recurring Payment",
        is_cash: bool = True,
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.OTHER,
        tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
        *,
        escalation_period: Period = "year",
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
    ) -> "CashFlowStream":
        """
        Generate recurring cashflows with optional escalation.

        Creates a stream of cashflows that occur at regular intervals with optional
        compound escalation. Commonly used for revenues, operating expenses, rent
        payments, dividends, and other recurring financial items.

        Parameters
        ----------
        start : date
            The date of the first cashflow.
        periods : int
            Number of periods (e.g., years if frequency='year', months if
            frequency='month').
        amount : float
            Base amount known at ``amount_reference_date``. Can be positive
            (inflows) or negative (outflows). If ``amount_reference_date`` is not
            provided, the amount is assumed to be known on ``start``.
        frequency : Period, optional
            Frequency of the cashflows. Default is "year".
            - "year": One cashflow per year
            - "quarter": Four cashflows per year (every 3 months)
            - "month": Twelve cashflows per year
            - "day": One cashflow per day
        escalation : float, optional
            Compound escalation rate as a decimal, interpreted over
            ``escalation_period``. With the default
            ``escalation_period="year"``, ``0.025`` means 2.5% year-on-year
            growth. Pass a different ``escalation_period`` to model rates
            quoted per month, quarter, or day. Default is 0 (no escalation).
        escalation_period : Period, optional
            Compounding period associated with ``escalation``. Default is
            ``"year"``. Pass a non-annual value such as ``"month"`` to model
            escalation rates quoted per month, quarter, or day.
        amount_reference_date : date, optional
            Date at which ``amount`` is known. Escalation is evaluated from this
            date to each payment date. Defaults to ``start``.
        escalation_policy : EscalationPolicy, optional
            Advanced override for custom escalation behavior. When provided, it
            must not be combined with ``escalation``, ``escalation_period``, or
            ``amount_reference_date``.
        label : str, optional
            Label for the cashflows. Can include {n} placeholder for period number
            (1-indexed). Default is "Recurring Payment".
        is_cash : bool, optional
            Whether the cashflows represent actual cash movements. Default is True.
        pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category applied to all generated flows. Default is ``"other"``.
        tax_treatment : TaxTreatment or str, optional
            Tax treatment applied to all generated flows. Default is ``"none"``.

        Returns
        -------
        CashFlowStream
            A new stream containing the recurring cashflows.

        Notes
        -----
        - Escalation is compounded, not simple interest.
        - Payment dates are generated from ``start`` and ``frequency``; escalation
          is then evaluated at each payment date using the configured reference date.
        - Date arithmetic handles month-end edge cases (e.g., Jan 31 + 1 month = Feb 28/29).
        """
        delta = time_delta_per_period(frequency)
        escalation_policy = _recurring_escalation(
            start=start,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            escalation_policy=escalation_policy,
        )
        resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category,
            tax_treatment,
        )
        entries = []
        for i in range(periods):
            flow_date = start + delta * i
            escalated_amount = amount * escalation_policy.factor(flow_date)
            flow_label = format_label(label, i + 1)
            entries.append(
                CashFlow(
                    amount=escalated_amount,
                    date=flow_date,
                    label=flow_label,
                    is_cash=is_cash,
                    pro_forma_category=resolved_category,
                    tax_treatment=resolved_tax_treatment,
                )
            )

        return cls(entries)

    @classmethod
    def from_streams(cls, *iterables) -> "CashFlowStream":
        """
        Create a CashFlowStream from multiple sources of cashflows.

        Accepts any combination of CashFlowStreams, individual CashFlows, lists of
        CashFlows, or other iterables containing CashFlow objects, and combines them
        into a single CashFlowStream. This is the primary way to combine multiple
        cashflow sources during model construction.

        Parameters
        ----------
        *iterables : CashFlowStream or CashFlow or Iterable[CashFlow]
            Variable number of cashflow sources. Can be:
            - CashFlowStream objects
            - Individual CashFlow objects
            - Lists of CashFlow objects
            - Any other iterable of CashFlow objects

        Returns
        -------
        CashFlowStream
            A new CashFlowStream containing all cashflows from all inputs.

        Examples
        --------
        >>> # Combine multiple streams generated by classmethods
        >>> revenue = CashFlowStream.from_recurring(...)
        >>> opex = CashFlowStream.from_recurring(...)
        >>> capex = CashFlowStream([...])  # Manual list
        >>> combined = CashFlowStream.from_streams(revenue, opex, capex)

        >>> # Mix streams, individual cashflows, and lists
        >>> construction_stream = CashFlowStream.from_recurring(...)
        >>> itc_flow = CashFlow(...)  # Single ITC payment
        >>> depreciation_flows = [...]  # List of depreciation cashflows
        >>> model = CashFlowStream.from_streams(
        ...     construction_stream,
        ...     itc_flow,  # Individual CashFlow now supported directly
        ...     depreciation_flows
        ... )

        >>> # Combine all cashflows for a project with individual flows
        >>> initial_investment = CashFlow(-1_000_000.0, date(2024, 1, 1))
        >>> project = CashFlowStream.from_streams(
        ...     initial_investment,  # Individual cashflow
        ...     CashFlowStream.from_recurring(start, 20, revenue, 'annual', escalation),
        ...     CashFlowStream.from_recurring(start, 20, opex, 'annual', opex_esc),
        ...     capex_stream,
        ...     depreciation_stream,
        ...     tax_stream
        ... )

        Notes
        -----
        - Order of cashflows in the result is determined by the order of arguments
          and the order within each iterable
        - No deduplication is performed - if the same CashFlow appears in multiple
          inputs, it will appear multiple times in the result
        - For ordered output, use .sort() on the result
        - This is more convenient than manual list concatenation when working with
          multiple CashFlowStream objects
        """
        return super().from_streams(*iterables)

    def append(self, flow: CashFlow) -> "CashFlowStream":
        """
        Return a new CashFlowStream with one cashflow appended.

        Parameters
        ----------
        flow : CashFlow
            Cashflow to append to the stream.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream containing all original cashflows plus *flow*.

        Examples
        --------
        >>> base = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
        >>> updated = base.append(CashFlow(-25.0, date(2026, 2, 1)))
        >>> updated.count()
        2
        """
        return super().append(flow)

    def extend(self, other: "CashFlowStream | Iterable[CashFlow]") -> "CashFlowStream":
        """
        Return a new CashFlowStream with additional cashflows appended.

        Parameters
        ----------
        other : CashFlowStream or Iterable[CashFlow]
            Cashflows to append.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream containing all original and additional cashflows.
        """
        return super().extend(other)

    def with_recurring(
        self,
        start: date,
        periods: int,
        amount: float,
        frequency: Period = "year",
        escalation: float = 0.0,
        label: str = "Recurring Payment",
        is_cash: bool = True,
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.OTHER,
        tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
        *,
        escalation_period: Period = "year",
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
    ) -> "CashFlowStream":
        """
        Chain ``from_recurring`` onto the current stream.

        Generates recurring cashflows and appends them to this stream, returning
        a new CashFlowStream. All parameters are forwarded to ``from_recurring``.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream with the recurring cashflows added.
        """
        recurring = CashFlowStream.from_recurring(
            start=start,
            periods=periods,
            amount=amount,
            frequency=frequency,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            escalation_policy=escalation_policy,
            label=label,
            is_cash=is_cash,
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
        )
        return self.extend(recurring)

    def apply(
        self,
        transform: Callable[[CashFlow], CashFlow],
        where: Callable[[CashFlow], bool] | None = None,
    ) -> "CashFlowStream":
        """
        Apply a functional transformation to each cashflow within the CashFlowStream
        for which the given (optional) condition is satisfied.

        Parameters
        ----------
        transform : Callable
            A callable that takes a CashFlow object and returns a modified CashFlow object.
        where : Callable
            A callable that takes a CashFlow object and returns a bool
            indicating whether to apply the transformation on that CashFlow.
            Defaults to applying the transformation to all CashFlows.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream object with the function applied to the specified cashflows.

        Examples
        --------
        >>> def scale_amount(cf: CashFlow) -> CashFlow:
        ...     return cf.replace(amount=cf.amount * 2)
        >>> stream = CashFlowStream([cf1, cf2])
        >>> scaled_stream = stream.apply(scale_amount)

        >>> # Apply discount factor to all amounts
        >>> discounted = stream.apply(lambda cf: cf.replace(amount=cf.amount * 0.9))

        >>> # Reclassify positive amounts as revenue
        >>> classified = stream.apply(
        ...     lambda cf: cf.replace(pro_forma_category=ProFormaCategory.REVENUE),
        ...     where=lambda cf: cf.amount > 0,
        ... )

        Notes
        -----
        This method returns a new CashFlowStream and does not modify the original.
        For operations on the entire stream (not element-wise), use ``apply_streamwise()``.
        """
        return super().apply(transform, where)

    def apply_streamwise(
        self, fn: Callable[["CashFlowStream"], "CashFlowStream"]
    ) -> "CashFlowStream":
        """
        Apply a function to an entire CashFlowStream.

        Unlike ``apply()`` which operates on each cashflow individually, this method
        passes the entire CashFlowStream to the function, allowing operations that
        depend on the full context of the stream and enabling composition with other
        CashFlowStream methods.

        Parameters
        ----------
        fn : Callable
            A callable that takes a CashFlowStream object and returns a CashFlowStream
            object. The function has access to the entire stream and can use any
            CashFlowStream methods.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream object with the function applied to the entire stream.

        Examples
        --------
        >>> def normalize_to_first(stream: CashFlowStream) -> CashFlowStream:
        ...     if not stream:
        ...         return stream
        ...     first_amount = stream[0].amount
        ...     return stream.apply(
        ...         lambda cf: cf.replace(amount=cf.amount / first_amount)
        ...     )
        >>> stream = CashFlowStream([cf1, cf2, cf3])
        >>> normalized_stream = stream.apply_streamwise(normalize_to_first)

        >>> # Or using lambda for composition
        >>> result = stream.apply_streamwise(lambda s: s.filter(some_pred).apply(scale_fn))
        """
        return super().apply_streamwise(fn)

    def flat_apply(self, fn: Callable[[CashFlow], Iterable[CashFlow]]) -> "CashFlowStream":
        """
        Flat-map: apply *fn* to each cashflow, collecting all produced flows.

        Parameters
        ----------
        fn : Callable[[CashFlow], Iterable[CashFlow]]
            A function that takes a CashFlow and returns zero or more CashFlows.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream containing all cashflows produced by *fn*.

        Examples
        --------
        >>> # Split each annual cashflow into monthly instalments
        >>> def monthly_split(cf: CashFlow) -> list[CashFlow]:
        ...     monthly = cf.amount / 12
        ...     return [CashFlow(monthly, cf.date + relativedelta(months=m))
        ...             for m in range(12)]
        >>> monthly_stream = stream.flat_apply(monthly_split)
        """
        return super().flat_apply(fn)

    def filter_apply(self, fn: Callable[[CashFlow], CashFlow | None]) -> "CashFlowStream":
        """
        Filter-and-transform: *fn* returns a transformed CashFlow or ``None`` to drop.

        Parameters
        ----------
        fn : Callable[[CashFlow], CashFlow | None]
            A function that takes a CashFlow and returns a (possibly transformed)
            CashFlow, or ``None`` to exclude it.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream with only the non-None results.

        Examples
        --------
        >>> # Double revenue flows, drop everything else
        >>> def double_revenue(cf: CashFlow) -> CashFlow | None:
        ...     if cf.pro_forma_category is ProFormaCategory.REVENUE:
        ...         return cf.replace(amount=cf.amount * 2)
        ...     return None
        >>> revenue_doubled = stream.filter_apply(double_revenue)
        """
        return super().filter_apply(fn)

    def filter(
        self,
        fn: Callable[[CashFlow], bool] | None = None,
        *,
        pro_forma_category: ProFormaCategory | str | None | _UnsetType = _UNSET,
        tax_treatment: TaxTreatment | str | _UnsetType = _UNSET,
        is_cash: bool | None = None,
    ) -> "CashFlowStream":
        """
        Return a new CashFlowStream object filtered by a predicate or keyword criteria.

        Accepts either a callable predicate **or** keyword arguments
        (``pro_forma_category``, ``tax_treatment``, ``is_cash``), but not both.
        Multiple keyword arguments are combined with
        AND semantics.

        Parameters
        ----------
        fn : Callable, optional
            A predicate function that takes a CashFlow object and returns a boolean.
        pro_forma_category : ProFormaCategory or str or None, optional
            Keep only cashflows in this pro-forma category. Pass ``None`` to
            select uncategorized flows.
        tax_treatment : TaxTreatment or str, optional
            Keep only cashflows with this tax treatment.
        is_cash : bool, optional
            Keep only cashflows where ``is_cash`` matches this value.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream containing only the matching cashflows.

        Raises
        ------
        ValueError
            If both *fn* and keyword arguments are provided, or if neither is.
        """
        has_kwargs = (
            not isinstance(pro_forma_category, _UnsetType)
            or not isinstance(tax_treatment, _UnsetType)
            or is_cash is not None
        )

        if fn is not None and has_kwargs:
            raise ValueError("Cannot combine a callable predicate with keyword arguments.")
        if fn is None and not has_kwargs:
            raise ValueError("Provide either a callable predicate or keyword arguments.")

        if fn is not None:
            return self._filter_where(fn)

        category_value = (
            parse_pro_forma_category(pro_forma_category)
            if not isinstance(pro_forma_category, _UnsetType) and pro_forma_category is not None
            else pro_forma_category
        )
        tax_value = (
            parse_tax_treatment(tax_treatment)
            if not isinstance(tax_treatment, _UnsetType)
            else tax_treatment
        )

        # Keyword-based filtering (AND semantics)
        return self._filter_where(
            lambda flow: (
                isinstance(pro_forma_category, _UnsetType)
                or flow.pro_forma_category == category_value
            )
            and (isinstance(tax_treatment, _UnsetType) or flow.tax_treatment == tax_value)
            and (is_cash is None or flow.is_cash is is_cash)
        )

    def inflows(self) -> "CashFlowStream":
        """Return only cashflows with positive amounts."""
        return self._filter_where(lambda flow: flow.amount > 0)

    def outflows(self) -> "CashFlowStream":
        """Return only cashflows with negative amounts."""
        return self._filter_where(lambda flow: flow.amount < 0)

    def cash_only(self) -> "CashFlowStream":
        """Return only cash-basis cashflows (``is_cash=True``)."""
        return self._filter_where(lambda flow: flow.is_cash)

    def date_range(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> "CashFlowStream":
        """
        Filter cashflows by inclusive date bounds.

        Parameters
        ----------
        start : date, optional
            Earliest date to include. If ``None``, no lower bound.
        end : date, optional
            Latest date to include. If ``None``, no upper bound.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream with only cashflows within the date range.
        """
        return super().date_range(start=start, end=end)

    @overload
    def group_by[KeyType](self, fn: Callable[[CashFlow], KeyType]) -> CashFlowGroup[KeyType]: ...
    @overload
    def group_by(self, *, period: Period) -> CashFlowGroup[date]: ...

    def group_by(
        self,
        fn: Callable[[CashFlow], Any] | None = None,
        *,
        period: Period | None = None,
    ) -> CashFlowGroup:
        """
        Group cashflows by a key function or by time period.

        Exactly one of *fn* or *period* must be provided.

        Parameters
        ----------
        fn : Callable, optional
            A key function applied to each cashflow.
        period : Period, optional
            Group by time period (``"day"``, ``"month"``, ``"quarter"``,
            ``"year"``).

        Returns
        -------
        CashFlowGroup
            A CashFlowGroup mapping keys to CashFlowStreams.

        Raises
        ------
        ValueError
            If not exactly one argument is provided.
        """
        provided = (fn is not None) + (period is not None)
        if provided != 1:
            raise ValueError("Provide exactly one of 'fn' or 'period'.")

        if fn is not None:
            groups = self._grouped_entries_by_key(fn)
            return CashFlowGroup(self._grouped_streams(groups))

        # period path
        assert period is not None
        period_groups = self._grouped_entries_by_period(period)
        return CashFlowGroup(self._grouped_streams(period_groups))

    def group_by_pro_forma_category(self) -> CashFlowGroup[ProFormaCategory | None]:
        """
        Group cashflows by their pro-forma category.

        This is sugar for ``group_by(lambda cf: cf.pro_forma_category)``.
        Uncategorized cashflows appear under the ``None`` key instead of
        being silently dropped.

        Returns
        -------
        CashFlowGroup
            Cashflows grouped by ``pro_forma_category``.
        """
        return self.group_by(lambda flow: flow.pro_forma_category)

    def group_by_tax_treatment(self) -> CashFlowGroup[TaxTreatment]:
        """Group cashflows by their tax treatment."""
        return self.group_by(lambda flow: flow.tax_treatment)

    def group_by_period(self, period: Period) -> CashFlowGroup[date]:
        """
        Group cashflows by time period.

        Groups cashflows into periods (day, month, quarter, or year) based on their
        dates. Each group contains all cashflows that fall within the same period.
        This is a convenience method that's sugar for ``group_by()`` with period logic.

        Parameters
        ----------
        period : Literal["day", "month", "quarter", "year"]
            The time period to group by:
            - "day": Group by exact date
            - "month": Group by calendar month (all cashflows in January 2024, etc.)
            - "quarter": Group by calendar quarter (Q1 = Jan-Mar, Q2 = Apr-Jun, etc.)
            - "year": Group by calendar year

        Returns
        -------
        CashFlowGroup
            A CashFlowGroup object mapping each period start date to a CashFlowStream
            containing all cashflows in that period.

        Examples
        --------
        >>> # Group by month and get monthly totals
        >>> by_month = stream.group_by_period("month")
        >>> monthly_totals = by_month.aggregate(lambda s: s.sum())
        >>> # Returns: {date(2024, 1, 1): 5000.0, date(2024, 2, 1): 6000.0, ...}

        >>> # Group by year and get yearly revenue
        >>> by_year = stream.group_by_period("year")
        >>> yearly_revenue = by_year.aggregate(
        ...     lambda s: s.filter(pro_forma_category=ProFormaCategory.REVENUE).sum()
        ... )

        >>> # Group by quarter
        >>> by_quarter = stream.group_by_period("quarter")
        >>> q1_2024 = by_quarter[date(2024, 1, 1)]  # All Q1 2024 cashflows

        Notes
        -----
        Period start dates are normalized to the beginning of each period:
        - Month: First day of the month (e.g., 2024-01-01, 2024-02-01)
        - Quarter: First day of the first month (e.g., 2024-01-01 for Q1, 2024-04-01 for Q2)
        - Year: First day of the year (e.g., 2024-01-01)
        - Day: The exact date (no normalization)

        This method is equivalent to:

        >>> by_month = stream.group_by(period="month")
        """
        return self.group_by(period=period)

    @overload
    def sort(
        self, fn: Callable[[CashFlow], SupportsLessThan], *, descending: bool = ...
    ) -> "CashFlowStream": ...
    @overload
    def sort(
        self,
        *,
        attr: str,
        descending: bool = ...,
    ) -> "CashFlowStream": ...
    @overload
    def sort(self) -> "CashFlowStream": ...

    def sort(
        self,
        fn: Callable[[CashFlow], SupportsLessThan] | None = None,
        *,
        attr: str | None = None,
        descending: bool = False,
    ) -> "CashFlowStream":
        """
        Return a new CashFlowStream with cashflows sorted by a key function or attribute.

        Accepts either a callable key function **or** an ``attr`` keyword, but not both.
        When called with no arguments, sorts by date ascending.

        Parameters
        ----------
        fn : Callable[[CashFlow], SupportsLessThan], optional
            A function that takes a CashFlow and returns a sortable key value.
            Mutually exclusive with *attr*.
        attr : Literal["date", "amount", "label"], optional
            A named CashFlow attribute to sort by.  Mutually exclusive with *fn*.
        descending : bool, optional
            If ``True``, sort in descending order. Default is ``False`` (ascending).

        Returns
        -------
        CashFlowStream
            A new sorted CashFlowStream.

        Raises
        ------
        ValueError
            If both *fn* and *attr* are provided.

        Examples
        --------
        >>> # Sort by date ascending (default)
        >>> sorted_stream = stream.sort()

        >>> # Sort by amount descending using attr keyword
        >>> sorted_stream = stream.sort(attr="amount", descending=True)

        >>> # Sort by a custom key function
        >>> sorted_stream = stream.sort(lambda cf: cf.date, descending=True)

        >>> # Sort by multiple keys: year then amount
        >>> sorted_stream = stream.sort(lambda cf: (cf.date.year, cf.amount))

        >>> # Chain with other operations
        >>> recent_revenue = (
        ...     stream.filter(pro_forma_category=ProFormaCategory.REVENUE).sort()[-10:]
        ... )  # Get 10 most recent revenue cashflows

        Notes
        -----
        This method returns a new CashFlowStream and does not modify the original.
        The sorting is stable, meaning that when multiple cashflows have the same
        key value, they maintain their original relative order.
        """
        if fn is not None and attr is not None:
            raise ValueError("Cannot pass both a key function and 'attr' to sort()")
        if attr not in (None, "date", "amount", "label"):
            raise AssertionError(f"Unexpected sort attribute: {attr!r}")
        if fn is not None:
            return super().sort(fn, descending=descending)
        if attr is None:
            return super().sort()
        return super().sort(attr=attr, descending=descending)

    def sort_by(
        self,
        attr: Literal["date", "amount", "label"] = "date",
        ascending: bool = True,
    ) -> "CashFlowStream":
        """
        Sort cashflows by a named attribute.

        .. deprecated::
            Use ``sort(attr=..., descending=...)`` instead.

        Parameters
        ----------
        attr : Literal["date", "amount", "label"], optional
            The cashflow attribute to sort by. Default is ``"date"``.
        ascending : bool, optional
            Sort in ascending order if ``True`` (default), descending if ``False``.

        Returns
        -------
        CashFlowStream
            A new sorted CashFlowStream.
        """
        return self.sort(attr=attr, descending=not ascending)

    def scale(self, factor: float) -> "CashFlowStream":
        """
        Multiply all cashflow amounts by the provided factor.

        Parameters
        ----------
        factor: float
            The value by which to scale the cashflow amounts.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream with scaled cashflows.

        Examples
        --------
        >>> # Change units from thousands to millions
        >>> stream_in_millions = stream_in_thousands.scale(1000)

        >>> # Add 20% to all cashflow amounts
        >>> scaled_stream = stream.scale(1.2)

        >>> # Reduce operating-cost amounts by 10%
        >>> operating_costs = stream.filter(
        ...     pro_forma_category=ProFormaCategory.OPERATING_COST
        ... )
        >>> other_flows = CashFlowStream(
        ...     entries=list(set(stream.entries) - set(operating_costs.entries))
        ... )
        >>> scaled_costs = operating_costs.scale(0.9)
        >>> result_stream = CashFlowStream.from_streams(scaled_costs, other_flows)
        """
        return CashFlowStream([cf.replace(amount=cf.amount * factor) for cf in self.entries])

    def sum(self) -> float:
        """
        Return the sum of all cashflow amounts.

        Returns
        -------
        float
            The sum of all cashflow amounts in the stream, including both
            positive (inflows) and negative (outflows) amounts.

        Examples
        --------
        >>> stream = CashFlowStream([
        ...     CashFlow(1000.0, date(2024, 1, 1)),
        ...     CashFlow(-500.0, date(2024, 2, 1)),
        ...     CashFlow(2000.0, date(2024, 3, 1))
        ... ])
        >>> total = stream.sum()
        >>> # Returns: 2500.0

        >>> # Get total revenue
        >>> revenue_total = stream.filter(pro_forma_category=ProFormaCategory.REVENUE).sum()

        >>> # Get net cash flow (cash flows only)
        >>> net_cash = stream.filter(lambda cf: cf.is_cash).sum()

        Notes
        -----
        Returns 0.0 for empty streams.
        """
        ## NOTE: Need to think about this and handling nested CFS. Jacob says we
        ## never want nested CFS. If you want to do anything nested, use a CashFlowGroup.
        return super().sum()

    def count(self) -> int:
        """
        Return the number of cashflows in the stream.

        Returns
        -------
        int
            The count of cashflows in the stream.

        Examples
        --------
        >>> stream = CashFlowStream([cf1, cf2, cf3])
        >>> n = stream.count()
        >>> # Returns: 3

        >>> # Count revenue items
        >>> revenue_count = stream.filter(pro_forma_category=ProFormaCategory.REVENUE).count()

        >>> # Check if stream is empty
        >>> if stream.count() == 0:
        ...     print("No cashflows")
        """
        return super().count()

    def min(self, key: Optional[Callable[[CashFlow], SupportsLessThan]] = None) -> CashFlow:
        """
        Return the cashflow with the minimum value.

        Parameters
        ----------
        key : Callable, optional
            A function to extract a comparison key from each cashflow.
            If None, compares by amount (default).

        Returns
        -------
        CashFlow
            The cashflow with the minimum value according to the key function.

        Raises
        ------
        ValueError
            If the stream is empty.

        Examples
        --------
        >>> # Get cashflow with smallest amount
        >>> smallest = stream.min()
        >>> # Returns the most negative cashflow (largest outflow)

        >>> # Get earliest cashflow by date
        >>> earliest = stream.min(key=lambda cf: cf.date)

        >>> # Get cashflow with smallest absolute value
        >>> closest_to_zero = stream.min(key=lambda cf: abs(cf.amount))
        """
        if not self.entries:
            raise ValueError("min() called on empty CashFlowStream")
        if key is None:
            return min(self.entries, key=lambda cf: cf.amount)
        return min(self.entries, key=key)

    def max(self, key: Optional[Callable[[CashFlow], SupportsLessThan]] = None) -> CashFlow:
        """
        Return the cashflow with the maximum value.

        Parameters
        ----------
        key : Callable, optional
            A function to extract a comparison key from each cashflow.
            If None, compares by amount (default).

        Returns
        -------
        CashFlow
            The cashflow with the maximum value according to the key function.

        Raises
        ------
        ValueError
            If the stream is empty.

        Examples
        --------
        >>> # Get cashflow with largest amount
        >>> largest = stream.max()
        >>> # Returns the most positive cashflow (largest inflow)

        >>> # Get most recent cashflow by date
        >>> most_recent = stream.max(key=lambda cf: cf.date)

        >>> # Get cashflow with largest absolute value
        >>> largest_magnitude = stream.max(key=lambda cf: abs(cf.amount))
        """
        if not self.entries:
            raise ValueError("max() called on empty CashFlowStream")
        if key is None:
            return max(self.entries, key=lambda cf: cf.amount)
        return max(self.entries, key=key)

    def npv(
        self, rate: float, valuation_date: date, convention: DayCountConvention = "actual/365"
    ) -> float:
        """
        Calculate the Net Present Value (NPV) of the cashflow stream.

        Computes the NPV by discounting or compounding all cashflows to the
        valuation date using the specified discount rate. Cashflows occurring
        after the valuation date are discounted back, while cashflows occurring
        before the valuation date are compounded forward.

        Parameters
        ----------
        rate : float
            The annual discount rate as a decimal (e.g., 0.10 for 10%).
        valuation_date : date
            The date at which to calculate the present value. This is the reference
            point for all discounting/compounding calculations.
        convention : DayCountConvention, optional
            The day count convention for converting days to year fractions.
            Default is "actual/365" (standard economics convention).

        Returns
        -------
        float
            The net present value of all cash cashflows in the stream, evaluated
            at the valuation date.

        Examples
        --------
        >>> # Calculate NPV at project start with 10% discount rate
        >>> stream = CashFlowStream([
        ...     CashFlow(-10000.0, date(2024, 1, 1)),  # Initial investment
        ...     CashFlow(5000.0, date(2024, 6, 1)),    # Return after 5 months
        ...     CashFlow(6000.0, date(2025, 1, 1))     # Return after 1 year
        ... ])
        >>> npv_value = stream.npv(rate=0.10, valuation_date=date(2024, 1, 1))

        >>> # Calculate NPV at a future date (compounds past cashflows forward)
        >>> npv_future = stream.npv(rate=0.08, valuation_date=date(2025, 6, 1))

        >>> # Use for investment decision making
        >>> discount_rate = 0.12
        >>> if stream.npv(discount_rate, date.today()) > 0:
        ...     print("Investment has positive NPV - accept project")

        >>> # Compare NPV across different scenarios
        >>> conservative = stream.npv(0.15, date(2024, 1, 1))
        >>> optimistic = stream.npv(0.08, date(2024, 1, 1))

        Notes
        -----
        - Only cashflows with `is_cash=True` are included in the calculation, as
          non-cash items (e.g., depreciation) don't represent actual cash movements.
        - Time differences are calculated in days and converted to years using the
          specified day count convention (default: actual/365).
        - The discount formula is: PV = CF / (1 + r)^t where t can be positive
          (future cashflows) or negative (past cashflows).
        - When t is negative (past cashflows), dividing by (1+r)^negative effectively
          compounds the value forward to the valuation date.
        - Returns 0.0 for empty streams or streams with no cash flows.
        """
        discount_policy = _constant_discount_policy(
            valuation_date=valuation_date,
            rate=rate,
            convention=convention,
        )
        total = 0.0
        for flow in self.entries:
            if not flow.is_cash:
                continue
            total += flow.amount / discount_policy.factor(flow.date)

        return total

    def irr(
        self,
        convention: DayCountConvention = "actual/365",
        *,
        tol: float = 1e-8,
        max_iter: int = 100,
    ) -> float:
        """
        Calculate the Internal Rate of Return (IRR) of the cashflow stream.

        The IRR is the discount rate at which the Net Present Value (NPV) of the
        stream equals zero. This method uses a centroid-based Newton-Raphson algorithm:
        a time-weighted centroid of inflows and outflows provides a near-optimal initial
        guess, and Newton-Raphson iterations converge to the root using the analytical
        first derivative of NPV with respect to the discount rate.

        Parameters
        ----------
        convention : DayCountConvention, optional
            The day count convention for converting days to year fractions.
            Default is ``"actual/365"`` (standard economics convention).
        tol : float, optional
            Convergence tolerance on |NPV|. Iteration stops when ``|NPV(r)| < tol``.
            Default is ``1e-8``.
        max_iter : int, optional
            Maximum number of Newton-Raphson iterations before raising a convergence
            error. Default is ``100``.

        Returns
        -------
        float
            The annual IRR as a decimal (e.g., ``0.10`` for 10%).

        Raises
        ------
        ValueError
            If the stream contains no cash cashflows, or if all cashflows have the
            same sign (i.e., there are no inflows or no outflows), making it
            impossible for the NPV to equal zero at any finite rate.
        ValueError
            If the algorithm fails to converge within ``max_iter`` iterations, or
            if the derivative becomes effectively zero during iteration.

        Notes
        -----
        - Only cashflows with ``is_cash=True`` are included, consistent with ``npv()``.
        - The earliest cashflow date is used as the internal time reference. This
          does not affect the IRR value: shifting the reference date scales NPV by a
          non-zero constant ``(1+r)^Δt``, leaving the root unchanged.
        - **Centroid initial guess**: inflows and outflows are each collapsed to their
          time-weighted centroid dates ``t_in`` and ``t_out``, and the single-period
          approximation ``r₀ = (ΣCF_in / ΣCF_out)^(1/(t_in - t_out)) - 1`` is used
          as the starting rate. This typically places the initial guess within one or
          two Newton-Raphson steps of the solution for project cashflow profiles.
        - **Newton-Raphson derivative**: ``dNPV/dr = −Σ tᵢ·CFᵢ / (1+r)^(tᵢ+1)``,
          computed in a single pass by reusing the present-value term.
        - The rate is clamped to ``r > -1 + 1e-8`` at each step to prevent the
          algorithm from escaping the valid domain ``(-1, ∞)``.

        Examples
        --------
        >>> # Simple two-cashflow project: invest $1000, receive $1100 one year later
        >>> stream = CashFlowStream([
        ...     CashFlow(-1000.0, date(2024, 1, 1)),
        ...     CashFlow(1100.0, date(2025, 1, 1)),
        ... ])
        >>> stream.irr()   # approximately 0.10 (10%)

        >>> # Multi-period project
        >>> stream = CashFlowStream([
        ...     CashFlow(-50_000.0, date(2024, 1, 1)),
        ...     CashFlow(15_000.0, date(2025, 1, 1)),
        ...     CashFlow(20_000.0, date(2026, 1, 1)),
        ...     CashFlow(25_000.0, date(2027, 1, 1)),
        ... ])
        >>> irr = stream.irr()

        >>> # Verify that NPV equals zero at the IRR
        >>> assert abs(stream.npv(irr, date(2024, 1, 1))) < 1e-6
        """
        cash_only = self.cash_only()
        if not cash_only.inflows() or not cash_only.outflows():
            raise ValueError(
                "IRR requires both positive (inflow) and negative (outflow) cashflows."
            )

        ref_date = cash_only.min(key=lambda cf: cf.date).date
        rate = _irr_initial_guess(cash_only, ref_date, convention)

        for _ in range(max_iter):
            npv, dnpv = _irr_npv_and_dnpv(cash_only, rate, ref_date, convention)
            if abs(npv) < tol:
                return rate
            if abs(dnpv) < 1e-12:
                raise ValueError("IRR did not converge: zero derivative encountered.")
            rate -= npv / dnpv
            rate = max(rate, -1.0 + 1e-8)

        npv, _ = _irr_npv_and_dnpv(cash_only, rate, ref_date, convention)
        if abs(npv) < tol:
            return rate
        raise ValueError(f"IRR did not converge after {max_iter} iterations.")


# ---------------------------------------------------------------------------
# Module-level helpers for CashFlowStream.irr()
# ---------------------------------------------------------------------------


def _irr_initial_guess(
    cashflows: "CashFlowStream", ref_date: date, convention: DayCountConvention
) -> float:
    """
    Compute a centroid-based initial guess for Newton-Raphson IRR iteration.

    Treats inflows and outflows as two single lumps located at their respective
    time-weighted centroid dates.  Under this two-lump approximation the
    NPV = 0 condition reduces to a closed-form equation in ``r``:

        r₀ = (ΣCF_in / Σ|CF_out|) ^ (1 / (t_in - t_out)) - 1

    where ``t_in`` and ``t_out`` are the centroid times of the positive and
    negative cashflows respectively, measured in fractional years from
    ``ref_date``.  Falls back to ``0.1`` when the centroids coincide.
    """

    def _centroid(
        stream: "CashFlowStream", weight: Callable[[CashFlow], float]
    ) -> tuple[float, float]:
        total = 0.0
        weighted_t = 0.0
        for cf in stream:
            w = weight(cf)
            t = timedelta_fractional_years(ref_date, cf.date, convention)
            total += w
            weighted_t += w * t
        return total, weighted_t

    sum_in, weighted_t_in = _centroid(cashflows.inflows(), lambda cf: cf.amount)
    sum_out, weighted_t_out = _centroid(cashflows.outflows(), lambda cf: abs(cf.amount))

    dt = (weighted_t_in / sum_in) - (weighted_t_out / sum_out)
    if abs(dt) < 1e-10:
        return 0.1
    return (sum_in / sum_out) ** (1.0 / dt) - 1.0


def _irr_npv_and_dnpv(
    cashflows: "CashFlowStream", rate: float, ref_date: date, convention: DayCountConvention
) -> tuple[float, float]:
    """
    Compute NPV and its first derivative with respect to ``rate`` in a single pass.

    Reuses the present-value term to compute the derivative without a second
    exponentiation:

        dNPV/dr = −Σ tᵢ · CFᵢ / (1+r)^(tᵢ+1)
                = −Σ [tᵢ / (1+r)] · PVᵢ

    Returns
    -------
    tuple[float, float]
        ``(npv, dnpv)`` where ``npv = Σ CFᵢ/(1+r)^tᵢ`` and
        ``dnpv = dNPV/dr``.
    """
    npv = 0.0
    dnpv = 0.0
    one_plus_r = 1.0 + rate
    for cf in cashflows:
        t = timedelta_fractional_years(ref_date, cf.date, convention)
        pv = cf.amount / (one_plus_r**t)
        npv += pv
        dnpv -= t / one_plus_r * pv
    return npv, dnpv
