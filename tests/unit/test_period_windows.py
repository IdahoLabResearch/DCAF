# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from datetime import date
import warnings

import pytest

from dcaf.shared.time import PeriodTruncationWarning, period_windows


def test_period_windows_integer_counts_are_full_periods():
    windows = period_windows(date(2026, 1, 1), 2, "month")

    assert [(window.start, window.end, window.fraction) for window in windows] == [
        (date(2026, 1, 1), date(2026, 2, 1), 1.0),
        (date(2026, 2, 1), date(2026, 3, 1), 1.0),
    ]


def test_period_windows_fractional_count_exact_midnight_does_not_warn():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        windows = period_windows(date(2026, 2, 1), 0.5, "month")

    assert caught == []
    assert len(windows) == 1
    assert windows[0].start == date(2026, 2, 1)
    assert windows[0].end == date(2026, 2, 15)
    assert windows[0].fraction == pytest.approx(0.5)


def test_period_windows_fractional_count_warns_and_truncates_partial_day():
    with pytest.warns(PeriodTruncationWarning) as caught:
        windows = period_windows(date(2026, 1, 1), 0.5, "month")

    assert "truncated exclusive end is 2026-01-16" in str(caught[0].message)
    assert "last included date is 2026-01-15" in str(caught[0].message)
    assert len(windows) == 1
    assert windows[0].start == date(2026, 1, 1)
    assert windows[0].end == date(2026, 1, 16)
    assert windows[0].fraction == pytest.approx(15 / 31)


def test_period_windows_fractional_day_under_one_complete_day_is_omitted():
    with pytest.warns(PeriodTruncationWarning, match="no complete days were included"):
        windows = period_windows(date(2026, 1, 1), 0.5, "day")

    assert windows == ()


def test_period_windows_rejects_negative_counts():
    with pytest.raises(ValueError, match="non-negative"):
        period_windows(date(2026, 1, 1), -0.5, "year")
