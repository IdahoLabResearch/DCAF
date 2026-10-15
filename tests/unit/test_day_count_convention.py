# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for day-count and year-length conventions."""

from calendar import isleap
from datetime import date, timedelta

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from dcaf.shared.time import elapsed_hours, timedelta_fractional_years
from dcaf.shared.types import parse_day_count_convention
from strategies import DATES, DAY_COUNT_CONVENTIONS, intervals


def test_actual_365_no_leap_excludes_leap_day():
    assert timedelta_fractional_years(
        date(2024, 1, 1),
        date(2025, 1, 1),
        "actual/365-no-leap",
    ) == pytest.approx(1.0)
    assert timedelta_fractional_years(
        date(2024, 2, 28),
        date(2024, 3, 1),
        "actual/365-no-leap",
    ) == pytest.approx(1.0 / 365.0)
    assert timedelta_fractional_years(
        date(2024, 2, 29),
        date(2024, 3, 1),
        "actual/365-no-leap",
    ) == pytest.approx(0.0)


def test_actual_365_fixed_preserves_calendar_days_over_365():
    assert timedelta_fractional_years(
        date(2024, 1, 1),
        date(2025, 1, 1),
        "actual/365-fixed",
    ) == pytest.approx(366.0 / 365.0)
    assert timedelta_fractional_years(
        date(2024, 2, 28),
        date(2024, 3, 1),
        "actual/365-fixed",
    ) == pytest.approx(2.0 / 365.0)


def test_actual_actual_splits_by_calendar_year():
    expected = 184.0 / 365.0 + 1.0 + 181.0 / 365.0
    assert timedelta_fractional_years(
        date(2023, 7, 1),
        date(2025, 7, 1),
        "actual/actual",
    ) == pytest.approx(expected)
    assert timedelta_fractional_years(
        date(2024, 1, 1),
        date(2025, 1, 1),
        "actual/actual",
    ) == pytest.approx(1.0)


def test_negative_spans_are_signed():
    assert timedelta_fractional_years(
        date(2024, 3, 1),
        date(2024, 2, 28),
        "actual/365-no-leap",
    ) == pytest.approx(-1.0 / 365.0)
    assert timedelta_fractional_years(
        date(2025, 7, 1),
        date(2023, 7, 1),
        "actual/actual",
    ) == pytest.approx(-(184.0 / 365.0 + 1.0 + 181.0 / 365.0))


@pytest.mark.parametrize(
    ("convention", "expected"),
    [
        # 184 days of 2027 and 182 days of the leap year 2028.
        ("actual/actual", 184 / 365 + 182 / 366),
        ("actual/365-fixed", 366 / 365),
        # 366 calendar days less Feb. 29, 2028.
        ("actual/365-no-leap", 1.0),
    ],
)
def test_year_fraction_across_a_leap_day(convention, expected):
    """A 366-day span from Jul. 1, 2027 to Jul. 1, 2028 crosses Feb. 29, 2028."""
    measured = timedelta_fractional_years(date(2027, 7, 1), date(2028, 7, 1), convention)
    assert measured == pytest.approx(expected)


@given(st.integers(1990, 2110))
@example(2028)  # leap year
@example(2100)  # century non-leap year
def test_actual_actual_calendar_year_is_one_year(year):
    """A full calendar year measures exactly one year under actual/actual, leap or not."""
    measured = timedelta_fractional_years(date(year, 1, 1), date(year + 1, 1, 1), "actual/actual")
    assert measured == pytest.approx(1.0)


@given(intervals())
def test_actual_365_fixed_is_calendar_days_over_365(interval):
    """Actual/365-fixed is calendar days divided by 365, which defines the convention."""
    start, end = interval
    measured = timedelta_fractional_years(start, end, "actual/365-fixed")
    assert measured == pytest.approx((end - start).days / 365, abs=1e-12)


