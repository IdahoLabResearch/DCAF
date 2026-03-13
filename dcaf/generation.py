"""
Generation stream module for physical energy quantities.

Provides Generation (single data point), GenerationStream (container),
and GenerationGroup (grouped container) for modeling MWh production
from various sources and energy carriers.
"""

from dataclasses import dataclass
from datetime import date
from typing import (
    Any,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Literal,
    overload,
)

from dcaf._streams import BaseGroup, BaseStream
from dcaf.cashflows import (
    CashFlow,
    CashFlowStream,
    CashFlowTags,
)
from dcaf.escalation import (
    ConstantRateEscalation,
    EscalationPolicy,
    _constant_discount_policy,
    _resolve_escalation_policy_override,
)
from dcaf.types import DayCountConvention, Period, SupportsLessThan
from dcaf.utils import (
    hours_per_period,
    time_delta_per_period,
)


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
    reference_date = min(entry.date for entry in entries) if amount_reference_date is None else amount_reference_date
    return ConstantRateEscalation(
        reference_date=reference_date,
        rate=escalation,
        period=escalation_period,
    )


@dataclass
class GenerationGroup[KeyType](BaseGroup[KeyType, Generation, "GenerationStream"]):
    """
    Dictionary-like container mapping grouping keys to ``GenerationStream`` objects.

    The group container supports aggregation, selective group-wise transforms,
    filtering by group predicate, and flattening back to a single stream.
    """

    def _empty_stream(self) -> "GenerationStream":
        """Return an empty stream for internal regrouping helpers."""
        return GenerationStream()

    def aggregate[T](self, fn: Callable[["GenerationStream"], T]) -> dict[KeyType, T]:
        """
        Apply a function to each group and return the results.

        Parameters
        ----------
        fn : Callable[[GenerationStream], T]
            Function applied independently to each grouped stream.

        Returns
        -------
        dict[KeyType, T]
            Mapping of each group key to the transformed value.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> grouped.aggregate(lambda s: s.sum())
        {'unit_1': 1000.0}
        """
        return super().aggregate(fn)

    def apply_to_groups(
        self,
        fn: Callable[["GenerationStream"], "GenerationStream"],
        keys: KeyType | Collection[KeyType] | None = None,
    ) -> "GenerationGroup[KeyType]":
        """
        Apply a transformation to selected groups and return a new group object.

        Parameters
        ----------
        fn : Callable[[GenerationStream], GenerationStream]
            Transformation applied to each selected grouped stream.
        keys : KeyType or Collection[KeyType], optional
            Group key or keys to transform. If ``None``, transforms all groups.

        Returns
        -------
        GenerationGroup[KeyType]
            New grouped result with transformed streams for the selected keys.

        Raises
        ------
        ValueError
            If any provided key is not present in this grouped container.

        Examples
        --------
        >>> by_source = stream.group_by(source=True)
        >>> scaled = by_source.apply_to_groups(
        ...     lambda s: s.apply(lambda g: Generation(
        ...         amount_mwh=g.amount_mwh * 1.1,
        ...         date=g.date,
        ...         source=g.source,
        ...         carrier=g.carrier,
        ...         label=g.label,
        ...     ))
        ... )
        """
        return super().apply_to_groups(fn, keys=keys)

    def filter_groups(
        self, fn: Callable[[KeyType, "GenerationStream"], bool]
    ) -> "GenerationGroup[KeyType]":
        """
        Keep only groups matching a predicate.

        Parameters
        ----------
        fn : Callable[[KeyType, GenerationStream], bool]
            Predicate receiving each key and grouped stream.

        Returns
        -------
        GenerationGroup[KeyType]
            New grouped container containing only matching groups.

        Examples
        --------
        >>> by_source = stream.group_by(source=True)
        >>> large_groups = by_source.filter_groups(lambda key, s: s.sum() > 1_000.0)
        """
        return super().filter_groups(fn)

    def ungroup(self) -> "GenerationStream":
        """
        Flatten all groups into a single ``GenerationStream``.

        Returns
        -------
        GenerationStream
            Stream containing the entries from every grouped stream.

        Examples
        --------
        >>> by_source = stream.group_by(source=True)
        >>> ungrouped = by_source.ungroup()
        >>> ungrouped.count() == stream.count()
        True
        """
        return super().ungroup()

    def keys(self) -> Iterable[KeyType]:
        """
        Return the available group keys.

        Returns
        -------
        Iterable[KeyType]
            View over the keys in this grouped container.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> list(grouped.keys())
        ['unit_1']
        """
        return super().keys()

    def values(self) -> Iterable["GenerationStream"]:
        """
        Return the grouped streams.

        Returns
        -------
        Iterable[GenerationStream]
            View over the grouped ``GenerationStream`` values.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> [group.count() for group in grouped.values()]
        [1]
        """
        return super().values()

    def items(self) -> Iterable[tuple[KeyType, "GenerationStream"]]:
        """
        Return ``(key, stream)`` pairs.

        Returns
        -------
        Iterable[tuple[KeyType, GenerationStream]]
            View over each grouping key and its associated stream.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> [(key, group.count()) for key, group in grouped.items()]
        [('unit_1', 1)]
        """
        return super().items()

    def __getitem__(self, key: KeyType) -> "GenerationStream":
        """
        Return the stream associated with a grouping key.

        Parameters
        ----------
        key : KeyType
            Key identifying the group to retrieve.

        Returns
        -------
        GenerationStream
            Stream stored for the requested group key.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> grouped['unit_1'].count()
        1
        """
        return super().__getitem__(key)

    def __len__(self) -> int:
        """
        Return the number of groups.

        Returns
        -------
        int
            Count of group keys in the container.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> len(grouped)
        1
        """
        return super().__len__()

    def __iter__(self) -> Iterator[KeyType]:
        """
        Iterate over group keys.

        Returns
        -------
        Iterator[KeyType]
            Iterator yielding each grouping key once.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> list(grouped)
        ['unit_1']
        """
        return super().__iter__()

    def sum(self) -> dict[KeyType, float]:
        """
        Return total MWh for each group.

        Returns
        -------
        dict[KeyType, float]
            Mapping of each group key to the sum of ``amount_mwh``.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> grouped.sum()
        {'unit_1': 1000.0}
        """
        return super().sum()

    def count(self) -> dict[KeyType, int]:
        """
        Return the number of generation entries in each group.

        Returns
        -------
        dict[KeyType, int]
            Mapping of each group key to the number of grouped entries.

        Examples
        --------
        >>> grouped = stream.group_by(source=True)
        >>> grouped.count()
        {'unit_1': 1}
        """
        return super().count()


