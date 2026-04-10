"""Levelized cost of energy (LCOE) calculation."""

from __future__ import annotations

from datetime import date
from math import isclose
from typing import Callable

from dcaf.shared.types import DayCountConvention, ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlowGroup, CashFlowStream
from dcaf.tax.liability import compute_taxable_income, tax_liability


def lcoe(
    basis_stream: CashFlowStream,
    component_streams: CashFlowGroup[str],
    replaceable_revenue_names: set[str],
    tax_rate: float | None,
    discount_rate: float,
    valuation_date: date,
    convention: DayCountConvention = "actual/365",
) -> float | None:
    """Solve for the levelized cost of energy.

    Finds the electricity price ($/MWh) at which the project NPV equals
    zero by bisection over the objective
    ``NPV(costs + price × basis_stream − recomputed_taxes) = 0``.

    Parameters
    ----------
    basis_stream : CashFlowStream
        Unit-price (``$1/MWh``) revenue basis stream under the chosen
        escalation policy.
    component_streams : CashFlowGroup[str]
        All named cashflow components of the project.
    replaceable_revenue_names : set[str]
        Component names whose revenue is replaced by the levelized
        price stream during the solve.
    tax_rate : float or None
        Project tax rate for recomputing taxes at each trial price.
    discount_rate : float
        Annual discount rate for NPV evaluation.
    valuation_date : date
        Reference date for discounting.
    convention : DayCountConvention, optional
        Day-count convention. Default is ``"actual/365"``.

    Returns
    -------
    float or None
        Levelized cost in $/MWh, or ``None`` when the basis stream is
        empty or the objective is not monotonically increasing in price.
    """
    if not basis_stream.entries:
        return None

    def objective(price: float) -> float:
        return _lcoe_objective(
            price=price,
            basis_stream=basis_stream,
            component_streams=component_streams,
            replaceable_revenue_names=replaceable_revenue_names,
            tax_rate=tax_rate,
            discount_rate=discount_rate,
            valuation_date=valuation_date,
            convention=convention,
        )

    return _solve_levelized_cost(objective)


def _solve_levelized_cost(objective: Callable[[float], float]) -> float | None:
    """Bracket the root of *objective* then refine with Brent's method.

    Probes ``price=0`` and ``price=1`` to verify the objective is
    increasing, expands the bracket until a sign change is found, then
    calls :func:`brent_root` for superlinear convergence.

    Returns ``None`` if the objective is not monotonically increasing
    or the bracket cannot be found within 100 doublings.
    """
    value_at_zero = objective(0.0)
    if isclose(value_at_zero, 0.0, abs_tol=1e-9):
        return 0.0

    value_at_one = objective(1.0)
    if not value_at_one > value_at_zero:
        return None

    if value_at_zero < 0.0:
        a = 0.0
        fa = value_at_zero
        b = max(1.0, -value_at_zero / max(value_at_one - value_at_zero, 1e-12))
        fb = objective(b)
        for _ in range(100):
            if fb >= 0.0:
                break
            a = b
            fa = fb
            b *= 2.0
            fb = objective(b)
        else:
            return None
    else:
        b = 0.0
        fb = value_at_zero
        a = min(-1.0, -value_at_zero / max(value_at_one - value_at_zero, 1e-12))
        fa = objective(a)
        for _ in range(100):
            if fa <= 0.0:
                break
            b = a
            fb = fa
            a *= 2.0
            fa = objective(a)
        else:
            return None

    return brent_root(objective, a, b, fa, fb)


