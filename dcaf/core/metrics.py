"""
``dcaf.core.metrics``

This module contains financial metrics for evaluating a multi-year project. 
"""
from typing import Optional, Sequence

def compute_wacc(
    equity_fraction: float,
    equity_cost: float,
    debt_fraction: float,
    debt_cost: float,
    tax_rate: float,
) -> float:
    """
    Compute the weighted-average cost of capital (WACC).

    Assumes capital structure fractions sum to ~1.0.

    Parameters
    ----------
    equity_fraction : float
    equity_cost     : float
    debt_fraction   : float
    debt_cost       : float
    tax_rate        : float

    Returns
    -------
    float
        WACC as a decimal fraction (e.g., 0.08 for 8%).
    """
    # Guard against weird sums
    total = equity_fraction + debt_fraction
    if total <= 0:
        raise ValueError("Equity_fraction + debt_fraction must be > 0")

    e_weight = equity_fraction / total
    d_weight = debt_fraction / total

    return e_weight * equity_cost + d_weight * debt_cost * (1.0 - tax_rate)

def npv(
    cashflows: Sequence[float],
    discount_rate: float | Sequence[float],
    t0: int = 0,
) -> float:
    """
    Compute net present value with optimized discounting.

    Parameters
    ----------
    cashflows : sequence of float
        Cashflow at each period (year 0, 1, ..., N-1).
    discount_rate : float or sequence of float
        If float: constant discount rate.
        If sequence: per-year discount rates of same length as cashflows.
    t0 : int
        Starting time index (usually 0).

    Returns
    -------
    float
        NPV.
    """
    if isinstance(discount_rate, (int, float)):
        rate = float(discount_rate)
        discount_factor = 1.0 / (1.0 + rate)
        present_value = 0.0
        multiplier = discount_factor ** t0
        
        for cf in cashflows:
            present_value += cf * multiplier
            multiplier *= discount_factor
        return present_value
    else:
        if len(discount_rate) != len(cashflows):
            raise ValueError("discount_rate sequence must match cashflows length.")
        present_value = 0.0
        for t, (cf, rate) in enumerate(zip(cashflows, discount_rate), start=t0):
            present_value += cf / ((1.0 + rate) ** t)
        return present_value


def irr(
    cashflows: Sequence[float],
    guess_low: float = -0.9,
    guess_high: float = 1.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> Optional[float]:
    """
    Compute internal rate of return (IRR) via bisection with optimized convergence.
    Returns None if no sign change in NPV across the bracket.

    Parameters
    ----------
    cashflows : sequence of float
    guess_low : float
        Lower bound on IRR search (e.g., -0.9 for -90%).
    guess_high : float
        Upper bound on IRR search (e.g., 1.0 for 100%).
    tol : float
    max_iter : int

    Returns
    -------
    float or None
    """
    def npv_at_rate(rate: float) -> float:
        return npv(cashflows, rate)

    f_low = npv_at_rate(guess_low)
    f_high = npv_at_rate(guess_high)

    if f_low * f_high > 0:
        # No sign change, IRR not bracketed
        return None

    low, high = guess_low, guess_high
    for iteration in range(max_iter):
        mid = 0.5 * (low + high)
        f_mid = npv_at_rate(mid)
        
        if abs(f_mid) < tol or abs(high - low) < tol:
            return mid
            
        if f_low * f_mid < 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return 0.5 * (low + high)

