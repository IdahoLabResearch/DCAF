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
    Optional,
    TypeVar,
    cast,
    overload,
)

from dcaf.streams.base import BaseGroup, BaseStream
from dcaf.finance.escalation import (
    ConstantRateEscalation,
    EscalationPolicy,
    _resolve_escalation_policy_override,
)
from dcaf.shared.formatting import format_label
from dcaf.shared.time import period_windows
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
from dcaf.metrics.npv import npv as _npv
from dcaf.metrics.irr import irr as _irr

KeyType = TypeVar("KeyType")


class _UnsetType:
    """Sentinel type for optional filter arguments."""


_UNSET = _UnsetType()


def _recurring_escalation(
    *,
    start: date,
    escalation: float,
    escalation_period: Period,
    amount_reference_date: date | None,
    day_count_convention: DayCountConvention,
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
        day_count_convention=day_count_convention,
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
        >>> from dateutil.relativedelta import relativedelta
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
class CashFlowGroup(BaseGroup[KeyType, CashFlow, "CashFlowStream"]):
    """
    Dictionary-like container mapping group keys to ``CashFlowStream`` objects.

    Produced by :meth:`CashFlowStream.group_by` and related methods. Supports
    aggregation, selective group-wise transformation, group filtering, and
    flattening back to a single stream.

    Examples
    --------
    Build a stream, group by pro-forma category, and compute per-category totals:

    >>> from datetime import date
    >>> from dcaf.streams import CashFlow, CashFlowStream
    >>> from dcaf.shared.types import ProFormaCategory
    >>> revenue = CashFlowStream.from_recurring(
    ...     date(2026, 1, 1), 3, 1000.0,
    ...     pro_forma_category=ProFormaCategory.REVENUE,
    ... )
    >>> opex = CashFlowStream.from_recurring(
    ...     date(2026, 1, 1), 3, -200.0,
    ...     pro_forma_category=ProFormaCategory.OPERATING_COST,
    ... )
    >>> stream = CashFlowStream.from_streams(revenue, opex)
    >>> by_category = stream.group_by_pro_forma_category()
    >>> totals = by_category.sum()

    Scale only the revenue group and flatten back:

    >>> scaled = by_category.apply_to_groups(
    ...     lambda s: s.apply(lambda cf: cf.replace(amount=cf.amount * 1.05)),
    ...     keys=ProFormaCategory.REVENUE,
    ... )
    >>> combined = scaled.ungroup()
    >>> combined.count()
    6

    Iterate over groups:

    >>> for key, group_stream in by_category.items():
    ...     print(key, group_stream.count())  # doctest: +SKIP
    """

    def _empty_stream(self) -> "CashFlowStream":
        """Return an empty stream for internal regrouping helpers."""
        return CashFlowStream()


@dataclass
class CashFlowStream(BaseStream[CashFlow]):
    """
    Functional container for ``CashFlow`` entries.

    Preserves insertion order and supports sequence-style operations (iteration,
    indexing, slicing, ``len()``) alongside domain-specific helpers for building,
    filtering, transforming, grouping, and discounting cashflows. All
    mutating-style operations return a new ``CashFlowStream``; the original is
    never modified.

    Examples
    --------
    Build a project cashflow and compute NPV and IRR:

    >>> from datetime import date
    >>> from dcaf.streams import CashFlow, CashFlowStream
    >>> stream = CashFlowStream([
    ...     CashFlow(-10_000.0, date(2026, 1, 1)),
    ...     CashFlow(  4_000.0, date(2027, 1, 1)),
    ...     CashFlow(  4_000.0, date(2028, 1, 1)),
    ...     CashFlow(  5_000.0, date(2029, 1, 1)),
    ... ])
    >>> stream.npv(rate=0.10, valuation_date=date(2026, 1, 1)) > 0
    True
    >>> 0.0 < stream.irr() < 0.30
    True

    Combine recurring revenue with a one-off capital cost:

    >>> capex = CashFlow(-12_000.0, date(2026, 1, 1))
    >>> revenue = CashFlowStream.from_recurring(date(2027, 1, 1), 5, 3_000.0)
    >>> project = CashFlowStream.from_streams(capex, revenue)
    >>> project.count()
    6

    Filter, transform, and group:

    >>> by_year = project.sort().group_by(period="year")
    >>> yearly_totals = by_year.sum()

    Index and slice like a sequence:

    >>> project[0].amount
    -12000.0
    >>> project[1:3].count()
    2
    """

    def _amount(self, entry: CashFlow) -> float:
        """Return the numeric amount for internal shared helpers."""
        return entry.amount

    @classmethod
    def from_recurring(
        cls,
        start: date,
        periods: int | float,
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
        day_count_convention: DayCountConvention = "actual/actual",
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
        periods : int or float
            Number of periods (e.g., years if frequency='year', months if
            frequency='month'). Fractional periods include the final complete
            days that fit in the requested period count. If the requested end
            falls within a day, the incomplete day is omitted and a warning is
            raised.
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
        day_count_convention : DayCountConvention, optional
            Day-count convention used for annual escalation.
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
        escalation_policy = _recurring_escalation(
            start=start,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            day_count_convention=day_count_convention,
            escalation_policy=escalation_policy,
        )
        resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category,
            tax_treatment,
        )
        entries = []
        windows = period_windows(
            start,
            periods,
            frequency,
            day_count_convention,
            context="CashFlowStream.from_recurring periods",
        )
        for i, window in enumerate(windows, start=1):
            flow_date = window.start
            escalated_amount = amount * escalation_policy.factor(flow_date) * window.fraction
            flow_label = format_label(label, i)
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

    @overload
    def group_by(self, fn: Callable[[CashFlow], KeyType]) -> "CashFlowGroup[KeyType]": ...
    @overload
    def group_by(self, *, period: Period) -> "CashFlowGroup[date]": ...

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
            return CashFlowGroup(cast(dict[Any, CashFlowStream], self._grouped_streams(groups)))

        # period path
        assert period is not None
        period_groups = self._grouped_entries_by_period(period)
        return CashFlowGroup(
            cast(dict[date, CashFlowStream], self._grouped_streams(period_groups))
        )

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
        self,
        rate: float,
        valuation_date: date,
        convention: DayCountConvention = "actual/actual",
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
            Default is ``"actual/actual"``, which uses calendar elapsed days.

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
        Investment has positive NPV - accept project

        >>> # Compare NPV across different scenarios
        >>> conservative = stream.npv(0.15, date(2024, 1, 1))
        >>> optimistic = stream.npv(0.08, date(2024, 1, 1))

        Notes
        -----
        - Only cashflows with ``is_cash=True`` are included; non-cash items (e.g.,
          depreciation) do not represent actual cash movements.
        - Time differences are calculated in days and converted to years using the
          specified day count convention (default: actual/actual).
        - The discount formula is: PV = CF / (1 + r)^t where t can be positive
          (future cashflows) or negative (past cashflows).
        - When t is negative (past cashflows), dividing by (1+r)^negative effectively
          compounds the value forward to the valuation date.
        - Returns 0.0 for empty streams or streams with no cash flows.
        """
        values = ((flow.amount, flow.date) for flow in self.entries if flow.is_cash)
        return _npv(values, rate, valuation_date, convention)

    def irr(
        self,
        convention: DayCountConvention = "actual/actual",
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
            Default is ``"actual/actual"``, which uses calendar elapsed days.
        tol : float, optional
            Relative convergence tolerance. Iteration stops when
            ``|NPV(r)| < tol * Σ|CFᵢ|``, i.e. when the NPV residual is less
            than ``tol`` as a fraction of total absolute cashflow.
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
            if the derivative becomes effectively zero during iteration.  Numerical
            overflow while evaluating the Newton step is treated as non-convergence
            and raises ``ValueError`` as well.  If the Newton step falls below
            float64 precision before the tolerance is met, the best achievable
            rate is returned rather than raising.

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
        - The initial guess and each Newton step are clamped to
          ``r > -1 + 1e-8`` to prevent the algorithm from touching the
          singularity at ``r = -1``.

        Examples
        --------
        >>> # Simple two-cashflow project: invest $1000, receive $1100 one year later
        >>> stream = CashFlowStream([
        ...     CashFlow(-1000.0, date(2024, 1, 1)),
        ...     CashFlow(1100.0, date(2025, 1, 1)),
        ... ])
        >>> stream.irr()   # approximately 10% # doctest: +NUMBER
        0.100

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
        return _irr(self, convention, tol=tol, max_iter=max_iter)
