"""Tests for day-count and year-length conventions."""

from datetime import date

import pytest

from dcaf.shared.time import elapsed_hours, timedelta_fractional_years
from dcaf.shared.types import parse_day_count_convention


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
