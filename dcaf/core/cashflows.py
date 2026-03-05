"""
Core cashflow abstractions for discounted cash-flow analysis.

Provides CashFlow (immutable data point), CashFlowStream (functional container),
CashFlowGroup (grouped container), and CashFlowTags (categorisation enum).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from operator import attrgetter
from enum import Enum
from typing import (
    Any,
    assert_never,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Literal,
    Optional,
    overload,
)

from .types import DayCountConvention, Period, SupportsLessThan
from .utils import compound_factor, period_start, time_delta_per_period, timedelta_fractional_years


class CashFlowTags(Enum):
    """
    Tags for categorizing cashflows in financial analysis and pro-forma statements.

    Used to filter and group cashflows by various characteristics such as income/expense
    classification, tax treatment, and accounting treatment.
    """

    # Income/Expense classification
    REVENUE = "revenue"
    EXPENSE = "expense"

    # Tax treatment
    TAXABLE = "taxable"
    TAX_DEDUCTIBLE = "tax_deductible"

    # Accounting treatment
    CAPEX = "capex"  # Capital expenditure
    OPEX = "opex"  # Operating expense
    DEPRECIATION = "depreciation"

    # Debt service
    DEBT_INTEREST = "debt_interest"
    DEBT_PRINCIPAL = "debt_principal"


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
    tags : frozenset[CashFlowTags]
        Set of tags for categorization (e.g., REVENUE, TAXABLE, EXPENSE).
    """

    amount: float
    date: date
    label: str = ""
    is_cash: bool = True
    tags: frozenset[CashFlowTags] = field(default_factory=frozenset)

    def has_tag(self, tag: CashFlowTags) -> bool:
        """
        Check if this cashflow has a specific tag.

        Parameters
        ----------
        tag : CashFlowTags
            The tag to check for.

        Returns
        -------
        bool
            True if the cashflow has the tag, False otherwise.

        Examples
        --------
        >>> cf = CashFlow(1000.0, date(2024, 1, 1), tags=frozenset({CashFlowTags.REVENUE}))
        >>> cf.has_tag(CashFlowTags.REVENUE)
        True
        >>> cf.has_tag(CashFlowTags.EXPENSE)
        False
        """
        return tag in self.tags

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
class CashFlowGroup[KeyType]:
    """
    A dictionary-like container for grouped CashFlows.

    Maps group keys to CashFlowStream objects, enabling aggregation and analysis
    across groups.
    """

    groups: dict[KeyType, "CashFlowStream"]

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
        return {key: fn(stream) for key, stream in self.groups.items()}

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
        >>> by_tag = stream.group_by(tag=True)
        >>> scaled_groups = by_tag.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: CashFlow(
        ...         cf.amount * 1.1, cf.date, cf.label, cf.is_cash, cf.tags
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
        >>> by_tag = stream.group_by(tag=True)
        >>> scaled_revenue = by_tag.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: CashFlow(
        ...         cf.amount * 1.05, cf.date, cf.label, cf.is_cash, cf.tags
        ...     )),
        ...     keys=[CashFlowTags.REVENUE]
        ... )

        >>> # Apply transformation to a single group
        >>> scaled_one_year = by_year.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: CashFlow(
        ...         cf.amount * 1.5, cf.date, cf.label, cf.is_cash, cf.tags
        ...     )),
        ...     keys=date(2024, 1, 1)
        ... )
        """
        if keys is None:
            # Apply to all groups
            transformed_keys = list(self.groups.keys())
        elif isinstance(keys, Collection) and not isinstance(keys, str):
            transformed_keys = list(keys)
            for key in transformed_keys:
                if key not in self.groups:
                    raise ValueError(
                        f"Unknown group key {key!r}. Known group keys: {list(self.groups.keys())}"
                    )
        elif keys in self.groups:
            # Single key (works for any hashable type, including strings)
            transformed_keys = [keys]
        else:
            raise ValueError(
                f"Could not interpret keys={keys!r} as valid grouping keys. "
                "Expected None, a single key, or a sequence of keys. "
                f"Valid keys: {list(self.groups.keys())}."
            )

        groups = {k: fn(cfs) if k in transformed_keys else cfs for k, cfs in self.groups.items()}
        return CashFlowGroup(groups)

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
        return CashFlowGroup({k: v for k, v in self.groups.items() if fn(k, v)})

    def ungroup(self) -> "CashFlowStream":
        """
        Ungroup the cashflows and return them as a single CashFlowStream.

        Combines all cashflows from all groups into a single stream. Note that
        cashflows may appear multiple times if they belonged to multiple groups
        (e.g., from group_by_tag where a flow can have multiple tags).

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
        >>> by_tag = stream.group_by(tag=True)
        >>> # Scale revenue by 1.05
        >>> revenue_scaled = by_tag[CashFlowTags.REVENUE].apply(
        ...     lambda cf: CashFlow(cf.amount * 1.05, cf.date, cf.label, cf.is_cash, cf.tags)
        ... )
        >>> # Put back into a stream
        >>> scaled_stream = CashFlowGroup({CashFlowTags.REVENUE: revenue_scaled}).ungroup()

        Notes
        -----
        - The resulting stream is not guaranteed to be in any particular order.
          Use `sort()` on the result if ordering is important.
        - Cashflows that appear in multiple groups (e.g., from group_by_tag)
          will appear multiple times in the ungrouped stream.
        """
        all_flows: list[CashFlow] = []

        for stream in self.groups.values():
            all_flows.extend(stream.flows)

        return CashFlowStream(all_flows)

    def keys(self) -> Iterable[KeyType]:
        """Return the group keys."""
        return self.groups.keys()

    def values(self) -> Iterable["CashFlowStream"]:
        """Return the CashFlowStream values."""
        return self.groups.values()

    def items(self) -> Iterable[tuple[KeyType, "CashFlowStream"]]:
        """Return key-value pairs of (key, CashFlowStream)."""
        return self.groups.items()

    def __getitem__(self, key: KeyType) -> "CashFlowStream":
        """Access a specific group by key."""
        return self.groups[key]

    def __len__(self) -> int:
        """Return the number of groups."""
        return len(self.groups)

    def __iter__(self) -> Iterator[KeyType]:
        """Iterate over group keys."""
        return iter(self.groups)

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
        >>> # Get total amount per tag
        >>> by_tag = stream.group_by(tag=True)
        >>> totals = by_tag.sum()
        >>> # Returns: {CashFlowTags.REVENUE: 50000.0,
        >>> #           CashFlowTags.EXPENSE: -20000.0, ...}

        >>> # Get monthly totals
        >>> by_month = stream.group_by(period="month")
        >>> monthly_totals = by_month.sum()
        >>> # Returns: {date(2024, 1, 1): 5000.0,
        >>> #           date(2024, 2, 1): 6000.0, ...}
        """
        return {key: stream.sum() for key, stream in self.groups.items()}

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
        >>> # Get count per tag
        >>> by_tag = stream.group_by(tag=True)
        >>> counts = by_tag.count()
        >>> # Returns: {CashFlowTags.REVENUE: 15, CashFlowTags.EXPENSE: 42, ...}

        >>> # Get cashflows per year
        >>> by_year = stream.group_by(period="year")
        >>> yearly_counts = by_year.count()
        >>> # Returns: {date(2023, 1, 1): 24, date(2024, 1, 1): 36, ...}
        """
        return {key: stream.count() for key, stream in self.groups.items()}


@dataclass
class CashFlowStream:
    flows: list[CashFlow] = field(default_factory=list)

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
        tags: frozenset[CashFlowTags] = frozenset(),
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
            Base amount for the first period. Can be positive (inflows) or negative
            (outflows). Subsequent periods will be escalated if escalation > 0.
        frequency : Period, optional
            Frequency of the cashflows. Default is "year".
            - "year": One cashflow per year
            - "quarter": Four cashflows per year (every 3 months)
            - "month": Twelve cashflows per year
            - "day": One cashflow per day
        escalation : float, optional
            Per-period compound escalation rate as a decimal (e.g., 0.025
            for 2.5% growth per period). Default is 0 (no escalation).
            Formula: amount_n = amount_0 * (1 + escalation)^n
        label : str, optional
            Label for the cashflows. Can include {n} placeholder for period number
            (1-indexed). Default is "Recurring Payment".
        is_cash : bool, optional
            Whether the cashflows represent actual cash movements. Default is True.
        tags : frozenset[CashFlowTags], optional
            Tags to apply to all cashflows. Default is empty set.

        Returns
        -------
        CashFlowStream
            A new stream containing the recurring cashflows.

        Notes
        -----
        - Escalation is compounded, not simple interest
        - Date arithmetic handles month-end edge cases (e.g., Jan 31 + 1 month = Feb 28/29)
        - For annual escalation with monthly frequency, use (1 + annual_rate)^(1/12) - 1
        """
        delta = time_delta_per_period(frequency)
        flows = []
        for i in range(periods):
            escalated_amount = amount * compound_factor(escalation, i)
            flow_date = start + delta * i
            flow_label = label.format(n=i + 1) if "{n}" in label else label
            flows.append(
                CashFlow(
                    amount=escalated_amount,
                    date=flow_date,
                    label=flow_label,
                    is_cash=is_cash,
                    tags=tags,
                )
            )

        return cls(flows)

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
        all_flows: list[CashFlow] = []

        for item in iterables:
            if isinstance(item, CashFlowStream):
                all_flows.extend(item.flows)
            elif isinstance(item, CashFlow):
                all_flows.append(item)
            else:
                # Assume it's an iterable of CashFlow objects
                all_flows.extend(item)

        return cls(all_flows)

    def append(self, flow: CashFlow) -> "CashFlowStream":
        """Return a new CashFlowStream with a single cashflow appended."""
        return CashFlowStream(self.flows + [flow])

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
        if isinstance(other, CashFlowStream):
            return CashFlowStream(self.flows + other.flows)
        return CashFlowStream(self.flows + list(other))

    def with_recurring(
        self,
        start: date,
        periods: int,
        amount: float,
        frequency: Period = "year",
        escalation: float = 0.0,
        label: str = "Recurring Payment",
        is_cash: bool = True,
        tags: frozenset[CashFlowTags] = frozenset(),
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
            label=label,
            is_cash=is_cash,
            tags=tags,
        )
        return CashFlowStream(self.flows + recurring.flows)

    def apply(self, fn: Callable[[CashFlow], CashFlow]) -> "CashFlowStream":
        """
        Apply a function to each cashflow within the CashFlowStream.

        Parameters
        ----------
        fn : Callable
            A callable that takes a CashFlow object and returns a modified CashFlow object.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream object with the function applied to each cashflow.

        Examples
        --------
        >>> def scale_amount(cf: CashFlow) -> CashFlow:
        ...     return CashFlow(cf.amount * 2, cf.date, cf.label, cf.is_cash, cf.tags)
        >>> stream = CashFlowStream([cf1, cf2])
        >>> scaled_stream = stream.apply(scale_amount)

        >>> # Apply discount factor to all amounts
        >>> discounted = stream.apply(lambda cf: CashFlow(
        ...     cf.amount * 0.9, cf.date, cf.label, cf.is_cash, cf.tags
        ... ))

        >>> # Add a tag to all cashflows
        >>> tagged = stream.apply(lambda cf: CashFlow(
        ...     cf.amount, cf.date, cf.label, cf.is_cash,
        ...     cf.tags | frozenset({CashFlowTags.REVENUE})
        ... ))

        Notes
        -----
        This method returns a new CashFlowStream and does not modify the original.
        For operations on the entire stream (not element-wise), use ``apply_streamwise()``.
        """
        return CashFlowStream([fn(flow) for flow in self.flows])

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
        ...     if not stream.flows:
        ...         return stream
        ...     first_amount = stream.flows[0].amount
        ...     return stream.apply(lambda cf: CashFlow(
        ...         cf.amount / first_amount, cf.date, cf.label, cf.is_cash, cf.tags))
        >>> stream = CashFlowStream([cf1, cf2, cf3])
        >>> normalized_stream = stream.apply_streamwise(normalize_to_first)

        >>> # Or using lambda for composition
        >>> result = stream.apply_streamwise(lambda s: s.filter(some_pred).apply(scale_fn))
        """
        ## NOTE: Depending on the passed in callable, this could modify self in
        ## place instead of creating new CFS object. Perhaps make self a (deep)copy?
        ## Keep an eye on this and how it gets used.
        return fn(self)

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
        result: list[CashFlow] = []
        for flow in self.flows:
            result.extend(fn(flow))
        return CashFlowStream(result)

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
        ...     if cf.has_tag(CashFlowTags.REVENUE):
        ...         return CashFlow(cf.amount * 2, cf.date, cf.label, cf.is_cash, cf.tags)
        ...     return None
        >>> revenue_doubled = stream.filter_apply(double_revenue)
        """
        result: list[CashFlow] = []
        for flow in self.flows:
            transformed = fn(flow)
            if transformed is not None:
                result.append(transformed)
        return CashFlowStream(result)

    def filter(
        self,
        fn: Callable[[CashFlow], bool] | None = None,
        *,
        tag: CashFlowTags | None = None,
        is_cash: bool | None = None,
    ) -> "CashFlowStream":
        """
        Return a new CashFlowStream object filtered by a predicate or keyword criteria.

        Accepts either a callable predicate **or** keyword arguments (``tag``,
        ``is_cash``), but not both. Multiple keyword arguments are combined with
        AND semantics.

        Parameters
        ----------
        fn : Callable, optional
            A predicate function that takes a CashFlow object and returns a boolean.
        tag : CashFlowTags, optional
            Keep only cashflows that have this tag.
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
        has_kwargs = tag is not None or is_cash is not None

        if fn is not None and has_kwargs:
            raise ValueError("Cannot combine a callable predicate with keyword arguments.")
        if fn is None and not has_kwargs:
            raise ValueError("Provide either a callable predicate or keyword arguments.")

        if fn is not None:
            return CashFlowStream([flow for flow in self.flows if fn(flow)])

        # Keyword-based filtering (AND semantics)
        result = self.flows
        if tag is not None:
            result = [flow for flow in result if flow.has_tag(tag)]
        if is_cash is not None:
            result = [flow for flow in result if flow.is_cash is is_cash]
        return CashFlowStream(result)

    def inflows(self) -> "CashFlowStream":
        """Return only cashflows with positive amounts."""
        return CashFlowStream([flow for flow in self.flows if flow.amount > 0])

    def outflows(self) -> "CashFlowStream":
        """Return only cashflows with negative amounts."""
        return CashFlowStream([flow for flow in self.flows if flow.amount < 0])

    def cash_only(self) -> "CashFlowStream":
        """Return only cash-basis cashflows (``is_cash=True``)."""
        return CashFlowStream([flow for flow in self.flows if flow.is_cash])

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
        result = self.flows
        if start is not None:
            result = [flow for flow in result if flow.date >= start]
        if end is not None:
            result = [flow for flow in result if flow.date <= end]
        return CashFlowStream(result)

    @overload
    def group_by[KeyType](self, fn: Callable[[CashFlow], KeyType]) -> CashFlowGroup[KeyType]: ...
    @overload
    def group_by(self, *, tag: Literal[True]) -> CashFlowGroup[CashFlowTags]: ...
    @overload
    def group_by(self, *, period: Period) -> CashFlowGroup[date]: ...

    def group_by(
        self,
        fn: Callable[[CashFlow], Any] | None = None,
        *,
        tag: bool = False,
        period: Period | None = None,
    ) -> CashFlowGroup:
        """
        Group cashflows by a key function, by tags, or by time period.

        Exactly one of *fn*, *tag*, or *period* must be provided.

        Parameters
        ----------
        fn : Callable, optional
            A key function applied to each cashflow.
        tag : bool, optional
            If ``True``, group by tags (fan-out: a flow with multiple tags
            appears in each corresponding group).
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
        provided = (fn is not None) + tag + (period is not None)
        if provided != 1:
            raise ValueError("Provide exactly one of 'fn', 'tag=True', or 'period'.")

        if fn is not None:
            groups: defaultdict[Any, list[CashFlow]] = defaultdict(list)
            for flow in self.flows:
                groups[fn(flow)].append(flow)
            return CashFlowGroup({k: CashFlowStream(v) for k, v in groups.items()})

        if tag:
            tag_groups: defaultdict[CashFlowTags, list[CashFlow]] = defaultdict(list)
            for flow in self.flows:
                for t in flow.tags:
                    tag_groups[t].append(flow)
            return CashFlowGroup({k: CashFlowStream(v) for k, v in tag_groups.items()})

        # period path
        assert period is not None
        period_groups: defaultdict[date, list[CashFlow]] = defaultdict(list)
        for flow in self.flows:
            period_groups[period_start(flow.date, period)].append(flow)
        return CashFlowGroup({k: CashFlowStream(v) for k, v in period_groups.items()})

    # Keep group_by_tag as alias for backwards compat during transition
    def group_by_tag(self) -> CashFlowGroup[CashFlowTags]:
        """
        Group cashflows by their tags.

        This is a convenience method that groups cashflows by each tag they contain.
        Since cashflows can have multiple tags, a single cashflow may appear in
        multiple groups. This is sugar for the more general ``group_by()`` method,
        handling the multi-tag case automatically.

        Returns
        -------
        CashFlowGroup
            A CashFlowGroup object mapping each CashFlowTags value to a
            CashFlowStream containing all cashflows with that tag.

        Examples
        --------
        >>> # Group by tags and get total per tag
        >>> by_tag = stream.group_by_tag()
        >>> revenue_total = by_tag[CashFlowTags.REVENUE].sum()
        >>> expense_total = by_tag[CashFlowTags.EXPENSE].sum()

        >>> # Get all taxable cashflows
        >>> taxable_flows = by_tag[CashFlowTags.TAXABLE]

        >>> # Count cashflows per tag
        >>> counts = by_tag.aggregate(lambda s: s.count())
        >>> # Returns: {CashFlowTags.REVENUE: 5, CashFlowTags.EXPENSE: 12, ...}

        Notes
        -----
        This method is equivalent to manually filtering by each tag, but more efficient:

        >>> # Instead of:
        >>> revenue = stream.filter(lambda cf: cf.has_tag(CashFlowTags.REVENUE))
        >>> expense = stream.filter(lambda cf: cf.has_tag(CashFlowTags.EXPENSE))
        >>> # You can do:
        >>> by_tag = stream.group_by_tag()
        >>> revenue = by_tag[CashFlowTags.REVENUE]
        >>> expense = by_tag[CashFlowTags.EXPENSE]
        """
        groups: defaultdict[CashFlowTags, list[CashFlow]] = defaultdict(list)
        for flow in self.flows:
            for tag in flow.tags:
                groups[tag].append(flow)

        return CashFlowGroup({key: CashFlowStream(flows) for key, flows in groups.items()})

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
        ...     lambda s: s.filter(tag=CashFlowTags.REVENUE).sum()
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
        return self.group_by(lambda cf: period_start(cf.date, period))

    @overload
    def sort(
        self, fn: Callable[[CashFlow], SupportsLessThan], *, descending: bool = ...
    ) -> "CashFlowStream": ...
    @overload
    def sort(
        self,
        *,
        attr: Literal["date", "amount", "label"],
        descending: bool = ...,
    ) -> "CashFlowStream": ...
    @overload
    def sort(self) -> "CashFlowStream": ...

    def sort(
        self,
        fn: Callable[[CashFlow], SupportsLessThan] | None = None,
        *,
        attr: Literal["date", "amount", "label"] | None = None,
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
        >>> recent_revenue = (stream
        ...     .filter(lambda cf: cf.has_tag(CashFlowTags.REVENUE))
        ...     .sort()
        ...     .flows[-10:])  # Get 10 most recent revenue cashflows

        Notes
        -----
        This method returns a new CashFlowStream and does not modify the original.
        The sorting is stable, meaning that when multiple cashflows have the same
        key value, they maintain their original relative order.
        """
        if fn is not None and attr is not None:
            raise ValueError("Cannot pass both a key function and 'attr' to sort()")

        if fn is not None:
            return CashFlowStream(sorted(self.flows, key=fn, reverse=descending))

        # Default to date when neither fn nor attr is provided
        resolved_attr = attr if attr is not None else "date"
        match resolved_attr:
            case "date" | "amount" | "label":
                key = attrgetter(resolved_attr)
            case _:
                assert_never(resolved_attr)
        return CashFlowStream(sorted(self.flows, key=key, reverse=descending))

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
        >>> revenue_total = stream.filter(lambda cf: cf.has_tag(CashFlowTags.REVENUE)).sum()

        >>> # Get net cash flow (cash flows only)
        >>> net_cash = stream.filter(lambda cf: cf.is_cash).sum()

        Notes
        -----
        Returns 0.0 for empty streams.
        """
        ## NOTE: Need to think about this and handling nested CFS. Jacob says we
        ## never want nested CFS. If you want to do anything nested, use a CashFlowGroup.
        return sum((flow.amount for flow in self.flows), start=0.0)

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
        >>> revenue_count = stream.filter(lambda cf: cf.has_tag(CashFlowTags.REVENUE)).count()

        >>> # Check if stream is empty
        >>> if stream.count() == 0:
        ...     print("No cashflows")
        """
        return len(self.flows)

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
        if not self.flows:
            raise ValueError("min() called on empty CashFlowStream")
        if key is None:
            return min(self.flows, key=lambda cf: cf.amount)
        return min(self.flows, key=key)

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
        if not self.flows:
            raise ValueError("max() called on empty CashFlowStream")
        if key is None:
            return max(self.flows, key=lambda cf: cf.amount)
        return max(self.flows, key=key)

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
        total = 0.0
        for flow in self.flows:
            if not flow.is_cash:
                continue
            years = timedelta_fractional_years(valuation_date, flow.date, convention)

            # Discount or compound the cashflow to the valuation date
            # Formula: PV = CF / (1 + r)^t
            # If t > 0 (future): discounts back to present
            # If t < 0 (past): compounds forward to present (dividing by (1+r)^negative)
            # If t = 0 (same date): no adjustment needed
            total += flow.amount / compound_factor(rate, years)

        return total
