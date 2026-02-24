"""
Docstring for dcaf.core.cashflows
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Literal,
    Optional,
    Protocol,
    TypeAlias,
)

Money: TypeAlias = Decimal
type Period = Literal["day", "month", "quarter", "year"]


class SupportsLessThan(Protocol):
    def __lt__(self, __other: Any) -> bool: ...


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


@dataclass(frozen=True)
class CashFlow:
    """
    Immutable representation of a single cashflow.

    Attributes
    ----------
    amount : Money
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

    amount: Money
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
        >>> cf = CashFlow(Decimal('1000'), date(2024, 1, 1), tags=frozenset({CashFlowTags.REVENUE}))
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
        >>> totals = grouped.aggregate(lambda s: sum(cf.amount for cf in s.flows))
        >>> # Returns: {date1: Decimal('1000'), date2: Decimal('2000'), ...}

        >>> # Get count of cashflows per year
        >>> by_year = stream.group_by(lambda cf: cf.date.year)
        >>> counts = by_year.aggregate(lambda s: len(s.flows))
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
        >>> by_tag = stream.group_by_tag()
        >>> scaled_groups = by_tag.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: CashFlow(
        ...         cf.amount * Decimal('1.1'), cf.date, cf.label, cf.is_cash, cf.tags
        ...     ))
        ... )

        >>> # Filter each group to only include cashflows after a certain date
        >>> by_year = stream.group_by_period("year")
        >>> recent_groups = by_year.apply_to_groups(
        ...     lambda s: s.filter(lambda cf: cf.date >= date(2024, 1, 1))
        ... )

        >>> # Sort each group by date
        >>> sorted_groups = by_year.apply_to_groups(lambda s: s.sort(lambda cf: cf.date))

        >>> # Apply transformation only to specific groups (sequence of keys)
        >>> by_tag = stream.group_by_tag()
        >>> scaled_revenue = by_tag.apply_to_groups(
        ...     lambda s: s.apply(lambda cf: CashFlow(
        ...         cf.amount * Decimal('1.05'), cf.date, cf.label, cf.is_cash, cf.tags
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
        >>> by_month = stream.group_by_period("month")
        >>> ungrouped = by_month.ungroup()
        >>> # ungrouped contains all original cashflows

        >>> # Filter groups, then ungroup
        >>> by_year = stream.group_by_period("year")
        >>> recent_years = CashFlowGroup({
        ...     year: flows
        ...     for year, flows in by_year.items()
        ...     if year.year >= 2024
        ... })
        >>> recent_flows = recent_years.ungroup()

        >>> # Apply transformations to groups, then ungroup
        >>> by_tag = stream.group_by_tag()
        >>> # Scale revenue by 1.05
        >>> revenue_scaled = by_tag[CashFlowTags.REVENUE].apply(
        ...     lambda cf: CashFlow(cf.amount * Decimal('1.05'), cf.date, cf.label, cf.is_cash, cf.tags)
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

    def sum(self) -> dict[KeyType, Money]:
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
        >>> by_tag = stream.group_by_tag()
        >>> totals = by_tag.sum()
        >>> # Returns: {CashFlowTags.REVENUE: Decimal('50000'),
        >>> #           CashFlowTags.EXPENSE: Decimal('-20000'), ...}

        >>> # Get monthly totals
        >>> by_month = stream.group_by_period("month")
        >>> monthly_totals = by_month.sum()
        >>> # Returns: {date(2024, 1, 1): Decimal('5000'),
        >>> #           date(2024, 2, 1): Decimal('6000'), ...}
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
        >>> by_tag = stream.group_by_tag()
        >>> counts = by_tag.count()
        >>> # Returns: {CashFlowTags.REVENUE: 15, CashFlowTags.EXPENSE: 42, ...}

        >>> # Get cashflows per year
        >>> by_year = stream.group_by_period("year")
        >>> yearly_counts = by_year.count()
        >>> # Returns: {date(2023, 1, 1): 24, date(2024, 1, 1): 36, ...}
        """
        return {key: stream.count() for key, stream in self.groups.items()}


@dataclass
class CashFlowStream:
    flows: list[CashFlow] | None = field(default_factory=list)

    @classmethod
    def from_recurring(
        cls,
        start: date,
        periods: int,
        amount: Money,
        frequency: Literal["annual", "monthly", "quarterly"] = "annual",
        escalation: Money = Decimal("0"),
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
            Number of periods (e.g., years if frequency='annual', months if
            frequency='monthly').
        amount : Money
            Base amount for the first period. Can be positive (inflows) or negative
            (outflows). Subsequent periods will be escalated if escalation > 0.
        frequency : Literal["annual", "monthly", "quarterly"], optional
            Frequency of the cashflows. Default is "annual".
            - "annual": One cashflow per year
            - "quarterly": Four cashflows per year (every 3 months)
            - "monthly": Twelve cashflows per year
        escalation : Money, optional
            Per-period compound escalation rate as a decimal (e.g., Decimal('0.025')
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

        Examples
        --------
        >>> # Annual revenue with 2.5% escalation for 20 years
        >>> revenue = CashFlowStream.from_recurring(
        ...     start=date(2028, 1, 1),
        ...     periods=20,
        ...     amount=Decimal('51_246_000'),
        ...     frequency='annual',
        ...     escalation=Decimal('0.025'),
        ...     label="Year {n} Revenue",
        ...     tags=frozenset({CashFlowTags.REVENUE, CashFlowTags.TAXABLE})
        ... )

        >>> # Monthly rent payments for 3 years (no escalation)
        >>> rent = CashFlowStream.from_recurring(
        ...     start=date(2025, 1, 1),
        ...     periods=36,
        ...     amount=Decimal('-5000'),
        ...     frequency='monthly',
        ...     label="Monthly Rent",
        ...     tags=frozenset({CashFlowTags.OPEX, CashFlowTags.TAX_DEDUCTIBLE})
        ... )

        >>> # Quarterly dividends with 3% annual growth (0.75% per quarter)
        >>> dividends = CashFlowStream.from_recurring(
        ...     start=date(2025, 3, 31),
        ...     periods=12,
        ...     amount=Decimal('25000'),
        ...     frequency='quarterly',
        ...     escalation=Decimal('0.0075'),
        ...     label="Q{n} Dividend"
        ... )

        Notes
        -----
        - Escalation is compounded, not simple interest
        - Date arithmetic handles month-end edge cases (e.g., Jan 31 + 1 month = Feb 28/29)
        - For annual escalation with monthly frequency, use (1 + annual_rate)^(1/12) - 1
        """
        flows = []
        for i in range(periods):
            # Calculate escalated amount using compound growth
            escalated_amount = amount * ((Decimal("1") + escalation) ** i)
            match frequency:
                case "annual":
                    flow_date = date(start.year + i, start.month, start.day)
                case "quarterly":
                    months_offset = i * 3
                    flow_date = start + relativedelta(months=months_offset)
                case "monthly":
                    flow_date = start + relativedelta(months=i)
                case _:
                    raise ValueError(
                        f"Unsupported frequency: {frequency}. "
                        f"Must be 'annual', 'monthly', or 'quarterly'."
                    )

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
        >>> initial_investment = CashFlow(Decimal('-1000000'), date(2024, 1, 1))
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
        ...     cf.amount * Decimal('0.9'), cf.date, cf.label, cf.is_cash, cf.tags
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

    def filter(self, fn: Callable[[CashFlow], bool]) -> "CashFlowStream":
        """
        Return a new CashFlowStream object filtered by a predicate function.

        Parameters
        ----------
        fn : Callable
            A predicate function that takes a CashFlow object and returns a boolean.
            Only cashflows for which the predicate returns True will be included in
            the resulting stream.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream object containing only the cashflows that satisfy
            the predicate.

        Examples
        --------
        >>> # Filter for positive cashflows only
        >>> positive_stream = stream.filter(lambda cf: cf.amount > 0)

        >>> # Filter for cashflows in a specific year
        >>> year_2024 = stream.filter(lambda cf: cf.date.year == 2024)

        >>> # Filter for cash-basis items only
        >>> cash_only = stream.filter(lambda cf: cf.is_cash)

        >>> # Filter by tag
        >>> revenue_only = stream.filter(lambda cf: cf.has_tag(CashFlowTags.REVENUE))
        >>> taxable = stream.filter(lambda cf: cf.has_tag(CashFlowTags.TAXABLE))
        """
        return CashFlowStream([flow for flow in self.flows if fn(flow)])

    def group_by[KeyType](self, fn: Callable[[CashFlow], KeyType]) -> CashFlowGroup[KeyType]:
        """
        Group cashflows by a key function.

        Applies the key function to each cashflow and groups all cashflows that
        return the same key value together.

        Parameters
        ----------
        fn : Callable
            A function that takes a CashFlow object and returns a hashable key value
            to group by (e.g., date, year, label, etc.).

        Returns
        -------
        CashFlowGroup
            A CashFlowGroup object mapping each unique key to a CashFlowStream of
            cashflows that share that key.

        Examples
        --------
        >>> # Group by exact date
        >>> by_date = stream.group_by(lambda cf: cf.date)

        >>> # Group by year
        >>> by_year = stream.group_by(lambda cf: cf.date.year)

        >>> # Group by positive/negative
        >>> by_sign = stream.group_by(lambda cf: "positive" if cf.amount > 0 else "negative")

        >>> # Then aggregate
        >>> yearly_totals = by_year.aggregate(lambda s: sum(cf.amount for cf in s.flows))
        """
        groups: defaultdict[KeyType, list[CashFlow]] = defaultdict(list)
        for flow in self.flows:
            key = fn(flow)
            groups[key].append(flow)

        return CashFlowGroup[KeyType]({key: CashFlowStream(flows) for key, flows in groups.items()})

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
        >>> revenue_total = sum(cf.amount for cf in by_tag[CashFlowTags.REVENUE].flows)
        >>> expense_total = sum(cf.amount for cf in by_tag[CashFlowTags.EXPENSE].flows)

        >>> # Get all taxable cashflows
        >>> taxable_flows = by_tag[CashFlowTags.TAXABLE]

        >>> # Count cashflows per tag
        >>> counts = by_tag.aggregate(lambda s: len(s.flows))
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
        >>> monthly_totals = by_month.aggregate(lambda s: sum(cf.amount for cf in s.flows))
        >>> # Returns: {date(2024, 1, 1): Decimal('5000'), date(2024, 2, 1): Decimal('6000'), ...}

        >>> # Group by year
        >>> by_year = stream.group_by_period("year")
        >>> yearly_revenue = by_year.aggregate(
        ...     lambda s: sum(cf.amount for cf in s.flows if cf.has_tag(CashFlowTags.REVENUE))
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

        >>> # Instead of:
        >>> by_month = stream.group_by(lambda cf: date(cf.date.year, cf.date.month, 1))
        >>> # You can do:
        >>> by_month = stream.group_by_period("month")
        """
        return self.group_by(lambda cf: self._get_period_start(cf.date, period))

    def sort(self, fn: Callable[[CashFlow], SupportsLessThan]) -> "CashFlowStream":
        """
        Return a new CashFlowStream with cashflows sorted by a key function.

        Parameters
        ----------
        fn : Callable[[CashFlow], SupportsLessThan]
            A function that takes a CashFlow object and returns a sortable key value
            (e.g., date, amount, label; more generally, any value which supports the
            `<` comparison operator). For descending order, use negative values
            or reverse the result.

        Returns
        -------
        CashFlowStream
            A new CashFlowStream with cashflows sorted by the key function in
            ascending order.

        Examples
        --------
        >>> # Sort by date (ascending) - most common use case
        >>> sorted_stream = stream.sort(lambda cf: cf.date)

        >>> # Sort by amount (descending) using negative
        >>> sorted_stream = stream.sort(lambda cf: -cf.amount)

        >>> # Sort by label alphabetically
        >>> sorted_stream = stream.sort(lambda cf: cf.label)

        >>> # Sort by multiple keys: year then amount
        >>> sorted_stream = stream.sort(lambda cf: (cf.date.year, cf.amount))

        >>> # Chain with other operations
        >>> recent_revenue = (stream
        ...     .filter(lambda cf: cf.has_tag(CashFlowTags.REVENUE))
        ...     .sort(lambda cf: cf.date)
        ...     .flows[-10:])  # Get 10 most recent revenue cashflows

        Notes
        -----
        This method returns a new CashFlowStream and does not modify the original.
        The sorting is stable, meaning that when multiple cashflows have the same
        key value, they maintain their original relative order.
        """
        ## NOTE: There is some conversation to be had about the flexibility of
        ## lambda functions, while also being cumbersome to simply sort by innate
        ## cashflow attributes like date. It would be easier to just do
        ## CFS.sort(by=date, ascending=False). We could do @overloads
        ## in the future to have an optional `by` and `key` parameter.
        return CashFlowStream(sorted(self.flows, key=fn))

    def sum(self) -> Money:
        """
        Return the sum of all cashflow amounts.

        Returns
        -------
        Money (Decimal)
            The sum of all cashflow amounts in the stream, including both
            positive (inflows) and negative (outflows) amounts.

        Examples
        --------
        >>> stream = CashFlowStream([
        ...     CashFlow(Decimal('1000'), date(2024, 1, 1)),
        ...     CashFlow(Decimal('-500'), date(2024, 2, 1)),
        ...     CashFlow(Decimal('2000'), date(2024, 3, 1))
        ... ])
        >>> total = stream.sum()
        >>> # Returns: Decimal('2500')

        >>> # Get total revenue
        >>> revenue_total = stream.filter(lambda cf: cf.has_tag(CashFlowTags.REVENUE)).sum()

        >>> # Get net cash flow (cash flows only)
        >>> net_cash = stream.filter(lambda cf: cf.is_cash).sum()

        Notes
        -----
        Returns Decimal('0') for empty streams.
        """
        ## NOTE: Need to think about this and handling nested CFS. Jacob says we
        ## never want nested CFS. If you want to do anything nested, use a CashFlowGroup.
        return sum((flow.amount for flow in self.flows), start=Decimal("0"))

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

    def npv(self, rate: float, valuation_date: date) -> Money:
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

        Returns
        -------
        Money (Decimal)
            The net present value of all cash cashflows in the stream, evaluated
            at the valuation date.

        Examples
        --------
        >>> # Calculate NPV at project start with 10% discount rate
        >>> stream = CashFlowStream([
        ...     CashFlow(Decimal('-10000'), date(2024, 1, 1)),  # Initial investment
        ...     CashFlow(Decimal('5000'), date(2024, 6, 1)),    # Return after 5 months
        ...     CashFlow(Decimal('6000'), date(2025, 1, 1))     # Return after 1 year
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
        - Time differences are calculated in days and converted to years using a
          365.25-day year convention.
        - The discount formula is: PV = CF / (1 + r)^t where t can be positive
          (future cashflows) or negative (past cashflows).
        - When t is negative (past cashflows), dividing by (1+r)^negative effectively
          compounds the value forward to the valuation date.
        - Returns Decimal('0') for empty streams or streams with no cash flows.
        """
        total = Decimal("0")
        rate_decimal = Decimal(str(rate))

        for flow in self.flows:
            # Only include actual cash flows
            if not flow.is_cash:
                continue

            # Calculate time difference in years (365.25-day convention)
            days_diff = (flow.date - valuation_date).days
            ## NOTE: MPR tool uses a 365.25 value - hardcoded for now.
            years = Decimal(days_diff) / Decimal("365.25")

            # Discount or compound the cashflow to the valuation date
            # Formula: PV = CF / (1 + r)^t
            # If t > 0 (future): discounts back to present
            # If t < 0 (past): compounds forward to present (dividing by (1+r)^negative)
            # If t = 0 (same date): no adjustment needed
            discount_factor = (Decimal("1") + rate_decimal) ** years
            present_value = flow.amount / discount_factor

            total += present_value

        return total

    @staticmethod
    def _get_period_start(dt: date, period: Period) -> date:
        match period:
            case "day":
                return dt
            case "month":
                return date(dt.year, dt.month, 1)
            case "quarter":
                quarter_month = ((dt.month - 1) // 3) * 3 + 1
                return date(dt.year, quarter_month, 1)
            case "year":
                return date(dt.year, 1, 1)
            case _:
                raise ValueError(f"Unknown period type: {period}")
