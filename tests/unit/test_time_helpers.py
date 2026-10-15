# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for the calendar helpers in ``dcaf.shared.time`` that other tests rely on.

Expected values are hand-calculated or follow from calendar properties, never from a
reimplementation of the helper under test.
"""

from datetime import date, timedelta

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from dcaf.shared.time import (
    _FLOAT_TOLERANCE,
    PeriodWindow,
    calendar_period_windows,
    _is_month_end,
    _whole_days_with_truncation,
    period_start,
    period_window_event_date,
)
from strategies import DATES, FREQUENCIES, TIMINGS, intervals

# === _is_month_end ===


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2030, 1, 31), True),
        (date(2030, 1, 30), False),
        (date(2030, 4, 30), True),
        (date(2030, 2, 28), True),  # non-leap February
        (date(2028, 2, 28), False),  # leap February
        (date(2028, 2, 29), True),
        (date(2100, 2, 28), True),  # century non-leap year
        (date(2000, 2, 28), False),  # 400-year leap year
        (date(2030, 12, 31), True),
        (date(2030, 1, 1), False),
    ],
)
def test_is_month_end_reference_dates(day, expected):
    """Hand-checked month-ends, including February in leap, non-leap, and century years."""
    assert _is_month_end(day) is expected


@given(DATES)
def test_is_month_end_when_next_day_is_start_of_next_month(day):
    """A day is a month-end exactly when the following day is the first of a month."""
    next_day = day + timedelta(days=1)
    next_month = day.month % 12 + 1
    is_month_end = _is_month_end(day)
    # next day is first day of next month
    assert is_month_end is ((next_day.day == 1) and (next_day.month == next_month))


# === _whole_days_with_truncation ===


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (14.0, (14, False)),
        (15.5, (15, True)),
        (0.5, (0, True)),
        (0.0, (0, False)),
        (14.0 + 1e-13, (14, False)),  # within tolerance of a whole day
        (14.0 - 1e-13, (14, False)),
        (14.0 - 1e-6, (13, True)),  # outside tolerance, so the partial day is dropped
        (14.0 + 1e-6, (14, True)),  # outside tolerance, so the partial day is dropped
    ],
)
def test_whole_days_with_truncation_reference_values(days, expected):
    """Whole days within tolerance round to the nearest day; anything else floors and flags."""
    assert _whole_days_with_truncation(days) == expected


@given(st.floats(min_value=0.0, max_value=1e6))
@example(31 * 0.1 * 10)  # float noise around a whole day
def test_whole_days_with_truncation_keeps_complete_days_only(days):
    """The result is within one day below the input (or float tolerance above it), and it is
    flagged as truncated exactly when it differs from the input by more than that tolerance."""
    whole, truncated = _whole_days_with_truncation(days)
    assert whole - _FLOAT_TOLERANCE <= days < whole + 1
    assert truncated is (abs(days - whole) > _FLOAT_TOLERANCE)


# === period_window_event_date ===


@pytest.mark.parametrize(
    ("timing", "expected"),
    [("begin", date(2030, 1, 1)), ("middle", date(2030, 1, 16)), ("end", date(2030, 1, 31))],
)
def test_period_window_event_date_january(timing, expected):
    """January's 31 days book on the 1st, the 16th (15 days after the start), or the 31st."""
    window = PeriodWindow(date(2030, 1, 1), date(2030, 2, 1))
    assert period_window_event_date(window, timing) == expected


@pytest.mark.parametrize(
    ("timing", "expected"),
    [("begin", date(2030, 1, 1)), ("middle", date(2030, 1, 1)), ("end", date(2030, 1, 2))],
)
def test_period_window_event_date_two_day_window(timing, expected):
    """A two-day window's middle rounds down to its first day."""
    window = PeriodWindow(date(2030, 1, 1), date(2030, 1, 3))
    assert period_window_event_date(window, timing) == expected


@given(intervals())
def test_period_window_event_date_is_ordered_inside_the_window(interval):
    """Begin is the first day and end the last day of the half-open window, with middle between
    them, its distance from each end differing by at most one day."""
    start, end = interval
    window = PeriodWindow(start, end)
    begin, middle, last = (period_window_event_date(window, t) for t in ("begin", "middle", "end"))

    assert begin == start
    assert last == end - timedelta(days=1)
    assert begin <= middle <= last
    assert 0 <= (last - middle).days - (middle - begin).days <= 1