@given(DATES, st.integers(0, 1500), st.integers(0, 1500), DAY_COUNT_CONVENTIONS)
@example(date(2027, 12, 1), 31, 90, "actual/actual")  # split at a year boundary
@example(date(2028, 2, 1), 28, 2, "actual/365-no-leap")  # split at Feb. 29
def test_year_fractions_of_consecutive_spans_add(start, first, second, convention):
    """Measuring ``[a, b)`` and ``[b, c)`` separately sums to measuring ``[a, c)``."""
    middle = start + timedelta(days=first)
    end = middle + timedelta(days=second)
    parts = timedelta_fractional_years(start, middle, convention) + timedelta_fractional_years(
        middle, end, convention
    )
    assert parts == pytest.approx(timedelta_fractional_years(start, end, convention), abs=1e-12)


@given(DATES, st.integers(0, 1500), st.integers(0, 1500), DAY_COUNT_CONVENTIONS)
@example(date(2028, 2, 1), 28, 2, "actual/365-no-leap")  # split at Feb. 29
def test_elapsed_hours_of_consecutive_spans_add(start, first, second, convention):
    """Hours over ``[a, b)`` and ``[b, c)`` sum exactly to the hours over ``[a, c)``."""
    middle = start + timedelta(days=first)
    end = middle + timedelta(days=second)
    parts = elapsed_hours(start, middle, convention) + elapsed_hours(middle, end, convention)
    assert parts == pytest.approx(elapsed_hours(start, end, convention), abs=1e-12)


@given(DATES)
@example(date(2028, 2, 29))
@example(date(2028, 2, 28))
@example(date(2100, 2, 28))  # no Feb. 29 in 2100
def test_no_leap_drops_exactly_feb_29(day):
    """Under actual/365-no-leap a single day has 24 hours unless it is Feb. 29, which has none.
    With additivity across consecutive spans, each Feb. 29 in a span removes exactly one day."""
    expected = 0.0 if (day.month, day.day) == (2, 29) else 24.0
    assert elapsed_hours(day, day + timedelta(days=1), "actual/365-no-leap") == expected


@given(st.integers(1990, 2109), st.integers(0, 364), st.integers(0, 365))
def test_no_leap_spans_without_feb_29_count_every_day(year, offset, days):
    """A span inside ``[Mar. 1, Mar. 1)`` of a year whose following February has no 29th holds
    no Feb. 29, so it has 24 hours per calendar day under actual/365-no-leap."""
    assume(not isleap(year + 1))
    start = date(year, 3, 1) + timedelta(days=offset)
    end = min(start + timedelta(days=days), date(year + 1, 3, 1))
    assert elapsed_hours(start, end, "actual/365-no-leap") == (end - start).days * 24.0


@given(intervals(), st.sampled_from(["actual/actual", "actual/365-fixed"]))
@example((date(2028, 2, 28), date(2028, 3, 1)), "actual/actual")  # includes Feb. 29
def test_fixed_and_actual_hours_count_every_calendar_day(interval, convention):
    """The fixed and actual/actual conventions put 24 hours on the clock for every calendar
    day, Feb. 29 included."""
    start, end = interval
    assert elapsed_hours(start, end, convention) == (end - start).days * 24.0


def test_elapsed_hours_excludes_leap_day_only_for_actual_365_no_leap():
    assert elapsed_hours(
        date(2024, 1, 1),
        date(2025, 1, 1),
        "actual/365-no-leap",
    ) == pytest.approx(8760.0)
    assert elapsed_hours(
        date(2024, 1, 1),
        date(2025, 1, 1),
        "actual/365-fixed",
    ) == pytest.approx(8784.0)
    assert elapsed_hours(
        date(2024, 1, 1),
        date(2025, 1, 1),
        "actual/actual",
    ) == pytest.approx(8784.0)


def test_day_count_convention_parser_rejects_unknown_values():
    assert parse_day_count_convention(" actual/365-no-leap ").value == "actual/365-no-leap"
    assert parse_day_count_convention("Actual/365NL").value == "actual/365-no-leap"
    assert parse_day_count_convention("Actual/365 No Leap").value == "actual/365-no-leap"
    assert parse_day_count_convention(" actual/365-fixed ").value == "actual/365-fixed"
    with pytest.raises(ValueError, match="Unknown day count convention"):
        parse_day_count_convention("30/360")
    with pytest.raises(ValueError, match="Unknown day count convention"):
        parse_day_count_convention("actual/365")