def brent_root(
    func: Callable[[float], float],
    a: float,
    b: float,
    fa: float | None = None,
    fb: float | None = None,
    *,
    xtol: float = 1e-12,
    ftol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Find a root of *func* in the bracket ``[a, b]`` using Brent's method.

    Combines inverse quadratic interpolation, the secant method, and
    bisection to achieve superlinear convergence while maintaining the
    guaranteed convergence of bisection.

    Parameters
    ----------
    func : Callable[[float], float]
        Continuous scalar function whose root is sought.
    a, b : float
        Bracket endpoints. ``func(a)`` and ``func(b)`` must have
        opposite signs.
    fa, fb : float or None, optional
        Pre-computed values of ``func(a)`` and ``func(b)``.  Computed
        from *func* when ``None``.
    xtol : float, optional
        Absolute tolerance on the bracket width. Iteration stops when
        ``|b − a| < xtol``. Default is ``1e-12``.
    ftol : float, optional
        Absolute tolerance on the function value. Iteration stops when
        ``|func(x)| < ftol``. Default is ``1e-6``.
    max_iter : int, optional
        Maximum number of iterations. Default is ``100``.

    Returns
    -------
    float
        Approximate root of *func*.

    Raises
    ------
    ValueError
        If ``func(a)`` and ``func(b)`` do not have opposite signs.
    """
    if fa is None:
        fa = func(a)
    if fb is None:
        fb = func(b)
    if fa * fb > 0.0:
        raise ValueError(
            f"func(a) and func(b) must have opposite signs, got f({a})={fa}, f({b})={fb}"
        )

    # Ensure |f(a)| >= |f(b)| so that b is always the best guess.
    if abs(fa) < abs(fb):
        a, b = b, a
        fa, fb = fb, fa

    c, fc = a, fa
    d = b - a
    used_bisection = True

    for _ in range(max_iter):
        if abs(fb) < ftol:
            return b
        if isclose(a, b, rel_tol=xtol, abs_tol=xtol):
            return b

        # Try interpolation.
        if fa != fc and fb != fc:
            # Inverse quadratic interpolation — three distinct f-values.
            s = (
                a * fb * fc / ((fa - fb) * (fa - fc))
                + b * fa * fc / ((fb - fa) * (fb - fc))
                + c * fa * fb / ((fc - fa) * (fc - fb))
            )
        else:
            # Secant method.
            s = b - fb * (b - a) / (fb - fa)

        # Acceptance conditions (Brent's criteria): reject interpolation
        # and fall back to bisection if s is outside the interval
        # ((3a + b) / 4, b) or the step is not shrinking fast enough.
        midpoint = (a + b) / 2.0
        cond1 = not ((min((3 * a + b) / 4, b)) <= s <= (max((3 * a + b) / 4, b)))
        cond2 = used_bisection and abs(s - b) >= abs(b - c) / 2
        cond3 = not used_bisection and abs(s - b) >= abs(c - d) / 2
        cond4 = used_bisection and isclose(b, c, rel_tol=xtol, abs_tol=xtol)
        cond5 = not used_bisection and isclose(c, d, rel_tol=xtol, abs_tol=xtol)

        if cond1 or cond2 or cond3 or cond4 or cond5:
            s = midpoint
            used_bisection = True
        else:
            used_bisection = False

        fs = func(s)

        d = c
        c, fc = b, fb

        if fa * fs < 0.0:
            b, fb = s, fs
        else:
            a, fa = s, fs

        # Keep |f(a)| >= |f(b)|.
        if abs(fa) < abs(fb):
            a, b = b, a
            fa, fb = fb, fa

    return b


def _lcoe_objective(
    *,
    price: float,
    basis_stream: CashFlowStream,
    component_streams: CashFlowGroup[str],
    replaceable_revenue_names: set[str],
    tax_rate: float | None,
    discount_rate: float,
    valuation_date: date,
    convention: DayCountConvention,
) -> float:
    """Evaluate project NPV at a trial electricity *price*.

    Replaces revenue components with ``price × basis_stream``,
    recomputes taxes, and returns the NPV of the resulting total stream.
    """
    _LCOE_CATEGORIES = frozenset(
        {
            ProFormaCategory.CAPITAL_COST,
            ProFormaCategory.OPERATING_COST,
            ProFormaCategory.TAX,
            ProFormaCategory.TAX_CREDIT,
            ProFormaCategory.DEPRECIATION,
        }
    )

    filtered: dict[str, CashFlowStream] = {}
    for name, stream in component_streams.items():
        if name in replaceable_revenue_names or name == "project:tax_liability":
            continue
        included = stream.filter(lambda cf: cf.pro_forma_category in _LCOE_CATEGORIES)
        if included.entries:
            filtered[name] = included
    filtered["project:levelized_revenue"] = basis_stream.scale(price)
    taxes = _recomputed_taxes(filtered, tax_rate)
    total_stream = CashFlowStream.from_streams(*filtered.values(), taxes)
    return total_stream.cash_only().npv(
        rate=discount_rate,
        valuation_date=valuation_date,
        convention=convention,
    )


def _recomputed_taxes(
    component_streams: dict[str, CashFlowStream],
    tax_rate: float | None,
) -> CashFlowStream:
    """Recompute tax liability from *component_streams* at *tax_rate*."""
    if tax_rate is None:
        return CashFlowStream()

    taxable_components: list[CashFlowStream] = []
    deductible_components: list[CashFlowStream] = []
    for stream in component_streams.values():
        if not stream.entries:
            continue
        taxable = stream.filter(tax_treatment=TaxTreatment.TAXABLE)
        deductible = stream.filter(tax_treatment=TaxTreatment.DEDUCTIBLE)
        if taxable.entries:
            taxable_components.append(taxable)
        if deductible.entries:
            deductible_components.append(deductible)

    taxable_income = compute_taxable_income(
        CashFlowStream.from_streams(*taxable_components),
        CashFlowStream.from_streams(*deductible_components),
    )
    return tax_liability(taxable_income, tax_rate=tax_rate, allow_refund=True)
