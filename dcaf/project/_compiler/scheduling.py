# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Generic period-scheduling and event-date remapping machinery shared across domains."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace as dc_replace
from datetime import date

from dateutil.relativedelta import relativedelta

from dcaf.project._compiler.context import AnalysisContext
from dcaf.shared.time import (
    ScheduleTruncationWarning,
    elapsed_periods,
    event_date,
    period_windows,
    time_delta_per_period,
)
from dcaf.shared.types import Period, TimingConvention
from dcaf.streams.cashflows import CashFlowStream


def _inclusive_phase_end(phase_end: date | None) -> date | None:
    """Convert an exclusive phase-end boundary to the inclusive last-allowable date."""
    if phase_end is None:
        return None
    return phase_end - relativedelta(days=1)


@dataclass(frozen=True)
class ScheduledPeriod:
    """One modeled operating period with an optional partial-period fraction.

    ``event_date`` is the booking date for the period, computed from the timing
    convention and phase boundaries. It defaults to ``start`` when not provided.
    """

    start: date
    end: date
    event_date: date
    fraction: float = 1.0


def operating_schedule(
    context: AnalysisContext,
    section: str,
    *,
    start: date,
    periods: int | float | None,
    frequency: Period,
    timing: TimingConvention = "end",
    phase_start: date | None = None,
    phase_end: date | None = None,
) -> tuple[ScheduledPeriod, ...]:
    """Build the sequence of modeled operating periods for a section.

    When *periods* is specified, generates that many period entries and
    allows a final fractional period, truncated to complete days because
    DCAF events are stored as ``datetime.date`` values. Otherwise infers the
    schedule from ``timeline.operations_end``, prorating any trailing
    partial period using :func:`elapsed_periods`.

    *timing*, *phase_start*, and *phase_end* (all exclusive ends) control
    event-date placement. ``phase_end`` is converted to the inclusive
    last-allowable date when forwarded to :func:`event_date`.
    """
    if periods is not None:
        return _schedule_from_period_count(
            context,
            section,
            start=start,
            periods=periods,
            frequency=frequency,
            timing=timing,
            phase_start=phase_start,
            phase_end=phase_end,
        )
    return _schedule_from_operations_end(
        context,
        section,
        start=start,
        frequency=frequency,
        timing=timing,
        phase_start=phase_start,
        phase_end=phase_end,
    )


def _schedule_from_period_count(
    context: AnalysisContext,
    section: str,
    *,
    start: date,
    periods: int | float,
    frequency: Period,
    timing: TimingConvention,
    phase_start: date | None,
    phase_end: date | None,
) -> tuple[ScheduledPeriod, ...]:
    """Build a schedule of an explicit period count, capped to ``phase_end``."""
    if periods <= 0:
        raise ValueError(f"{section} periods must be positive")

    phase_end_inclusive = _inclusive_phase_end(phase_end)
    windows = period_windows(
        start,
        periods,
        frequency,
        context.config.day_count_convention,
        context=f"{section} periods",
    )
    if phase_end is not None and windows and windows[-1].end > phase_end:
        warn_configured_schedule_truncated(
            section=section,
            requested_end=windows[-1].end,
            boundary=phase_end,
        )
    schedule: list[ScheduledPeriod] = []
    for window in windows:
        if phase_end is not None and window.start >= phase_end:
            break
        effective_window_end = min(window.end, phase_end) if phase_end is not None else window.end
        fraction = (
            window.fraction
            if effective_window_end == window.end
            else elapsed_periods(
                window.start,
                effective_window_end,
                frequency,
                context.config.day_count_convention,
            )
        )
        window_end_inclusive = effective_window_end - relativedelta(days=1)
        effective_phase_end = (
            min(phase_end_inclusive, window_end_inclusive)
            if phase_end_inclusive is not None
            else window_end_inclusive
        )
        schedule.append(
            ScheduledPeriod(
                start=window.start,
                end=effective_window_end,
                event_date=event_date(
                    window.start,
                    frequency,
                    timing,
                    phase_start,
                    effective_phase_end,
                ),
                fraction=fraction,
            )
        )
    return tuple(schedule)