@dataclass
class GenerationStream(BaseStream[Generation]):
    """
    Container for Generation data points with a fluent API.

    Mirrors the CashFlowStream pattern for physical energy quantities and
    supports iteration, indexing, slicing, and ``len()``.
    """

    def _amount(self, entry: Generation) -> float:
        """Return the numeric amount for internal shared helpers."""
        return entry.amount_mwh

    @overload
    def __getitem__(self, index: int) -> Generation: ...

    @overload
    def __getitem__(self, index: slice) -> "GenerationStream": ...

    def __getitem__(self, index: int | slice) -> "Generation | GenerationStream":
        """
        Return a single generation entry or a sliced stream.

        Parameters
        ----------
        index : int or slice
            Integer position of a single generation entry, or a slice selecting
            a contiguous subset of the stream.

        Returns
        -------
        Generation or GenerationStream
            A single ``Generation`` when *index* is an integer, or a new
            ``GenerationStream`` containing the selected entries when *index* is
            a slice.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
        >>> stream[0].date
        datetime.date(2030, 1, 1)
        >>> stream[1:].count()
        2
        """
        return super().__getitem__(index)

    def __iter__(self) -> Iterator[Generation]:
        """
        Iterate over generation entries in insertion order.

        Returns
        -------
        Iterator[Generation]
            Iterator yielding each generation entry in the stream.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2)
        >>> [entry.date.year for entry in stream]
        [2030, 2031]
        """
        return super().__iter__()

    def __len__(self) -> int:
        """
        Return the number of generation entries in the stream.

        Returns
        -------
        int
            Number of generation entries stored in the stream.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 4)
        >>> len(stream)
        4
        """
        return super().__len__()

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
        label: str = "Generation {n}",
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
            Label template.

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
            gen_label = label.format(n=i + 1) if "{n}" in label else label
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

    def append(self, entry: Generation) -> "GenerationStream":
        """
        Return a new stream with one generation entry appended.

        Parameters
        ----------
        entry : Generation
            Entry to append.

        Returns
        -------
        GenerationStream
            New stream containing the original entries plus *entry*.

        Examples
        --------
        >>> stream = GenerationStream()
        >>> updated = stream.append(Generation(100.0, date(2030, 1, 1)))
        >>> updated.count()
        1
        """
        return super().append(entry)

    def extend(self, other: "GenerationStream | Iterable[Generation]") -> "GenerationStream":
        """
        Return a new stream with additional generation entries appended.

        Parameters
        ----------
        other : GenerationStream or Iterable[Generation]
            Additional entries to append.

        Returns
        -------
        GenerationStream
            New stream containing the original and appended entries.

        Examples
        --------
        >>> base = GenerationStream([Generation(100.0, date(2030, 1, 1))])
        >>> extra = [Generation(200.0, date(2031, 1, 1))]
        >>> base.extend(extra).count()
        2
        """
        return super().extend(other)

    def apply(self, fn: Callable[[Generation], Generation]) -> "GenerationStream":
        """
        Apply a transformation to each generation entry.

        Parameters
        ----------
        fn : Callable[[Generation], Generation]
            One-to-one transformation function applied to every entry.

        Returns
        -------
        GenerationStream
            New stream containing the transformed entries.

        Examples
        --------
        >>> stream = GenerationStream([Generation(100.0, date(2030, 1, 1))])
        >>> doubled = stream.apply(
        ...     lambda g: Generation(g.amount_mwh * 2, g.date, g.source, g.carrier, g.label)
        ... )
        >>> doubled[0].amount_mwh
        200.0
        """
        return super().apply(fn)

    def apply_streamwise(
        self, fn: Callable[["GenerationStream"], "GenerationStream"]
    ) -> "GenerationStream":
        """
        Apply a transformation to the entire stream at once.

        Parameters
        ----------
        fn : Callable[[GenerationStream], GenerationStream]
            Transformation receiving the whole stream and returning a new stream.

        Returns
        -------
        GenerationStream
            Result returned by *fn*.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
        >>> trimmed = stream.apply_streamwise(lambda s: s[1:])
        >>> trimmed.count()
        2
        """
        return super().apply_streamwise(fn)

    def flat_apply(self, fn: Callable[[Generation], Iterable[Generation]]) -> "GenerationStream":
        """
        Flat-map generation entries to zero or more output entries.

        Parameters
        ----------
        fn : Callable[[Generation], Iterable[Generation]]
            Mapping function that can emit multiple entries per input entry.

        Returns
        -------
        GenerationStream
            New stream containing all emitted entries.

        Examples
        --------
        >>> stream = GenerationStream([Generation(100.0, date(2030, 1, 1))])
        >>> duplicated = stream.flat_apply(lambda g: [g, g])
        >>> duplicated.count()
        2
        """
        return super().flat_apply(fn)

    def filter_apply(
        self, fn: Callable[[Generation], Generation | None]
    ) -> "GenerationStream":
        """
        Transform entries while dropping ``None`` results.

        Parameters
        ----------
        fn : Callable[[Generation], Generation | None]
            Mapping function returning either a replacement entry or ``None``.

        Returns
        -------
        GenerationStream
            New stream containing only the non-``None`` results.

        Examples
        --------
        >>> stream = GenerationStream([Generation(100.0, date(2030, 1, 1))])
        >>> kept = stream.filter_apply(lambda g: g if g.amount_mwh > 0 else None)
        >>> kept.count()
        1
        """
        return super().filter_apply(fn)

    def with_capacity(
        self,
        capacity_mw: float,
        capacity_factor: float,
        start: date,
        periods: int,
        frequency: Period = "year",
        source: str = "",
        carrier: str = "electricity",
        label: str = "Generation {n}",
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
            Label template for the appended entries.

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
        >>> stream.filter(source="uprate")
        >>> stream.filter(source="unit_1", carrier="electricity")

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
    def group_by[KeyType](
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
        >>> stream.group_by(source=True)
        >>> stream.group_by(carrier=True)
        >>> stream.group_by(period="month")
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
            src_groups = self._grouped_entries_by_attr("source")
            return GenerationGroup(self._grouped_streams(src_groups))

        if carrier is True:
            car_groups = self._grouped_entries_by_attr("carrier")
            return GenerationGroup(self._grouped_streams(car_groups))

        # period is set
        assert period is not None
        per_groups = self._grouped_entries_by_period(period)
        return GenerationGroup(self._grouped_streams(per_groups))

    def date_range(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> "GenerationStream":
        """
        Filter generation entries by inclusive date bounds.

        Parameters
        ----------
        start : date, optional
            Earliest date to include. If ``None``, no lower bound is applied.
        end : date, optional
            Latest date to include. If ``None``, no upper bound is applied.

        Returns
        -------
        GenerationStream
            New stream containing only entries within the selected date range.

        Examples
        --------
        >>> stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
        >>> stream.date_range(start=date(2031, 1, 1)).count()
        2
        """
        return super().date_range(start=start, end=end)

    @overload
    def sort(
        self, fn: Callable[[Generation], SupportsLessThan], *, descending: bool = ...
    ) -> "GenerationStream": ...
    @overload
    def sort(
        self,
        *,
        attr: Literal["date", "amount_mwh", "source", "carrier", "label"],
        descending: bool = ...,
    ) -> "GenerationStream": ...
    @overload
    def sort(self) -> "GenerationStream": ...

    def sort(
        self,
        fn: Callable[[Generation], SupportsLessThan] | None = None,
        *,
        attr: Literal["date", "amount_mwh", "source", "carrier", "label"] | None = None,
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
        if attr not in (None, "date", "amount_mwh", "source", "carrier", "label"):
            raise AssertionError(f"Unexpected sort attribute: {attr!r}")
        return super().sort(fn, attr=attr, descending=descending)

    def sum(self) -> float:
        """
        Return total MWh across all entries.

        Returns
        -------
        float
            Sum of ``amount_mwh`` across the stream.

        Examples
        --------
        >>> stream = GenerationStream([Generation(100.0, date(2030, 1, 1))])
        >>> stream.sum()
        100.0
        """
        return super().sum()

    def count(self) -> int:
        """
        Return the number of generation entries in the stream.

        Returns
        -------
        int
            Number of stored generation entries.

        Examples
        --------
        >>> stream = GenerationStream([Generation(100.0, date(2030, 1, 1))])
        >>> stream.count()
        1
        """
        return super().count()

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
        discount_policy = _constant_discount_policy(
            valuation_date=valuation_date,
            rate=rate,
            convention=convention,
        )
        total = 0.0
        for entry in self.entries:
            total += entry.amount_mwh / discount_policy.factor(entry.date)
        return total

    def to_revenue(
        self,
        price_per_mwh: float,
        escalation: float = 0.0,
        label: str = "Generation Revenue {n}",
        tags: frozenset[CashFlowTags] = frozenset({CashFlowTags.REVENUE, CashFlowTags.TAXABLE}),
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
            Label template.
        tags : frozenset[CashFlowTags], optional
            Tags for the revenue flows.

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
        entries: list[CashFlow] = []
        for i, entry in enumerate(self.entries):
            price = price_per_mwh * escalation_policy.factor(entry.date)
            flow_label = label.format(n=i + 1) if "{n}" in label else label
            entries.append(
                CashFlow(
                    amount=entry.amount_mwh * price,
                    date=entry.date,
                    label=flow_label,
                    is_cash=True,
                    tags=tags,
                )
            )
        return CashFlowStream(entries)

    def to_cost(
        self,
        rate_per_mwh: float,
        escalation: float = 0.0,
        label: str = "Variable Cost {n}",
        tags: frozenset[CashFlowTags] = frozenset(
            {CashFlowTags.EXPENSE, CashFlowTags.OPEX, CashFlowTags.TAX_DEDUCTIBLE}
        ),
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
            Label template.
        tags : frozenset[CashFlowTags], optional
            Tags for the cost flows.

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
        entries: list[CashFlow] = []
        for i, entry in enumerate(self.entries):
            cost = rate_per_mwh * escalation_policy.factor(entry.date)
            flow_label = label.format(n=i + 1) if "{n}" in label else label
            entries.append(
                CashFlow(
                    amount=-entry.amount_mwh * cost,
                    date=entry.date,
                    label=flow_label,
                    is_cash=True,
                    tags=tags,
                )
            )
        return CashFlowStream(entries)
