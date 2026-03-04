"""
Generation stream module for physical energy quantities.

Provides Generation (single data point), GenerationStream (container),
and GenerationGroup (grouped container) for modeling MWh production
from various sources and energy carriers.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    Literal,
    overload,
)

from dcaf.core.cashflows import (
    CashFlow,
    CashFlowStream,
    CashFlowTags,
)

from .types import DayCountConvention, Period
from .utils import (
    compound_factor,
    hours_per_period,
    period_start,
    time_delta_per_period,
    timedelta_fractional_years,
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


@dataclass
class GenerationGroup[KeyType]:
    """Dict-like container mapping keys to GenerationStream objects."""

    groups: dict[KeyType, "GenerationStream"]

    def aggregate[T](self, fn: Callable[["GenerationStream"], T]) -> dict[KeyType, T]:
        """Apply a function to each group and return a dict of results."""
        return {key: fn(stream) for key, stream in self.groups.items()}

    def keys(self) -> Iterable[KeyType]:
        return self.groups.keys()

    def values(self) -> Iterable["GenerationStream"]:
        return self.groups.values()

    def items(self) -> Iterable[tuple[KeyType, "GenerationStream"]]:
        return self.groups.items()

    def __getitem__(self, key: KeyType) -> "GenerationStream":
        return self.groups[key]

    def __len__(self) -> int:
        return len(self.groups)

    def __iter__(self) -> Iterator[KeyType]:
        return iter(self.groups)

    def sum(self) -> dict[KeyType, float]:
        """Return the sum of MWh for each group."""
        return {key: stream.sum() for key, stream in self.groups.items()}

    def count(self) -> dict[KeyType, int]:
        """Return the count of generation entries for each group."""
        return {key: stream.count() for key, stream in self.groups.items()}


@dataclass
class GenerationStream:
    """
    Container for Generation data points with a fluent API.

    Mirrors the CashFlowStream pattern for physical energy quantities.
    """

    entries: list[Generation] = field(default_factory=list)

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
    def from_streams(cls, *streams: "GenerationStream") -> "GenerationStream":
        """Combine multiple GenerationStreams into one."""
        all_entries: list[Generation] = []
        for s in streams:
            all_entries.extend(s.entries)
        return cls(all_entries)

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
        """Create generation from capacity and append to this stream."""
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
        return GenerationStream.from_streams(self, new)

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

        Multiple keyword arguments are combined with AND semantics.
        Passing *fn* together with any keyword argument raises ``ValueError``.

        Examples
        --------
        >>> stream.filter(lambda g: g.amount_mwh > 1000)
        >>> stream.filter(source="uprate")
        >>> stream.filter(source="unit_1", carrier="electricity")
        """
        kwargs_given = source is not None or carrier is not None
        if fn is not None and kwargs_given:
            raise ValueError(
                "Cannot pass both a predicate function and keyword arguments to filter()"
            )
        if fn is not None:
            return GenerationStream([e for e in self.entries if fn(e)])

        def _pred(g: Generation) -> bool:
            if source is not None and g.source != source:
                return False
            if carrier is not None and g.carrier != carrier:
                return False
            return True

        return GenerationStream([e for e in self.entries if _pred(e)])

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

        Exactly one of *fn* or a keyword argument must be provided; combining them
        raises ``ValueError``.  Calling with no arguments also raises ``ValueError``.

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
            groups: defaultdict[Any, list[Generation]] = defaultdict(list)
            for entry in self.entries:
                groups[fn(entry)].append(entry)
            return GenerationGroup({k: GenerationStream(v) for k, v in groups.items()})

        if source is True:
            src_groups: defaultdict[str, list[Generation]] = defaultdict(list)
            for entry in self.entries:
                src_groups[entry.source].append(entry)
            return GenerationGroup({k: GenerationStream(v) for k, v in src_groups.items()})

        if carrier is True:
            car_groups: defaultdict[str, list[Generation]] = defaultdict(list)
            for entry in self.entries:
                car_groups[entry.carrier].append(entry)
            return GenerationGroup({k: GenerationStream(v) for k, v in car_groups.items()})

        # period is set
        assert period is not None
        per_groups: defaultdict[date, list[Generation]] = defaultdict(list)
        for entry in self.entries:
            per_groups[period_start(entry.date, period)].append(entry)
        return GenerationGroup({k: GenerationStream(v) for k, v in per_groups.items()})

    def sum(self) -> float:
        """Return total MWh across all entries."""
        return sum((e.amount_mwh for e in self.entries), start=0.0)

    def count(self) -> int:
        """Return number of entries."""
        return len(self.entries)

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
        """
        total = 0.0
        for entry in self.entries:
            years = timedelta_fractional_years(valuation_date, entry.date, convention)
            total += entry.amount_mwh / compound_factor(rate, years)
        return total

    def to_revenue(
        self,
        price_per_mwh: float,
        escalation: float = 0.0,
        label: str = "Generation Revenue {n}",
        tags: frozenset[CashFlowTags] = frozenset({CashFlowTags.REVENUE, CashFlowTags.TAXABLE}),
    ) -> CashFlowStream:
        """
        Convert generation entries to revenue cashflows.

        Parameters
        ----------
        price_per_mwh : float
            Base price per MWh.
        escalation : float, optional
            Annual compound escalation rate for the price.
        label : str, optional
            Label template.
        tags : frozenset[CashFlowTags], optional
            Tags for the revenue flows.

        Returns
        -------
        CashFlowStream

        Examples
        --------
        >>> gen = GenerationStream.from_capacity(1000, 0.92, date(2025, 1, 1), 5)
        >>> revenue = gen.to_revenue(50.0, escalation=0.02)
        """
        if not self.entries:
            return CashFlowStream()
        base_year = min(e.date.year for e in self.entries)
        flows: list[CashFlow] = []
        for i, entry in enumerate(self.entries):
            years_elapsed = entry.date.year - base_year
            price = price_per_mwh * compound_factor(escalation, years_elapsed)
            flow_label = label.format(n=i + 1) if "{n}" in label else label
            flows.append(
                CashFlow(
                    amount=entry.amount_mwh * price,
                    date=entry.date,
                    label=flow_label,
                    is_cash=True,
                    tags=tags,
                )
            )
        return CashFlowStream(flows)

    def to_cost(
        self,
        rate_per_mwh: float,
        escalation: float = 0.0,
        label: str = "Variable Cost {n}",
        tags: frozenset[CashFlowTags] = frozenset(
            {CashFlowTags.EXPENSE, CashFlowTags.OPEX, CashFlowTags.TAX_DEDUCTIBLE}
        ),
    ) -> CashFlowStream:
        """
        Convert generation entries to variable cost cashflows (negative amounts).

        Parameters
        ----------
        rate_per_mwh : float
            Base cost rate per MWh (positive number; flows will be negative).
        escalation : float, optional
            Annual compound escalation rate.
        label : str, optional
            Label template.
        tags : frozenset[CashFlowTags], optional
            Tags for the cost flows.

        Returns
        -------
        CashFlowStream

        Examples
        --------
        >>> gen = GenerationStream.from_capacity(1000, 0.92, date(2025, 1, 1), 5)
        >>> costs = gen.to_cost(5.0, escalation=0.02)
        """
        if not self.entries:
            return CashFlowStream()
        base_year = min(e.date.year for e in self.entries)
        flows: list[CashFlow] = []
        for i, entry in enumerate(self.entries):
            years_elapsed = entry.date.year - base_year
            cost = rate_per_mwh * compound_factor(escalation, years_elapsed)
            flow_label = label.format(n=i + 1) if "{n}" in label else label
            flows.append(
                CashFlow(
                    amount=-entry.amount_mwh * cost,
                    date=entry.date,
                    label=flow_label,
                    is_cash=True,
                    tags=tags,
                )
            )
        return CashFlowStream(flows)

    def to_ptc(
        self,
        rate_per_mwh: float,
        years: int,
        escalation: float = 0.0,
        label: str = "PTC {n}",
        tags: frozenset[CashFlowTags] = frozenset({CashFlowTags.REVENUE}),
    ) -> CashFlowStream:
        """
        Convert generation entries to Production Tax Credit cashflows.

        Only entries within the first ``years`` years (from the earliest entry
        date) are included.

        Parameters
        ----------
        rate_per_mwh : float
            PTC rate per MWh.
        years : int
            Number of years of PTC eligibility.
        escalation : float, optional
            Annual escalation of the PTC rate.
        label : str, optional
            Label template.
        tags : frozenset[CashFlowTags], optional
            Tags for the PTC flows.

        Returns
        -------
        CashFlowStream

        Examples
        --------
        >>> gen = GenerationStream.from_capacity(1000, 0.92, date(2025, 1, 1), 20)
        >>> ptc = gen.to_ptc(27.5, years=10, escalation=0.02)
        """
        if not self.entries:
            return CashFlowStream()
        base_year = min(e.date.year for e in self.entries)
        cutoff_year = base_year + years
        flows: list[CashFlow] = []
        n = 0
        for entry in self.entries:
            if entry.date.year >= cutoff_year:
                continue
            n += 1
            years_elapsed = entry.date.year - base_year
            ptc_rate = rate_per_mwh * compound_factor(escalation, years_elapsed)
            flow_label = label.format(n=n) if "{n}" in label else label
            flows.append(
                CashFlow(
                    amount=entry.amount_mwh * ptc_rate,
                    date=entry.date,
                    label=flow_label,
                    is_cash=True,
                    tags=tags,
                )
            )
        return CashFlowStream(flows)
