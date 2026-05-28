"""Shared time and compounding utilities."""

from datetime import date, timedelta
from functools import cache
from typing import assert_never

from dateutil.relativedelta import relativedelta

from dcaf.shared.types import (
    DayCountConvention,
    Period,
    TimingConvention,
    _DayCountConventionEnum,
    _PeriodEnum,
    parse_day_count_convention,
    parse_period,
)


def _normalize_period(period: Period) -> _PeriodEnum:
    try:
        return parse_period(period)
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc


def _normalize_day_count_convention(convention: DayCountConvention) -> _DayCountConventionEnum:
    try:
        return parse_day_count_convention(str(convention))
    except ValueError as exc:
        raise AssertionError(str(exc)) from exc


def period_start(dt: date, period: Period) -> date:
    """Return the start date of the period containing *dt*.

    Examples
    --------
    >>> from datetime import date
    >>> period_start(date(2025, 5, 17), "quarter")
    datetime.date(2025, 4, 1)
    """
    normalized_period = _normalize_period(period)
    match normalized_period:
        case _PeriodEnum.DAY:
            return dt
        case _PeriodEnum.MONTH:
            return date(dt.year, dt.month, 1)
        case _PeriodEnum.QUARTER:
            quarter_month = ((dt.month - 1) // 3) * 3 + 1
            return date(dt.year, quarter_month, 1)
        case _PeriodEnum.YEAR:
            return date(dt.year, 1, 1)
        case _:
            assert_never(normalized_period)


def period_end(dt: date, period: Period) -> date:
    """Return the last date of the period containing *dt*.

    Examples
    --------
    >>> from datetime import date
    >>> period_end(date(2025, 5, 17), "quarter")
    datetime.date(2025, 6, 30)
    """
    match period:
        case "day":
            return dt
        case "month":
            next_month = date(dt.year, dt.month, 1) + relativedelta(months=1)
            return next_month - relativedelta(days=1)
        case "quarter":
            quarter_month = ((dt.month - 1) // 3) * 3 + 1
            quarter_start = date(dt.year, quarter_month, 1)
            return quarter_start + relativedelta(months=3) - relativedelta(days=1)
        case "year":
            return date(dt.year, 12, 31)
        case _:
            assert_never(period)


def event_date(
    dt: date,
    frequency: Period,
    timing: TimingConvention = "end",
    phase_start: date | None = None,
    phase_end: date | None = None,
) -> date:
    """Return the event booking date for a modeled period.

    Parameters
    ----------
    dt : date
        The period anchor date (typically the start of the modeled period window).
    frequency : Period
        The period granularity (e.g. ``"year"``, ``"month"``).
    timing : TimingConvention, optional
        ``"end"`` (default) books events at the end of the calendar period,
        capped by *phase_end*. ``"begin"`` books events at the start of the
        calendar period, floored by *phase_start*. ``"middle"`` books events
        at the midpoint between the effective begin and end dates, providing
        a better approximation for continuous spend or generation when
        discounting.
    phase_start : date or None, optional
        Earliest allowable event date (e.g. construction start or operations
        start). Used with ``"begin"`` and ``"middle"`` timing. When ``None``,
        the calendar period start is used without flooring.
    phase_end : date or None, optional
        Latest allowable event date (e.g. last day of construction or
        operations end). Used with ``"end"`` and ``"middle"`` timing. When
        ``None``, the calendar period end is used without capping.

    Returns
    -------
    date
        The computed event date.

    Examples
    --------
    >>> from datetime import date
    >>> event_date(
    ...     date(2025, 1, 1),
    ...     frequency="month",
    ...     timing="middle",
    ...     phase_start=date(2025, 1, 10),
    ...     phase_end=date(2025, 1, 25),
    ... )
    datetime.date(2025, 1, 17)
    """
    match timing:
        case "end":
            cal_end = period_end(dt, frequency)
            return min(cal_end, phase_end) if phase_end is not None else cal_end
        case "begin":
            cal_start = period_start(dt, frequency)
            return max(cal_start, phase_start) if phase_start is not None else cal_start
        case "middle":
            cal_start = period_start(dt, frequency)
            cal_end = period_end(dt, frequency)
            effective_start = max(cal_start, phase_start) if phase_start is not None else cal_start
            effective_end = min(cal_end, phase_end) if phase_end is not None else cal_end
            mid_days = (effective_end - effective_start).days // 2
            return effective_start + timedelta(days=mid_days)
        case _:
            assert_never(timing)


def timedelta_fractional_years(
    start: date, end: date, convention: DayCountConvention = "actual/actual"
) -> float:
    """Calculate the year fraction between two dates using the given day count convention.

    Examples
    --------
    >>> from datetime import date
    >>> round(timedelta_fractional_years(date(2025, 1, 1), date(2025, 7, 2)), 4)
    0.4986
    """
    normalized_convention = _normalize_day_count_convention(convention)
    match normalized_convention:
        case _DayCountConventionEnum.ACTUAL_365_NO_LEAP:
            return _days_excluding_leap_days(start, end) / 365.0
        case _DayCountConventionEnum.ACTUAL_365_FIXED:
            return (end - start).days / 365.0
        case _DayCountConventionEnum.ACTUAL_ACTUAL:
            return _actual_actual_fractional_years(start, end)
        case _:
            assert_never(normalized_convention)


def elapsed_hours(
    start: date,
    end: date,
    convention: DayCountConvention = "actual/actual",
) -> float:
    """Return elapsed physical hours under the supplied day-count convention.

    ``actual/365-no-leap`` uses no-leap elapsed days, excluding Feb. 29. The fixed and
    actual/actual conventions use calendar elapsed days.
    """
    normalized_convention = _normalize_day_count_convention(convention)
    match normalized_convention:
        case _DayCountConventionEnum.ACTUAL_365_NO_LEAP:
            return _days_excluding_leap_days(start, end) * 24.0
        case _DayCountConventionEnum.ACTUAL_365_FIXED | _DayCountConventionEnum.ACTUAL_ACTUAL:
            return (end - start).days * 24.0
        case _:
            assert_never(normalized_convention)


def _is_leap_year(year: int) -> bool:
    """Return whether *year* has a Feb. 29."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_year(year: int) -> int:
    """Return the number of days in *year*."""
    return 366 if _is_leap_year(year) else 365


def _days_excluding_leap_days(start: date, end: date) -> int:
    """Return calendar days in ``[start, end)`` with Feb. 29 removed."""
    if end < start:
        return -_days_excluding_leap_days(end, start)

    elapsed_days = (end - start).days
    leap_days = 0
    for year in range(start.year, end.year + 1):
        if not _is_leap_year(year):
            continue
        leap_day = date(year, 2, 29)
        if start <= leap_day < end:
            leap_days += 1
    return elapsed_days - leap_days


def _actual_actual_fractional_years(start: date, end: date) -> float:
    """Return ISDA-style actual/actual year fraction over ``[start, end)``."""
    if end < start:
        return -_actual_actual_fractional_years(end, start)
    if end == start:
        return 0.0

    total = 0.0
    current = start
    while current < end:
        next_year = date(current.year + 1, 1, 1)
        segment_end = min(end, next_year)
        total += (segment_end - current).days / _days_in_year(current.year)
        current = segment_end
    return total


def compound_factor(rate: float, periods: float) -> float:
    """Compute a compound growth or discount factor.

    Examples
    --------
    >>> round(compound_factor(0.08, 5), 4)
    1.4693
    """
    return (1.0 + rate) ** periods


def elapsed_months(start_date: date, end_date: date) -> float:
    """Return the fractional number of calendar months between two dates."""
    if end_date < start_date:
        return -elapsed_months(end_date, start_date)

    whole_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    anchor = start_date + relativedelta(months=whole_months)
    if anchor > end_date:
        whole_months -= 1
        anchor = start_date + relativedelta(months=whole_months)

    next_anchor = anchor + relativedelta(months=1)
    partial_month = (
        (end_date - anchor).days / (next_anchor - anchor).days if end_date > anchor else 0.0
    )
    return whole_months + partial_month


def elapsed_periods(
    start_date: date,
    end_date: date,
    period: Period,
    convention: DayCountConvention = "actual/actual",
) -> float:
    """Return the fractional number of periods between two dates.

    Examples
    --------
    >>> from datetime import date
    >>> elapsed_periods(date(2025, 1, 15), date(2025, 4, 15), "month")
    3.0
    """
    normalized_period = _normalize_period(period)
    match normalized_period:
        case _PeriodEnum.YEAR:
            return timedelta_fractional_years(start_date, end_date, convention)
        case _PeriodEnum.QUARTER:
            return elapsed_months(start_date, end_date) / 3.0
        case _PeriodEnum.MONTH:
            return elapsed_months(start_date, end_date)
        case _PeriodEnum.DAY:
            return float((end_date - start_date).days)
        case _:
            assert_never(normalized_period)


@cache
def hours_per_period(period: Period) -> float:
    """Return the number of hours in a period."""
    normalized_period = _normalize_period(period)
    match normalized_period:
        case _PeriodEnum.YEAR:
            return 8760.0
        case _PeriodEnum.QUARTER:
            return 8760.0 / 4
        case _PeriodEnum.MONTH:
            return 8760.0 / 12
        case _PeriodEnum.DAY:
            return 24.0
        case _:
            assert_never(normalized_period)


@cache
def time_delta_per_period(period: Period) -> relativedelta:
    """Return a relativedelta representing one period."""
    normalized_period = _normalize_period(period)
    match normalized_period:
        case _PeriodEnum.YEAR:
            return relativedelta(years=1)
        case _PeriodEnum.QUARTER:
            return relativedelta(months=3)
        case _PeriodEnum.MONTH:
            return relativedelta(months=1)
        case _PeriodEnum.DAY:
            return relativedelta(days=1)
        case _:
            assert_never(normalized_period)
