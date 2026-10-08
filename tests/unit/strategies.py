# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Shared Hypothesis strategies and independent calendar oracles for property tests.

The oracles deliberately reimplement calendar logic in the most direct way possible (day-by-day
counting, month arithmetic) rather than calling DCAF helpers, so properties checked against them
are not tautological. Month lengths come from the standard library's ``calendar.monthrange``.
"""

from calendar import monthrange
from datetime import date, timedelta

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


def is_month_end(day: date) -> bool:
    """Return whether *day* is the last day of its month."""
    return day.day == monthrange(day.year, day.month)[1]


def anchored_boundary(start: date, count: int, frequency: str, end_of_month: bool = True) -> date:
    """Return boundary *count* of a schedule anchored at *start*: the start's day number in the
    target month, clamped to shorter months, or the target month's last day when
    *end_of_month* is set and *start* is a month-end. Daily schedules ignore *end_of_month*."""
    # Daily frequency is the simple case since the day is our base unit of time
    if frequency == "day":
        return start + timedelta(days=count)
    # Month/quarter/year frequencies can have subtleties in anchoring due to handling of leap
    # years and the variable length of months.
    # Use number of months as base unit of time for month/quarter/year frequencies
    months = {"month": 1, "quarter": 3, "year": 12}[frequency] * count
    index = start.year * 12 + start.month - 1 + months
    year, month = index // 12, index % 12 + 1
    month_length = monthrange(year, month)[1]
    # start date is the last day of its month and `end_of_month=True` -> anchor to last day of month
    if end_of_month and is_month_end(start):
        return date(year, month, month_length)
    # otherwise keep the start's day number, clamped to shorter months
    return date(year, month, min(start.day, month_length))


@st.composite
def _late_month_dates(draw: st.DrawFn) -> date:
    """Draw a day from the 28th through the end of a month, where anchoring rules differ."""
    day = draw(DATES)
    return day.replace(day=draw(st.integers(28, monthrange(day.year, day.month)[1])))


# Schedule anchors over-weight late-month days, including Feb. 28 and Feb. 29 in leap,
# non-leap, and century years, since uniform dates rarely land on them.
ANCHOR_DATES = st.one_of(
    DATES,
    _late_month_dates(),
    st.sampled_from([date(2028, 2, 29), date(2000, 2, 29), date(2030, 2, 28), date(2100, 2, 28)]),
)


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