def _schedule_from_operations_end(
    context: AnalysisContext,
    section: str,
    *,
    start: date,
    frequency: Period,
    timing: TimingConvention,
    phase_start: date | None,
    phase_end: date | None,
) -> tuple[ScheduledPeriod, ...]:
    """Build a schedule inferred from ``timeline.operations_end``, with a trailing partial period."""
    exclusive_end = context.require_timeline_date("operations_end")
    if exclusive_end <= start:
        raise ValueError(f"timeline.operations_end must be after the {section} start")

    phase_end_inclusive = _inclusive_phase_end(phase_end)
    operations_end_inclusive = exclusive_end - relativedelta(days=1)
    effective_phase_end = (
        phase_end_inclusive if phase_end_inclusive is not None else operations_end_inclusive
    )

    delta = time_delta_per_period(frequency)
    current = start
    schedule = []
    while current < exclusive_end:
        window_end = min(current + delta, exclusive_end)
        schedule.append(
            ScheduledPeriod(
                start=current,
                end=window_end,
                event_date=event_date(current, frequency, timing, phase_start, effective_phase_end),
                fraction=elapsed_periods(
                    current,
                    window_end,
                    frequency,
                    context.config.day_count_convention,
                ),
            )
        )
        current += delta
    return tuple(schedule)


def remap_event_dates(
    context: AnalysisContext,
    stream: CashFlowStream,
    frequency: Period,
    phase_start: date | None,
    phase_end: date | None,
    *,
    truncate_after_phase_end: bool = False,
    component_name: str = "cashflow",
) -> CashFlowStream:
    """Remap cashflow dates according to the project timing convention.

    Applies the timeline's timing convention to each cashflow in *stream*,
    replacing each date with the computed event date. When
    ``truncate_after_phase_end`` is true, events are first remapped without
    capping to ``phase_end`` and then entries on or after the exclusive phase
    end are dropped with a warning.
    """
    timing = context.config.timeline.timing
    phase_end_inclusive = None if truncate_after_phase_end else _inclusive_phase_end(phase_end)
    remapped = stream.apply(
        lambda cf: dc_replace(
            cf,
            date=event_date(cf.date, frequency, timing, phase_start, phase_end_inclusive),
        )
    )
    if truncate_after_phase_end:
        return truncate_cashflow_schedule(
            remapped,
            boundary=phase_end,
            component_name=component_name,
        )
    return remapped


def truncate_cashflow_schedule(
    stream: CashFlowStream,
    *,
    boundary: date | None,
    component_name: str,
) -> CashFlowStream:
    """Drop scheduled cashflows on or after an exclusive analysis boundary."""
    if boundary is None:
        return stream
    dropped_dates = [cf.date for cf in stream.entries if cf.date >= boundary]
    if not dropped_dates:
        return stream
    warn_schedule_truncated(
        component_name=component_name,
        dropped_count=len(dropped_dates),
        first_dropped=min(dropped_dates),
        last_dropped=max(dropped_dates),
        boundary=boundary,
    )
    return stream.filter_apply(lambda cf: cf if cf.date < boundary else None)


def warn_schedule_truncated(
    *,
    component_name: str,
    dropped_count: int,
    first_dropped: date,
    last_dropped: date,
    boundary: date,
) -> None:
    """Warn that a configured schedule was truncated by ``operations_end``."""
    entry_word = "entry" if dropped_count == 1 else "entries"
    warnings.warn(
        (
            f"{component_name} schedule truncated at operations_end "
            f"{boundary.isoformat()}: dropped {dropped_count} cashflow "
            f"{entry_word} dated from {first_dropped.isoformat()} through "
            f"{last_dropped.isoformat()}. Analyses where operations_end "
            "falls before a configured schedule completes may be misspecified."
        ),
        ScheduleTruncationWarning,
        stacklevel=4,
    )


def warn_configured_schedule_truncated(
    *,
    section: str,
    requested_end: date,
    boundary: date,
) -> None:
    """Warn that an explicit period count exceeded ``operations_end``."""
    warnings.warn(
        (
            f"{section} schedule requested through {requested_end.isoformat()} "
            f"but operations_end is {boundary.isoformat()}; entries after "
            "operations_end were truncated. Analyses where operations_end "
            "falls before a configured schedule completes may be misspecified."
        ),
        ScheduleTruncationWarning,
        stacklevel=4,
    )
