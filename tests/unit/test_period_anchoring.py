# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for anchored schedule boundaries and the end-of-month rule.

Boundary *k* of a schedule is ``start + k * period``, computed from the schedule start rather than
from the previous boundary. When ``end_of_month`` is set and the start is the last day of its
month, every month, quarter, or year boundary is the last day of its month. Elapsed months and
quarters are measured against the same boundaries, so every full scheduled period is exactly one
period long.
"""

from datetime import date, timedelta
import warnings

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from dcaf.shared.time import add_periods, elapsed_periods, period_windows
from strategies import (
    ANCHOR_DATES,
    DAY_COUNT_CONVENTIONS,
    FREQUENCIES,
    anchored_boundary,
    is_month_end,
)

END_OF_MONTH = st.booleans()
MONTHLY_FREQUENCIES = st.sampled_from(["month", "quarter"])
# Measurement on whole anchored boundaries: daily, monthly, and quarterly periods count boundaries,
# while years are measured by the day-count convention and so are excluded.
COUNTED_FREQUENCIES = st.sampled_from(["day", "month", "quarter"])


def _elapsed_oracle(start: date, end: date, frequency: str, end_of_month: bool) -> float:
    """Count whole anchored periods from *start* to *end*, plus the elapsed share of the days in
    the anchored period containing *end*."""
    k = 0
    while anchored_boundary(start, k + 1, frequency, end_of_month) <= end:
        k += 1
    lower = anchored_boundary(start, k, frequency, end_of_month)
    upper = anchored_boundary(start, k + 1, frequency, end_of_month)
    return k + (end - lower).days / (upper - lower).days


# === Boundary dates: reference schedules ===

_REFERENCE_SCHEDULES = [
    # tuples of (start, frequency, end_of_month=False boundaries, end_of_month=True boundaries)
    # A 31st start keeps its day number, clamped in shorter months, under either rule.
    (
        date(2030, 1, 31),
        "month",
        [date(2030, 2, 28), date(2030, 3, 31), date(2030, 4, 30)],
        [date(2030, 2, 28), date(2030, 3, 31), date(2030, 4, 30)],
    ),
    # The 30th of a 31-day month is not a month-end, so it keeps its day number.
    (
        date(2030, 1, 30),
        "month",
        [date(2030, 2, 28), date(2030, 3, 30), date(2030, 4, 30)],
        [date(2030, 2, 28), date(2030, 3, 30), date(2030, 4, 30)],
    ),
    # Feb. 28 in a non-leap year is a month-end: the flag chooses the 28th or month-ends.
    (
        date(2030, 2, 28),
        "month",
        [date(2030, 3, 28), date(2030, 4, 28), date(2030, 5, 28)],
        [date(2030, 3, 31), date(2030, 4, 30), date(2030, 5, 31)],
    ),
    # Feb. 28 in a leap year is not a month-end, so it keeps its day number.
    (
        date(2028, 2, 28),
        "month",
        [date(2028, 3, 28), date(2028, 4, 28), date(2028, 5, 28)],
        [date(2028, 3, 28), date(2028, 4, 28), date(2028, 5, 28)],
    ),
    # A 30-day month-end start reaches the 31st only under the month-end rule.
    (
        date(2030, 4, 30),
        "month",
        [date(2030, 5, 30), date(2030, 6, 30), date(2030, 7, 30)],
        [date(2030, 5, 31), date(2030, 6, 30), date(2030, 7, 31)],
    ),
    # A quarter-end start stays on quarter-ends only under the month-end rule.
    (
        date(2030, 6, 30),
        "quarter",
        [date(2030, 9, 30), date(2030, 12, 30), date(2031, 3, 30)],
        [date(2030, 9, 30), date(2030, 12, 31), date(2031, 3, 31)],
    ),
    # A yearly month-end start lands on Feb. 29 in leap years under the month-end rule.
    (
        date(2029, 2, 28),
        "year",
        [date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 28)],
        [date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)],
    ),
    # A yearly leap-day start clamps to Feb. 28 in non-leap years and returns to Feb. 29 in leap
    # years under either rule.
    (
        date(2028, 2, 29),
        "year",
        [date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)],
        [date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)],
    ),
]


@pytest.mark.parametrize("end_of_month", [False, True])
@pytest.mark.parametrize(("start", "frequency", "without_eom", "with_eom"), _REFERENCE_SCHEDULES)
def test_add_periods_matches_reference_schedules(
    start, frequency, without_eom, with_eom, end_of_month
):
    """Each reference start produces its listed boundaries under each setting of the month-end
    rule, spelling out the rule on the start dates where the two settings can differ."""
    expected = with_eom if end_of_month else without_eom
    boundaries = [
        add_periods(start, k, frequency, end_of_month=end_of_month)
        for k in range(1, len(expected) + 1)
    ]
    assert boundaries == expected


# === Boundary dates: properties ===


@given(ANCHOR_DATES, st.integers(-240, 240), FREQUENCIES, END_OF_MONTH)
@example(date(2028, 2, 29), 4, "year", True)  # leap day returns in the next leap year
@example(date(2100, 2, 28), 1, "year", True)  # 2100 is not a leap year
@example(date(2030, 4, 30), -1, "month", True)  # backward from a month-end: Mar. 31
@example(date(2030, 4, 30), -1, "month", False)  # backward keeping the day number: Mar. 30
def test_add_periods_counts_each_boundary_from_the_start(start, count, frequency, end_of_month):
    """Boundary *k* is computed from the start, never from an earlier clamped boundary, in either
    direction."""
    assert add_periods(start, count, frequency, end_of_month=end_of_month) == anchored_boundary(
        start, count, frequency, end_of_month
    )


@given(ANCHOR_DATES, FREQUENCIES, END_OF_MONTH)
@example(date(2030, 2, 28), "month", True)  # a month-end start is its own zeroth boundary
def test_add_periods_zero_count_returns_start(start, frequency, end_of_month):
    """Boundary zero of any schedule is its start, whatever the frequency or month-end rule."""
    assert add_periods(start, 0, frequency, end_of_month=end_of_month) == start


@given(ANCHOR_DATES, st.integers(-240, 240), FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 1, "month", True)  # Apr. 30 -> May 31 -> Apr. 30
@example(date(2028, 2, 29), 1, "year", False)  # Feb. 29 -> Feb. 28 -> Feb. 28
@example(date(2030, 1, 31), 1, "month", False)  # Jan. 31 -> Feb. 28 -> Jan. 28
@example(date(2030, 1, 28), 1, "month", True)  # Jan. 28 -> Feb. 28 -> Jan. 31
def test_add_periods_reverse_count_returns_to_the_start_when_its_day_survives(
    start, count, frequency, end_of_month
):
    """Stepping *count* periods and back returns to the start's month, and to the start itself
    unless the outbound boundary was clamped and lost the start's day.

    The day survives when it exists in every month (the 1st through the 27th, or the 28th without
    the month-end rule) or, under the month-end rule, when the start is a month-end. Otherwise the
    round trip can land elsewhere in the month: Jan. 31 clamps to Feb. 28 and returns as Jan. 28,
    and under the month-end rule Jan. 28 reaches Feb. 28, a month-end, and returns as Jan. 31.
    """
    there = add_periods(start, count, frequency, end_of_month=end_of_month)
    back = add_periods(there, -count, frequency, end_of_month=end_of_month)

    survives = (start.day <= 27 or is_month_end(start)) if end_of_month else start.day <= 28
    if frequency == "day" or survives:
        assert back == start
    else:
        assert (back.year, back.month) == (start.year, start.month)


@given(ANCHOR_DATES, st.integers(-240, 240), st.sampled_from(["month", "quarter", "year"]))
@example(date(2030, 4, 30), -1, "month")  # backward from a 30-day month-end: Mar. 31
@example(date(2030, 6, 30), 2, "quarter")  # Jun. 30 -> Dec. 31
def test_end_of_month_start_keeps_every_boundary_on_a_month_end(start, count, frequency):
    """Under the default month-end rule, a schedule starting on a month-end lands on a month-end
    at every month, quarter, or year boundary, stepping forward or backward."""
    assume(is_month_end(start))
    assert is_month_end(add_periods(start, count, frequency))


@given(ANCHOR_DATES, st.integers(-240, 240))
@example(date(2030, 2, 28), 1)  # a month-end start still steps one calendar day
def test_daily_schedules_ignore_end_of_month(start, count):
    """Daily boundaries step whole calendar days, so the month-end rule never changes them."""
    expected = start + timedelta(days=count)
    assert add_periods(start, count, "day", end_of_month=True) == expected
    assert add_periods(start, count, "day", end_of_month=False) == expected


@given(ANCHOR_DATES, st.integers(-240, 240), FREQUENCIES)
def test_end_of_month_only_changes_short_month_end_starts(start, count, frequency):
    """The flag only matters for a start on the last day of a month shorter than 31 days."""
    assume(not (is_month_end(start) and start.day < 31))
    assert add_periods(start, count, frequency, end_of_month=True) == add_periods(
        start, count, frequency, end_of_month=False
    )


@given(ANCHOR_DATES, st.integers(-240, 240), st.integers(1, 2000), FREQUENCIES)
@example(date(2030, 4, 30), 1, 31, "month")  # Apr. 30 -> May 31 is a month-end step
def test_end_of_month_defaults_to_true(start, count, days, frequency):
    """Omitting ``end_of_month`` behaves exactly like passing ``True`` for boundaries,
    elapsed periods, and period windows."""
    end = start + timedelta(days=days)
    assert add_periods(start, count, frequency) == add_periods(
        start, count, frequency, end_of_month=True
    )
    assert elapsed_periods(start, end, frequency) == elapsed_periods(
        start, end, frequency, end_of_month=True
    )
    assert period_windows(start, abs(count), frequency) == period_windows(
        start, abs(count), frequency, end_of_month=True
    )


# === Elapsed periods ===


@given(ANCHOR_DATES, st.integers(0, 240), COUNTED_FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 1, "month", True)  # Apr. 30 -> May 31 is one month
@example(date(2030, 2, 28), 1, "month", False)  # Feb. 28 -> Mar. 28 is one month
@example(date(2030, 6, 30), 2, "quarter", True)  # Jun. 30 -> Dec. 31 is two quarters
def test_full_scheduled_periods_measure_exactly_whole(start, count, frequency, end_of_month):
    """Every whole scheduled day, month, or quarter measures exactly one period. Years are
    excluded because they are measured by the day-count convention (see
    ``test_scheduled_years_are_measured_by_day_count_convention``)."""
    end = add_periods(start, count, frequency, end_of_month=end_of_month)
    assert elapsed_periods(start, end, frequency, end_of_month=end_of_month) == count


@pytest.mark.parametrize(
    ("start", "end", "day_count_convention", "expected"),
    [
        # A scheduled year from a leap day is 365 days that include Feb. 29, 2028.
        (date(2028, 2, 29), date(2029, 2, 28), "actual/actual", 307 / 366 + 58 / 365),
        (date(2028, 2, 29), date(2029, 2, 28), "actual/365-fixed", 1.0),
        (date(2028, 2, 29), date(2029, 2, 28), "actual/365-no-leap", 364 / 365),
        # A scheduled year that contains no leap day is one year under every convention.
        (date(2029, 6, 30), date(2030, 6, 30), "actual/actual", 1.0),
        (date(2029, 6, 30), date(2030, 6, 30), "actual/365-fixed", 1.0),
        (date(2029, 6, 30), date(2030, 6, 30), "actual/365-no-leap", 1.0),
        # A 366-day scheduled year that crosses Feb. 29.
        (date(2028, 1, 1), date(2029, 1, 1), "actual/actual", 1.0),
        (date(2028, 1, 1), date(2029, 1, 1), "actual/365-fixed", 366 / 365),
        (date(2028, 1, 1), date(2029, 1, 1), "actual/365-no-leap", 1.0),
    ],
)
@pytest.mark.parametrize("end_of_month", [False, True])
def test_scheduled_years_are_measured_by_day_count_convention(
    start, end, day_count_convention, expected, end_of_month
):
    """Year measurement uses the day-count convention, not anchored boundaries, so a scheduled
    year need not measure exactly 1.0: the year from Feb. 29, 2028 to Feb. 28, 2029 is one
    schedule period but measures short of a year under actual/actual and actual/365-no-leap."""
    assert add_periods(start, 1, "year", end_of_month=end_of_month) == end
    measured = elapsed_periods(start, end, "year", day_count_convention, end_of_month=end_of_month)
    assert measured == pytest.approx(expected)


@given(ANCHOR_DATES, st.integers(1, 2000), COUNTED_FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 15, "month", True)  # 15 of the 31 days in [Apr. 30, May 31)
@example(date(2030, 1, 1), 36, "quarter", True)  # 36 of the 90 days in [Jan. 1, Apr. 1)
def test_partial_periods_measure_the_elapsed_share_of_the_scheduled_period(
    start, days, frequency, end_of_month
):
    """Elapsed days, months, or quarters count the whole scheduled periods passed, plus the
    elapsed share of the days in the scheduled period containing the end date."""
    end = start + timedelta(days=days)
    measured = elapsed_periods(start, end, frequency, end_of_month=end_of_month)
    assert measured == pytest.approx(_elapsed_oracle(start, end, frequency, end_of_month))


@given(ANCHOR_DATES, st.integers(1, 2000), FREQUENCIES, END_OF_MONTH, DAY_COUNT_CONVENTIONS)
@example(date(2030, 4, 30), 31, "month", True, "actual/actual")  # Apr. 30 <-> May 31
@example(date(2028, 2, 29), 365, "year", True, "actual/actual")  # Feb. 29 <-> Feb. 28
def test_elapsed_periods_reversed_dates_negate(start, days, frequency, end_of_month, convention):
    """Measuring from the later date back to the earlier one gives exactly the negative of the
    forward measurement, for every frequency and day-count convention."""
    end = start + timedelta(days=days)
    forward = elapsed_periods(start, end, frequency, convention, end_of_month=end_of_month)
    assert elapsed_periods(end, start, frequency, convention, end_of_month=end_of_month) == -forward


@given(ANCHOR_DATES, st.integers(1, 2000), END_OF_MONTH, DAY_COUNT_CONVENTIONS)
@example(date(2029, 2, 28), 365, True, "actual/actual")  # a month-end start
def test_year_measurement_ignores_end_of_month(start, days, end_of_month, convention):
    """Elapsed years come from the day-count convention, so the month-end rule never changes
    them."""
    end = start + timedelta(days=days)
    assert elapsed_periods(
        start, end, "year", convention, end_of_month=end_of_month
    ) == elapsed_periods(start, end, "year", convention)


@pytest.mark.parametrize(
    ("start", "end", "frequency", "end_of_month", "expected"),
    [
        (date(2030, 4, 30), date(2030, 5, 31), "month", True, 1.0),
        # Without the month-end rule, Apr. 30 + 1 month is May 30, one day into the 31-day
        # month [May 30, Jun. 30).
        (date(2030, 4, 30), date(2030, 5, 31), "month", False, 1 + 1 / 31),
        (date(2030, 2, 28), date(2030, 3, 31), "month", True, 1.0),
        (date(2030, 2, 28), date(2030, 3, 31), "month", False, 1 + 3 / 31),
        (date(2030, 4, 30), date(2030, 5, 15), "month", True, 15 / 31),
        (date(2030, 4, 30), date(2030, 5, 15), "month", False, 15 / 30),
        # Without the month-end rule, Jun. 30 + 2 quarters is Dec. 30, one day into a 90-day
        # quarter [Dec. 30, Mar. 30).
        (date(2030, 6, 30), date(2030, 12, 31), "quarter", True, 2.0),
        (date(2030, 6, 30), date(2030, 12, 31), "quarter", False, 2 + 1 / 90),
        # Jan. 1 -> Feb. 6 is 36 of the quarter's 90 days.
        (date(2030, 1, 1), date(2030, 2, 6), "quarter", True, 0.4),
    ],
)
def test_end_of_month_changes_month_end_measurement(start, end, frequency, end_of_month, expected):
    """Exact elapsed months and quarters from month-end starts under each setting of the
    month-end rule, worked out by hand so they do not depend on the test oracle."""
    measured = elapsed_periods(start, end, frequency, end_of_month=end_of_month)
    assert measured == pytest.approx(expected)


# === Period windows ===


@given(ANCHOR_DATES, st.integers(0, 60), FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 3, "month", True)  # a 30-day month-end start stays on month-ends
@example(date(2030, 6, 30), 4, "quarter", False)  # quarterly start keeping the day number
def test_period_windows_are_anchored_whole_periods(start, periods, frequency, end_of_month):
    """A whole number of periods produces back-to-back windows between consecutive anchored
    boundaries, each measuring exactly one period."""
    windows = period_windows(start, periods, frequency, end_of_month=end_of_month)

    expected = [anchored_boundary(start, k, frequency, end_of_month) for k in range(periods + 1)]
    assert [(w.start, w.end) for w in windows] == list(zip(expected, expected[1:]))
    assert all(w.fraction == 1.0 for w in windows)


@pytest.mark.parametrize(
    ("start", "frequency", "expected_ends"),
    [
        (
            date(2030, 1, 31),
            "month",
            [date(2030, 2, 28), date(2030, 3, 31), date(2030, 4, 30), date(2030, 5, 31)],
        ),
        (
            date(2028, 2, 29),
            "year",
            [date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)],
        ),
    ],
)
def test_issue_38_schedules_do_not_drift(start, frequency, expected_ends):
    """The schedules reported in issue #38 keep their anchor: a Jan. 31 monthly schedule
    returns to the 31st after February, and a Feb. 29 yearly schedule returns to Feb. 29 in
    the next leap year."""
    assert [w.end for w in period_windows(start, 4, frequency)] == expected_ends


@given(
    ANCHOR_DATES,
    st.integers(0, 24),
    st.floats(min_value=0.01, max_value=0.99),
    FREQUENCIES,
    END_OF_MONTH,
)
@example(date(2030, 1, 1), 0, 0.4, "quarter", True)  # 36 of the quarter's 90 days
@example(date(2030, 4, 30), 1, 0.5, "month", True)  # tail is half of [May 31, Jun. 30)
def test_period_windows_fractional_tail_is_a_share_of_the_next_scheduled_period(
    start, whole, fraction, frequency, end_of_month
):
    """A fractional period count appends a final window holding the complete days of that share
    of the next scheduled period, measured against that period's length."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        windows = period_windows(start, whole + fraction, frequency, end_of_month=end_of_month)

    tail_start = anchored_boundary(start, whole, frequency, end_of_month)
    next_boundary = anchored_boundary(start, whole + 1, frequency, end_of_month)
    period_days = (next_boundary - tail_start).days
    requested = fraction * period_days
    nearest = round(requested)
    tail_days = nearest if abs(requested - nearest) <= 1e-12 else int(requested)

    assert len(windows) == whole + (tail_days > 0)
    if tail_days:
        tail = windows[-1]
        assert (tail.start, tail.end) == (tail_start, tail_start + timedelta(days=tail_days))
        if frequency in ("month", "quarter"):
            assert tail.fraction == pytest.approx(tail_days / period_days)
