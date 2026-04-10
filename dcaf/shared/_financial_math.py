"""Internal numeric kernels for discounting-based metrics.

This module keeps stream-agnostic math helpers outside ``dcaf.metrics`` so
stream classes can import them at module scope without creating package cycles.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from datetime import date
from typing import Protocol

from dcaf.shared.time import timedelta_fractional_years
from dcaf.shared.types import DayCountConvention

_IRR_MIN_RATE = -1.0 + 1e-8


class _CashFlowLike(Protocol):
    """Minimal cashflow interface required by the IRR solver."""

    @property
    def amount(self) -> float: ...

    @property
    def date(self) -> date: ...


class _CashFlowStreamLike[CashFlowT: _CashFlowLike](Protocol):
    """Minimal stream interface required by the IRR solver."""

    def __iter__(self) -> Iterator[CashFlowT]: ...

    def __len__(self) -> int: ...

    def cash_only(self) -> "_CashFlowStreamLike[CashFlowT]": ...

    def inflows(self) -> "_CashFlowStreamLike[CashFlowT]": ...

    def outflows(self) -> "_CashFlowStreamLike[CashFlowT]": ...

    def min(self, key: Callable[[CashFlowT], date]) -> CashFlowT: ...


def npv(
    values: Iterable[tuple[float, date]],
    rate: float,
    valuation_date: date,
    convention: DayCountConvention = "actual/365",
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
        Default is ``"actual/365"``.

    Returns
    -------
    float
        Sum of present values. Returns ``0.0`` for an empty *values*
        sequence.
    """
    one_plus_r = 1.0 + rate
    total = 0.0
    for amount, entry_date in values:
        t = timedelta_fractional_years(valuation_date, entry_date, convention)
        total += amount / one_plus_r**t
    return total


def irr[CashFlowT: _CashFlowLike](
    stream: _CashFlowStreamLike[CashFlowT],
    convention: DayCountConvention = "actual/365",
    *,
    tol: float = 1e-8,
    max_iter: int = 100,
) -> float:
    """Compute the internal rate of return of a cashflow stream.

    Finds the discount rate at which the NPV of *stream* equals zero
    using a centroid-based Newton-Raphson algorithm.

    Parameters
    ----------
    stream : CashFlowStream
        Cashflow stream to evaluate. Only ``is_cash=True`` entries are
        used.
    convention : DayCountConvention, optional
        Day-count convention. Default is ``"actual/365"``.
    tol : float, optional
        Relative convergence tolerance. Iteration stops when
        ``|NPV(r)| < tol * Σ|CFᵢ|``. Default is ``1e-8``.
    max_iter : int, optional
        Maximum Newton-Raphson iterations. Default is ``100``.

    Returns
    -------
    float
        Annual IRR as a decimal (e.g. ``0.10`` for 10%).

    Raises
    ------
    ValueError
        If the stream has no cash flows, all flows share the same sign,
        or the algorithm fails to converge.
    """
    cash_only = stream.cash_only()
    if not cash_only.inflows() or not cash_only.outflows():
        raise ValueError("IRR requires both positive (inflow) and negative (outflow) cashflows.")

    ref_date = cash_only.min(key=lambda cf: cf.date).date
    rate = max(_irr_initial_guess(cash_only, ref_date, convention), _IRR_MIN_RATE)

    # Scale factor for normalizing the convergence check. IRR is
    # scale-invariant, but NPV scales linearly with cashflow magnitude.
    # Without normalization the absolute tolerance becomes unreachable
    # for large-magnitude streams because the Newton step falls below
    # float64 precision before |NPV| < tol.
    scale = sum(abs(cf.amount) for cf in cash_only)

    for _ in range(max_iter):
        try:
            current_npv, dnpv = _irr_npv_and_dnpv(cash_only, rate, ref_date, convention)
        except OverflowError as exc:
            raise ValueError(
                "IRR did not converge: overflow encountered during iteration."
            ) from exc
        if abs(current_npv) < tol * scale:
            return rate
        if abs(dnpv) < 1e-12:
            raise ValueError("IRR did not converge: zero derivative encountered.")
        unclamped = rate - current_npv / dnpv
        if unclamped == rate:
            # Newton step is below float64 precision, so the rate cannot improve.
            return rate
        rate = max(unclamped, _IRR_MIN_RATE)

    try:
        current_npv, _ = _irr_npv_and_dnpv(cash_only, rate, ref_date, convention)
    except OverflowError as exc:
        raise ValueError("IRR did not converge: overflow encountered during iteration.") from exc
    if abs(current_npv) < tol * scale:
        return rate
    raise ValueError(f"IRR did not converge after {max_iter} iterations.")


def _irr_initial_guess[CashFlowT: _CashFlowLike](
    cashflows: _CashFlowStreamLike[CashFlowT], ref_date: date, convention: DayCountConvention
) -> float:
    """Compute a centroid-based initial guess for Newton-Raphson IRR iteration."""

    def _centroid(
        stream: _CashFlowStreamLike[CashFlowT], weight: Callable[[CashFlowT], float]
    ) -> tuple[float, float]:
        total = 0.0
        weighted_t = 0.0
        for cashflow in stream:
            w = weight(cashflow)
            t = timedelta_fractional_years(ref_date, cashflow.date, convention)
            total += w
            weighted_t += w * t
        return total, weighted_t

    sum_in, weighted_t_in = _centroid(cashflows.inflows(), lambda cf: cf.amount)
    sum_out, weighted_t_out = _centroid(cashflows.outflows(), lambda cf: abs(cf.amount))

    if sum_in < 1e-15 or sum_out < 1e-15:
        return 0.1

    dt = (weighted_t_in / sum_in) - (weighted_t_out / sum_out)
    if abs(dt) < 1e-10:
        return 0.1

    ratio = sum_in / sum_out
    try:
        return ratio ** (1.0 / dt) - 1.0
    except OverflowError:
        return 0.1


def _irr_npv_and_dnpv[CashFlowT: _CashFlowLike](
    cashflows: _CashFlowStreamLike[CashFlowT],
    rate: float,
    ref_date: date,
    convention: DayCountConvention,
) -> tuple[float, float]:
    """Compute NPV and its first derivative with respect to *rate* in a single pass."""
    current_npv = 0.0
    dnpv = 0.0
    one_plus_r = 1.0 + rate
    for cashflow in cashflows:
        t = timedelta_fractional_years(ref_date, cashflow.date, convention)
        pv = cashflow.amount / (one_plus_r**t)
        current_npv += pv
        dnpv -= t / one_plus_r * pv
    return current_npv, dnpv
