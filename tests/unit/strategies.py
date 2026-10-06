# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Shared Hypothesis strategies and independent calendar oracles for property tests.

The oracles deliberately reimplement calendar logic in the most direct way possible (day-by-day
counting, month arithmetic) rather than calling DCAF helpers, so properties checked against them
are not tautological.
"""

from datetime import date, timedelta
from typing import Any

from hypothesis import strategies as st

from dcaf.shared.types import ProFormaCategory, TaxTreatment

DAY_COUNT_CONVENTIONS = st.sampled_from(["actual/actual", "actual/365-fixed", "actual/365-no-leap"])
FREQUENCIES = st.sampled_from(["day", "month", "quarter", "year"])
TIMINGS = st.sampled_from(["begin", "middle", "end"])

NON_FINITE = st.sampled_from([float("nan"), float("inf"), float("-inf")])
PRO_FORMA_CATEGORIES = st.one_of(st.none(), st.sampled_from(ProFormaCategory))
TAX_TREATMENTS = st.sampled_from(TaxTreatment)
LABELS = st.text(max_size=20)

# Dates span several leap years (including the 2100 non-leap century) without approaching
# date.min/date.max, where one-day periods and calendar windows cannot be formed.
DATES = st.dates(min_value=date(1990, 1, 1), max_value=date(2110, 12, 31))

# Bounded so float conservation checks can use tight tolerances.
FINITE_AMOUNTS = st.floats(min_value=-1e12, max_value=1e12, allow_nan=False)

# Stream entries draw dates from a narrow span crossing the 2028 leap day and several month,
# quarter, and year boundaries, so grouping, sorting, and range filters regularly see collisions.
STREAM_DATES = st.dates(min_value=date(2027, 11, 1), max_value=date(2029, 2, 28))
# Signed zeros are drawn explicitly because they are neither inflows nor outflows.
STREAM_AMOUNTS = st.one_of(st.sampled_from([0.0, -0.0, 1.0, -1.0]), FINITE_AMOUNTS)
STREAM_LABELS = st.sampled_from(["", "a", "b"])

# Inclusive bounds on the length in days of one nominal period starting on any date.
PERIOD_DAYS = {"day": (1, 1), "month": (28, 31), "quarter": (89, 92), "year": (365, 366)}


def pooled_lists(elements: st.SearchStrategy[Any], max_size: int = 8) -> st.SearchStrategy[list]:
    """Draw lists that repeat a few distinct elements, since duplicates must survive every
    stream operation."""
    return st.lists(elements, max_size=4).flatmap(
        lambda pool: st.lists(st.sampled_from(pool), max_size=max_size) if pool else st.just([])
    )


@st.composite
def intervals(draw: st.DrawFn, max_days: int = 1500) -> tuple[date, date]:
    """Draw a non-empty half-open ``[start, end)`` date interval."""
    start = draw(DATES)
    return start, start + timedelta(days=draw(st.integers(min_value=1, max_value=max_days)))


@st.composite
def enum_spellings(draw: st.DrawFn, value: str) -> str:
    """Draw a user-facing spelling of an enum value: any case, separators, and padding."""
    # each character can be either lowercase or uppercase
    chars = [draw(st.sampled_from([c.lower(), c.upper()])) for c in value]
    # underscores in enum variant can be given as underscores, spaces, or hyphens
    spelled = "".join(draw(st.sampled_from(["_", " ", "-"])) if c == "_" else c for c in chars)
    # leading and trailing padding with spaces or tabs
    pad = st.sampled_from(["", " ", "\t"])
    return draw(pad) + spelled + draw(pad)


def is_leap_day(day: date) -> bool:
    """Return whether *day* is Feb. 29."""
    return day.month == 2 and day.day == 29


def counted_days(start: date, end: date, day_count_convention: str) -> int:
    """Count the days in ``[start, end)`` that a day-count convention puts on the clock."""
    days = (start + timedelta(days=i) for i in range((end - start).days))
    if day_count_convention == "actual/365-no-leap":
        return sum(1 for day in days if not is_leap_day(day))
    return sum(1 for _ in days)


def year_fraction(start: date, end: date, day_count_convention: str) -> float:
    """Return the signed years from *start* to *end*, adding each day's share of a year: 1/365
    for counted days, or for actual/actual 1/365 or 1/366 by the length of the day's year."""
    if end < start:
        return -year_fraction(end, start, day_count_convention)
    if day_count_convention != "actual/actual":
        return counted_days(start, end, day_count_convention) / 365
    days = (start + timedelta(days=i) for i in range((end - start).days))
    return sum(1 / (date(day.year + 1, 1, 1) - date(day.year, 1, 1)).days for day in days)


def _calendar_period_start(day: date, frequency: str) -> date:
    """Return the first day of the calendar period containing *day*."""
    match frequency:
        case "day":
            return day
        case "month":
            return day.replace(day=1)
        case "quarter":
            return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
        case "year":
            return date(day.year, 1, 1)
    raise AssertionError(frequency)


def _next_calendar_period_start(period_start: date, frequency: str) -> date:
    """Return the first day of the calendar period after the one starting at *period_start*."""
    if frequency == "day":
        return period_start + timedelta(days=1)
    months = {"month": 1, "quarter": 3, "year": 12}[frequency]
    index = period_start.year * 12 + period_start.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def add_period(day: date, frequency: str) -> date:
    """Return the day one nominal period after *day*, clamping to the end of a shorter month."""
    if frequency == "day":
        return day + timedelta(days=1)
    months = {"month": 1, "quarter": 3, "year": 12}[frequency]
    index = day.year * 12 + day.month - 1 + months
    year, month = index // 12, index % 12 + 1
    month_length = (date(year + month // 12, month % 12 + 1, 1) - date(year, month, 1)).days
    return date(year, month, min(day.day, month_length))


def tail_days(tail_start: date, fraction: float, frequency: str) -> int:
    """Count the complete days in a fractional final period: the requested share of the next
    nominal period, with a sub-day remainder dropped."""
    requested = fraction * (add_period(tail_start, frequency) - tail_start).days
    nearest = round(requested)
    return nearest if abs(requested - nearest) <= 1e-12 else int(requested)


def calendar_period_key(day: date, frequency: str) -> date:
    """Identify the calendar settlement period containing *day* by its first day."""
    return _calendar_period_start(day, frequency)


def calendar_windows(start: date, end: date, frequency: str) -> list[tuple[date, date]]:
    """Split ``[start, end)`` into its overlaps with consecutive calendar periods."""
    windows = []
    period_start = _calendar_period_start(start, frequency)
    while period_start < end:
        period_end = _next_calendar_period_start(period_start, frequency)
        windows.append((max(start, period_start), min(end, period_end)))
        period_start = period_end
    return windows


def timing_point(start: date, end: date, timing: str) -> date:
    """Return the booking day for half-open ``[start, end)``: first, middle, or last day."""
    last = end - timedelta(days=1)
    match timing:
        case "begin":
            return start
        case "middle":
            return start + timedelta(days=(last - start).days // 2)
        case "end":
            return last
    raise AssertionError(timing)