@given(DATES, TIMINGS)
def test_period_window_event_date_one_day_window_books_on_that_day(day, timing):
    """Every timing books a one-day window on its only day."""
    window = PeriodWindow(day, day + timedelta(days=1))
    assert period_window_event_date(window, timing) == day


# === period_start ===


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        ("day", date(2030, 5, 17)),
        ("month", date(2030, 5, 1)),
        ("quarter", date(2030, 4, 1)),
        ("year", date(2030, 1, 1)),
    ],
)
def test_period_start_reference_dates(frequency, expected):
    """May 17, 2030 is in the month starting May 1, the quarter starting Apr. 1, and the year
    starting Jan. 1."""
    assert period_start(date(2030, 5, 17), frequency) == expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2030, 3, 31), date(2030, 1, 1)),
        (date(2030, 4, 1), date(2030, 4, 1)),
        (date(2030, 9, 30), date(2030, 7, 1)),
        (date(2030, 12, 31), date(2030, 10, 1)),
    ],
)
def test_period_start_quarter_edges(day, expected):
    """The last and first days of quarters fall in the expected calendar quarter."""
    assert period_start(day, "quarter") == expected


@given(DATES, FREQUENCIES)
def test_period_start_is_an_idempotent_lower_bound(day, frequency):
    """The period start is on or before the day, is itself a period start, and stays in the
    day's year and, below a year, the day's quarter."""
    start = period_start(day, frequency)

    assert start <= day
    assert period_start(start, frequency) == start
    assert start.year == day.year
    if frequency != "year":
        assert (start.month - 1) // 3 == (day.month - 1) // 3


@given(DATES, FREQUENCIES)
def test_period_start_changes_only_at_period_boundaries(day, frequency):
    """The day after a day shares its period start unless that next day starts a new period."""
    following = day + timedelta(days=1)
    starts_new_period = period_start(following, frequency) == following

    assert (period_start(following, frequency) == period_start(day, frequency)) is not (
        starts_new_period
    )


# === calendar_period_windows ===


@pytest.mark.parametrize(
    ("start", "end", "frequency", "expected"),
    [
        (
            date(2030, 1, 15),
            date(2030, 3, 10),
            "month",
            [
                (date(2030, 1, 15), date(2030, 2, 1)),
                (date(2030, 2, 1), date(2030, 3, 1)),
                (date(2030, 3, 1), date(2030, 3, 10)),
            ],
        ),
        (
            date(2028, 11, 30),
            date(2029, 4, 1),
            "quarter",
            [(date(2028, 11, 30), date(2029, 1, 1)), (date(2029, 1, 1), date(2029, 4, 1))],
        ),
        (date(2030, 2, 1), date(2030, 3, 1), "month", [(date(2030, 2, 1), date(2030, 3, 1))]),
    ],
)
def test_calendar_period_windows_reference_spans(start, end, frequency, expected):
    """Spans are clipped at calendar period starts, and a span that is one calendar period is a
    single window."""
    assert calendar_period_windows(start, end, frequency) == expected


@given(intervals(), FREQUENCIES)
@example((date(2028, 2, 28), date(2028, 3, 2)), "month")  # crosses a leap-year month end
@example((date(2029, 12, 31), date(2030, 1, 1)), "year")  # a single day ending a year
def test_calendar_period_windows_tile_the_span_at_period_starts(interval, frequency):
    """Windows cover ``[start, end)`` back to back and in order, each is non-empty and lies in
    one calendar period, and every cut between windows is a calendar period start."""
    start, end = interval
    windows = calendar_period_windows(start, end, frequency)

    assert windows[0][0] == start
    assert windows[-1][1] == end
    for (_, before_end), (after_start, _) in zip(windows, windows[1:]):
        assert before_end == after_start
        assert period_start(after_start, frequency) == after_start
    for window_start, window_end in windows:
        assert window_start < window_end
        last_day = window_end - timedelta(days=1)
        assert period_start(window_start, frequency) == period_start(last_day, frequency)
