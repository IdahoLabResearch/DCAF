"""Internal rate of return calculation."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Callable

from dcaf.shared.time import timedelta_fractional_years
from dcaf.shared.types import DayCountConvention

if TYPE_CHECKING:
    from dcaf.streams.cashflows import CashFlow, CashFlowStream

_IRR_MIN_RATE = -1.0 + 1e-8


def irr(
    stream: CashFlowStream,
    convention: DayCountConvention = "actual/actual",
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
        Day-count convention. Default is ``"actual/actual"``.
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

    Examples
    --------
    >>> from datetime import date
    >>> from dcaf.streams import CashFlow, CashFlowStream
    >>> stream = CashFlowStream([
    ...     CashFlow(-1000.0, date(2025, 1, 1)),
    ...     CashFlow(600.0, date(2026, 1, 1)),
    ...     CashFlow(600.0, date(2027, 1, 1)),
    ... ])
    >>> round(irr(stream), 4)
    0.1307
    """
    cash_only = stream.cash_only()
    if not cash_only.inflows() or not cash_only.outflows():
        raise ValueError("IRR requires both positive (inflow) and negative (outflow) cashflows.")

    ref_date = cash_only.min(key=lambda cf: cf.date).date
    rate = max(_irr_initial_guess(cash_only, ref_date, convention), _IRR_MIN_RATE)

    # Scale factor for normalizing the convergence check.  IRR is
    # scale-invariant, but NPV scales linearly with cashflow magnitude.
    # Without normalization the absolute tolerance becomes unreachable
    # for large-magnitude streams (e.g. trillions of dollars) because
    # the Newton step falls below float64 precision before |NPV| < tol.
    scale = sum(abs(cf.amount) for cf in cash_only)

    for _ in range(max_iter):
        try:
            npv, dnpv = _irr_npv_and_dnpv(cash_only, rate, ref_date, convention)
        except OverflowError as exc:
            raise ValueError(
                "IRR did not converge: overflow encountered during iteration."
            ) from exc
        if abs(npv) < tol * scale:
            return rate
        if abs(dnpv) < 1e-12:
            raise ValueError("IRR did not converge: zero derivative encountered.")
        unclamped = rate - npv / dnpv
        if unclamped == rate:
            # Newton step is below float64 precision — rate can't improve.
            return rate
        rate = max(unclamped, _IRR_MIN_RATE)

    try:
        npv, _ = _irr_npv_and_dnpv(cash_only, rate, ref_date, convention)
    except OverflowError as exc:
        raise ValueError("IRR did not converge: overflow encountered during iteration.") from exc
    if abs(npv) < tol * scale:
        return rate
    raise ValueError(f"IRR did not converge after {max_iter} iterations.")


def _irr_initial_guess(
    cashflows: CashFlowStream, ref_date: date, convention: DayCountConvention
) -> float:
    """Compute a centroid-based initial guess for Newton-Raphson IRR iteration.

    Treats inflows and outflows as two single lumps located at their respective
    time-weighted centroid dates.  Under this two-lump approximation the
    NPV = 0 condition reduces to a closed-form equation in ``r``:

        r₀ = (ΣCF_in / Σ|CF_out|) ^ (1 / (t_in - t_out)) - 1

    Falls back to ``0.1`` when the centroids coincide, when the weighted sums
    are degenerate (zero), or when the ratio overflows during exponentiation.
    """

    def _centroid(
        stream: CashFlowStream, weight: Callable[[CashFlow], float]
    ) -> tuple[float, float]:
        total = 0.0
        weighted_t = 0.0
        for cf in stream:
            w = weight(cf)
            t = timedelta_fractional_years(ref_date, cf.date, convention)
            total += w
            weighted_t += w * t
        return total, weighted_t

    sum_in, weighted_t_in = _centroid(cashflows.inflows(), lambda cf: cf.amount)
    sum_out, weighted_t_out = _centroid(cashflows.outflows(), lambda cf: abs(cf.amount))

    # Degenerate centroids: zero-amount cashflows passed the inflows/outflows
    # check but sum to zero, making the centroid time undefined.
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


def _irr_npv_and_dnpv(
    cashflows: CashFlowStream, rate: float, ref_date: date, convention: DayCountConvention
) -> tuple[float, float]:
    """Compute NPV and its first derivative with respect to *rate* in a single pass.

    Returns ``(npv, dnpv)`` where ``npv = Σ CFᵢ/(1+r)^tᵢ`` and
    ``dnpv = −Σ tᵢ·CFᵢ / (1+r)^(tᵢ+1)``.
    """
    npv = 0.0
    dnpv = 0.0
    one_plus_r = 1.0 + rate
    for cf in cashflows:
        t = timedelta_fractional_years(ref_date, cf.date, convention)
        pv = cf.amount / (one_plus_r**t)
        npv += pv
        dnpv -= t / one_plus_r * pv
    return npv, dnpv
