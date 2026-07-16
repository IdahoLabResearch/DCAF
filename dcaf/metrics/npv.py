# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Net present value calculation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from dcaf.shared.time import timedelta_fractional_years
from dcaf.shared.types import DayCountConvention


def npv(
    values: Iterable[tuple[float, date]],
    rate: float,
    valuation_date: date,
    convention: DayCountConvention = "actual/actual",
) -> float:
    """Compute the net present value of a series of dated values.

    Discounts or compounds each ``(amount, date)`` pair to the
    ``valuation_date`` using the formula ``PV = amount / (1 + rate)^t``
    where *t* is the fractional-year distance from *valuation_date* to
    the entry's date under the chosen day-count convention.

    This is the single primitive behind :meth:`CashFlowStream.npv` and
    :meth:`GenerationStream.discounted_sum`.

    Parameters
    ----------
    values : Iterable[tuple[float, date]]
        ``(amount, date)`` pairs to discount. Both financial amounts and
        physical quantities (e.g. MWh) are supported.
    rate : float
        Annual discount rate as a decimal (e.g. ``0.10`` for 10%).
    valuation_date : date
        Reference date for discounting/compounding.
    convention : DayCountConvention, optional
        Day-count convention for year-fraction conversion.
        Default is ``"actual/actual"``.

    Returns
    -------
    float
        Sum of present values. Returns ``0.0`` for an empty *values*
        sequence.

    Examples
    --------
    >>> from datetime import date
    >>> npv(
    ...     [(-1000.0, date(2024, 1, 1)), (1100.0, date(2025, 1, 1))],
    ...     rate=0.10,
    ...     valuation_date=date(2024, 1, 1),
    ... )  # doctest: +SKIP
    0.0  # approximately
    """
    one_plus_r = 1.0 + rate
    total = 0.0
    for amount, d in values:
        t = timedelta_fractional_years(valuation_date, d, convention)
        total += amount / one_plus_r**t
    return total
