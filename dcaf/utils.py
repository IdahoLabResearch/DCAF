from datetime import date
from functools import cache
from typing import assert_never

from dateutil.relativedelta import relativedelta

from .types import DayCountConvention, Period


def period_start(dt: date, period: Period) -> date:
    """Return the start date of the period containing *dt*."""
    match period:
        case "day":
            return dt
        case "month":
            return date(dt.year, dt.month, 1)
        case "quarter":
            quarter_month = ((dt.month - 1) // 3) * 3 + 1
            return date(dt.year, quarter_month, 1)
        case "year":
            return date(dt.year, 1, 1)
        case _:
            assert_never(period)


def period_end(dt: date, period: Period) -> date:
    """Return the last date of the period containing *dt*."""
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


def timedelta_fractional_years(
    start: date, end: date, convention: DayCountConvention = "actual/365"
) -> float:
    """Calculate the year fraction between two dates using the given day count convention."""
    # For discounting purposes, we need to choose a convention for how many days are in a year. The default
    # behavior is the standard "actual/365" convention, which assumes every year--including leap years--has
    # 365 days.
    match convention:
        case "actual/365":
            return (end - start).days / 365.0
        case _:
            assert_never(convention)


def compound_factor(rate: float, periods: float) -> float:
    """
    Compute a compound growth/discount factor.

    Used for both discounting (``amount / compound_factor(rate, years)``)
    and escalation (``amount * compound_factor(rate, periods)``).

    Parameters
    ----------
    rate : float
        The periodic rate (e.g., 0.10 for 10%).
    periods : float
        Number of compounding periods (can be fractional).

    Returns
    -------
    float
        ``(1 + rate) ** periods``.
    """
    return (1.0 + rate) ** periods


@cache
def hours_per_period(period: Period) -> float:
    """Return the number of hours in a period."""
    match period:
        case "year":
            return 8760.0
        case "quarter":
            return 8760.0 / 4
        case "month":
            return 8760.0 / 12
        case "day":
            return 24.0
        case _:
            assert_never(period)


@cache
def time_delta_per_period(period: Period) -> relativedelta:
    """Return a relativedelta representing one period."""
    match period:
        case "year":
            return relativedelta(years=1)
        case "quarter":
            return relativedelta(months=3)
        case "month":
            return relativedelta(months=1)
        case "day":
            return relativedelta(days=1)
        case _:
            assert_never(period)
