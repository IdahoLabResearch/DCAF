"""Shared escalation policies for date-based cost and price adjustment.

This module provides the reusable building blocks for escalation across DCAF.
Policies encapsulate how an amount known on a reference date should be adjusted
to another date. Simple constant-rate escalation and index-backed escalation are
both supported directly, and more complex piecewise strategies can be assembled
with :class:`EscalationBuilder`.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

from dcaf.types import DayCountConvention, Period
from dcaf.utils import compound_factor, elapsed_periods

type IndexPoint = tuple[date, float]
type IndexSeries = tuple[IndexPoint, ...]
type IndexInterpolation = Literal["step"]


class EscalationPolicy(Protocol):
    """Protocol for date-based escalation policies.

    Escalation policies convert a value known at ``reference_date`` into an
    equivalent value at another date by returning a multiplicative factor.
    This protocol defines the minimum interface required by the rest of DCAF
    when interacting with an escalation policy.

    Attributes
    ----------
    reference_date : date
        Date at which the policy's factor is defined as ``1.0``.

    Examples
    --------
    ``ConstantRateEscalation`` satisfies this protocol:

    >>> from datetime import date
    >>> policy = ConstantRateEscalation(date(2025, 1, 1), rate=0.02)
    >>> round(policy.factor(date(2026, 1, 1)), 4)
    1.02
    """

    @property
    def reference_date(self) -> date: ...

    def factor(self, target_date: date) -> float:
        """Return the factor that converts a reference-date amount to ``target_date``.

        Parameters
        ----------
        target_date : date
            Date to which the reference-date amount should be escalated.

        Returns
        -------
        float
            Multiplicative factor that converts an amount known at
            ``reference_date`` to its escalated value at ``target_date``.
        """
        ...

#===================================================================
# ESCALATION POLICY IMPLEMENTATIONS
#===================================================================

@dataclass(frozen=True)
class ConstantRateEscalation:
    """Escalation using a constant compound rate over a given period.

    Parameters
    ----------
    reference_date : date
        Date at which the escalation factor is ``1.0``.
    rate : float
        Compound escalation rate expressed as a decimal. For example, use
        ``0.025`` for 2.5%.
    period : {"day", "month", "quarter", "year"}, optional
        Compounding period associated with ``rate``. Default is ``"year"``.
    day_count_convention : {"actual/365"}, optional
        Day-count convention used when converting annual rates to fractional
        periods. Default is ``"actual/365"``.

    Notes
    -----
    - Annual escalation uses the supplied day-count convention.
    - Monthly and quarterly escalation use calendar-based elapsed periods so
      aligned dates such as January 1 to February 1 count as exactly one month.

    Examples
    --------
    Annual escalation:

    >>> from datetime import date
    >>> policy = ConstantRateEscalation(date(2025, 1, 1), rate=0.02)
    >>> round(policy.factor(date(2027, 1, 1)), 4)
    1.0404

    Monthly escalation:

    >>> monthly = ConstantRateEscalation(date(2025, 1, 1), rate=0.01, period="month")
    >>> round(monthly.factor(date(2025, 3, 1)), 4)
    1.0201
    """

    reference_date: date
    rate: float
    period: Period = "year"
    day_count_convention: DayCountConvention = "actual/365"

    def factor(self, target_date: date) -> float:
        """Return the escalation factor from ``reference_date`` to ``target_date``.

        Parameters
        ----------
        target_date : date
            Date to which the reference-date amount should be escalated.

        Returns
        -------
        float
            Compound escalation factor between ``reference_date`` and
            ``target_date``.
        """
        periods = elapsed_periods(
            self.reference_date,
            target_date,
            self.period,
            self.day_count_convention,
        )
        return compound_factor(self.rate, periods)


@dataclass(frozen=True)
class IndexSeriesEscalation:
    """Escalation based on dated index values.

    Parameters
    ----------
    reference_date : date
        Date at which the policy's factor is evaluated relative to the supplied
        index series.
    points : tuple[tuple[date, float], ...]
        Ordered index observations expressed as ``(date, value)`` pairs.
        Values must be strictly positive and dates must be strictly increasing.
    interpolation : {"step"}, optional
        Method used to evaluate the index between supplied observations.
        ``"step"`` uses the latest observed value on or before the requested
        date. Default is ``"step"``.

    Notes
    -----
    The current implementation intentionally supports step interpolation only.
    That is a good fit for many published inflation index series where values
    are observed periodically and held constant until the next observation.

    Examples
    --------
    >>> from datetime import date
    >>> policy = IndexSeriesEscalation(
    ...     reference_date=date(2020, 1, 1),
    ...     points=(
    ...         (date(2020, 1, 1), 100.0),
    ...         (date(2021, 1, 1), 103.0),
    ...         (date(2022, 1, 1), 106.09),
    ...     ),
    ... )
    >>> round(policy.factor(date(2021, 6, 1)), 4)
    1.03
    """

    reference_date: date
    points: IndexSeries
    interpolation: IndexInterpolation = "step"

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("IndexSeriesEscalation requires at least one data point")
        previous_date: date | None = None
        for point_date, value in self.points:
            if previous_date is not None and point_date <= previous_date:
                raise ValueError("Index series dates must be strictly increasing")
            if value <= 0.0:
                raise ValueError("Index series values must be positive")
            previous_date = point_date

        # Validate that the reference date is evaluable.
        self._value_on(self.reference_date)

    def factor(self, target_date: date) -> float:
        """Return the factor implied by the relative change in index values.

        Parameters
        ----------
        target_date : date
            Date to which the reference-date amount should be escalated.

        Returns
        -------
        float
            Ratio of the evaluated index value at ``target_date`` to the
            evaluated index value at ``reference_date``.
        """
        return self._value_on(target_date) / self._value_on(self.reference_date)

    def _value_on(self, target_date: date) -> float:
        """Return the index value applicable on ``target_date``."""
        if self.interpolation != "step":
            raise AssertionError(f"Unsupported interpolation '{self.interpolation}'")

        dates = [point_date for point_date, _ in self.points]
        index = bisect_right(dates, target_date) - 1
        if index < 0:
            raise ValueError(
                f"Date {target_date.isoformat()} is before the first index point "
                f"{dates[0].isoformat()}"
            )
        return self.points[index][1]


@dataclass(frozen=True)
class EscalationSegment:
    """One forward-valid segment within a composite escalation policy.

    Parameters
    ----------
    start_date : date
        Date on which this segment becomes active.
    policy : EscalationPolicy
        Policy used from ``start_date`` until the next segment begins, or
        indefinitely if there is no later segment.

    Notes
    -----
    ``policy.reference_date`` must match ``start_date`` so that segment factors
    chain cleanly inside :class:`CompositeEscalation`.
    """

    start_date: date
    policy: EscalationPolicy

    def __post_init__(self) -> None:
        if self.policy.reference_date != self.start_date:
            raise ValueError("Segment start_date must match policy.reference_date")


@dataclass(frozen=True)
class CompositeEscalation:
    """Piecewise escalation built from ordered policy segments.

    Parameters
    ----------
    reference_date : date
        Date at which the composite factor is ``1.0``.
    segments : tuple[EscalationSegment, ...]
        Ordered segments that define the active escalation policy over time.
        The first segment defines the earliest covered date. Each segment is
        active from its ``start_date`` until the next segment begins.

    Notes
    -----
    Composite policies are valid from the first segment start date onward.
    The composite ``reference_date`` may fall anywhere inside that covered
    window, allowing the policy to evaluate both earlier and later dates by
    normalizing each segment to the composite reference point.

    Examples
    --------
    >>> from datetime import date
    >>> historical = IndexSeriesEscalation(
    ...     reference_date=date(2020, 1, 1),
    ...     points=(
    ...         (date(2020, 1, 1), 100.0),
    ...         (date(2021, 1, 1), 103.0),
    ...         (date(2022, 1, 1), 106.09),
    ...     ),
    ... )
    >>> forward = ConstantRateEscalation(date(2022, 1, 1), rate=0.03)
    >>> policy = CompositeEscalation(
    ...     reference_date=date(2020, 1, 1),
    ...     segments=(
    ...         EscalationSegment(date(2020, 1, 1), historical),
    ...         EscalationSegment(date(2022, 1, 1), forward),
    ...     ),
    ... )
    >>> round(policy.factor(date(2024, 1, 1)), 4)
    1.1246
    """

    reference_date: date
    segments: tuple[EscalationSegment, ...]
    _segment_starts: tuple[date, ...] = field(init=False, repr=False)
    _segment_anchor_factors: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("CompositeEscalation requires at least one segment")

        previous_start: date | None = None
        for segment in self.segments:
            if previous_start is not None and segment.start_date <= previous_start:
                raise ValueError("Composite segments must be strictly increasing by start_date")
            previous_start = segment.start_date

        segment_starts = tuple(segment.start_date for segment in self.segments)
        object.__setattr__(self, "_segment_starts", segment_starts)

        if self.reference_date < segment_starts[0]:
            raise ValueError("reference_date must be on or after the first segment start_date")

        reference_index = self._segment_index(self.reference_date, segment_starts)
        anchor_factors = [0.0] * len(self.segments)

        # Within a segment, the factor from a to b is policy.factor(b) / policy.factor(a).
        anchor_factors[reference_index] = 1.0 / self.segments[reference_index].policy.factor(
            self.reference_date
        )

        for index in range(reference_index + 1, len(self.segments)):
            previous_segment = self.segments[index - 1]
            anchor_factors[index] = (
                anchor_factors[index - 1] * previous_segment.policy.factor(segment_starts[index])
            )

        for index in range(reference_index - 1, -1, -1):
            anchor_factors[index] = (
                anchor_factors[index + 1]
                / self.segments[index].policy.factor(segment_starts[index + 1])
            )

        object.__setattr__(self, "_segment_anchor_factors", tuple(anchor_factors))

    def factor(self, target_date: date) -> float:
        """Return the chained factor from ``reference_date`` to ``target_date``.

        Parameters
        ----------
        target_date : date
            Date within the composite's covered window.

        Returns
        -------
        float
            Multiplicative factor that converts a reference-date amount to
            ``target_date``.
        """
        target_index = self._segment_index(target_date)
        return self._segment_anchor_factors[target_index] * self.segments[target_index].policy.factor(
            target_date
        )

    def _segment_index(self, target_date: date, starts: tuple[date, ...] | None = None) -> int:
        """Return the index of the segment covering ``target_date``."""
        effective_starts = self._segment_starts if starts is None else starts
        index = bisect_right(effective_starts, target_date) - 1
        if index < 0:
            raise ValueError("CompositeEscalation does not support dates before the first segment")
        return index

#===================================================================
# BUILDER API FOR ADVANCED USERS
#===================================================================

@dataclass(frozen=True)
class EscalationBuilder:
    """Immutable builder for composing escalation policies.

    Parameters
    ----------
    reference_date : date
        Date at which the eventual built policy has a factor of ``1.0``.

    Notes
    -----
    The builder is immutable. Each method returns a new builder with the
    additional segment appended. This mirrors the fluent configuration style
    already used elsewhere in DCAF.
    The first configured segment defaults to ``reference_date`` but may start
    earlier when a composite policy needs to normalize historical dates to a
    later reference point.

    Examples
    --------
    >>> from datetime import date
    >>> policy = (
    ...     EscalationBuilder(date(2020, 1, 1))
    ...     .index_series(
    ...         (
    ...             (date(2020, 1, 1), 100.0),
    ...             (date(2021, 1, 1), 104.0),
    ...             (date(2022, 1, 1), 108.16),
    ...         )
    ...     )
    ...     .constant_rate(0.04, start_date=date(2022, 1, 1))
    ...     .build()
    ... )
    >>> round(policy.factor(date(2023, 1, 1)), 4)
    1.1249
    """

    reference_date: date
    _segments: tuple[EscalationSegment, ...] = ()

    def segment(self, policy: EscalationPolicy, *, start_date: date | None = None) -> "EscalationBuilder":
        """Append a policy segment to the builder.

        Parameters
        ----------
        policy : EscalationPolicy
            Policy to append.
        start_date : date, optional
            Date at which the supplied policy becomes active. For the first
            segment this defaults to ``reference_date`` and may be earlier when
            needed. For later segments it must be provided explicitly.

        Returns
        -------
        EscalationBuilder
            New builder with the segment appended.
        """
        segment_start = self._resolve_start_date(start_date)
        if policy.reference_date != segment_start:
            raise ValueError("Policy reference_date must match the segment start_date")
        return EscalationBuilder(
            reference_date=self.reference_date,
            _segments=self._segments + (EscalationSegment(segment_start, policy),),
        )

    def constant_rate(
        self,
        rate: float,
        *,
        period: Period = "year",
        start_date: date | None = None,
        day_count_convention: DayCountConvention = "actual/365",
    ) -> "EscalationBuilder":
        """Append a constant-rate escalation segment.

        Parameters
        ----------
        rate : float
            Compound escalation rate expressed as a decimal.
        period : {"day", "month", "quarter", "year"}, optional
            Compounding period associated with ``rate``. Default is ``"year"``.
        start_date : date, optional
            Date at which the segment becomes active. For the first segment this
            defaults to ``reference_date`` and may be earlier when needed. For
            later segments it must be supplied explicitly.
        day_count_convention : {"actual/365"}, optional
            Day-count convention used when ``period="year"``. Default is
            ``"actual/365"``.

        Returns
        -------
        EscalationBuilder
            New builder with the constant-rate segment appended.
        """
        segment_start = self._resolve_start_date(start_date)
        return self.segment(
            ConstantRateEscalation(
                reference_date=segment_start,
                rate=rate,
                period=period,
                day_count_convention=day_count_convention,
            ),
            start_date=segment_start,
        )

    def index_series(
        self,
        points: IndexSeries,
        *,
        start_date: date | None = None,
        interpolation: IndexInterpolation = "step",
    ) -> "EscalationBuilder":
        """Append an index-backed escalation segment.

        Parameters
        ----------
        points : tuple[tuple[date, float], ...]
            Ordered index observations expressed as ``(date, value)`` pairs.
        start_date : date, optional
            Date at which the segment becomes active. For the first segment this
            defaults to ``reference_date`` and may be earlier when needed. For
            later segments it must be supplied explicitly.
        interpolation : {"step"}, optional
            Interpolation method used between observations. Default is
            ``"step"``.

        Returns
        -------
        EscalationBuilder
            New builder with the index-backed segment appended.
        """
        segment_start = self._resolve_start_date(start_date)
        return self.segment(
            IndexSeriesEscalation(
                reference_date=segment_start,
                points=points,
                interpolation=interpolation,
            ),
            start_date=segment_start,
        )

    def build(self) -> EscalationPolicy:
        """Build the composed escalation policy.

        Returns
        -------
        EscalationPolicy
            The configured policy. A single configured segment is returned
            directly only when it already shares the builder's
            ``reference_date``; otherwise a :class:`CompositeEscalation` is
            returned.

        Raises
        ------
        ValueError
            If no segments have been configured.
        """
        if not self._segments:
            raise ValueError("EscalationBuilder requires at least one configured segment")
        if len(self._segments) == 1 and self._segments[0].start_date == self.reference_date:
            return self._segments[0].policy
        return CompositeEscalation(reference_date=self.reference_date, segments=self._segments)

    def _resolve_start_date(self, start_date: date | None) -> date:
        """Resolve and validate the effective start date for a new segment."""
        if not self._segments:
            if start_date is None:
                return self.reference_date
            if start_date > self.reference_date:
                raise ValueError("The first segment must start on or before reference_date")
            return start_date

        if start_date is None:
            raise ValueError("start_date is required for segments after the first")
        if start_date <= self._segments[-1].start_date:
            raise ValueError("Segment start_date must be after the prior segment start_date")
        return start_date
