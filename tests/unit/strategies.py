# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Shared Hypothesis strategies for property tests.

Strategies only generate inputs. Expected calendar values come from hand calculations or from
properties of the DCAF helpers under test, never from reimplementations of those helpers.
"""

from calendar import monthrange
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

# Month-end schedule anchors: the last day of a uniformly drawn month, plus Feb. month-ends in
# leap, non-leap, and century years.
MONTH_END_DATES = st.one_of(
    DATES.map(lambda day: day.replace(day=monthrange(day.year, day.month)[1])),
    st.sampled_from([date(2028, 2, 29), date(2000, 2, 29), date(2030, 2, 28), date(2100, 2, 28)]),
)


@st.composite
def _dates_off_short_month_ends(draw: st.DrawFn) -> date:
    """Draw a day that is not the last day of a month shorter than 31 days, over-weighting the
    28th onward. The 31st of a 31-day month is included."""
    day = draw(DATES)
    month_length = monthrange(day.year, day.month)[1]
    last = month_length if month_length == 31 else month_length - 1
    return day.replace(day=draw(st.one_of(st.integers(1, last), st.integers(min(28, last), last))))


# Schedule anchors where the end-of-month rule has no effect, including Feb. 28 in a leap year
# and the 30th of a 31-day month.
DATES_OFF_SHORT_MONTH_ENDS = st.one_of(
    _dates_off_short_month_ends(),
    st.sampled_from([date(2028, 2, 28), date(2030, 1, 30), date(2030, 1, 31), date(2100, 2, 27)]),
)
