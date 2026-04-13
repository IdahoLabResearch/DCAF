"""Project configuration value objects."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite


def _validate_finite(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is not finite (inf or NaN)."""
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


def wacc(
    *,
    debt_fraction: float,
    debt_cost: float,
    equity_cost: float,
    tax_rate: float,
    equity_fraction: float | None = None,
) -> float:
    """Compute the weighted average cost of capital (WACC).

    Uses the standard after-tax WACC formula:

    .. math::

        WACC = w_e \\cdot r_e + w_d \\cdot r_d \\cdot (1 - t)

    Parameters
    ----------
    debt_fraction : float
        Debt share of total capital. Must be non-negative and at most ``1.0``.
    debt_cost : float
        Nominal pre-tax cost of debt.
    equity_cost : float
        Nominal cost of equity.
    tax_rate : float
        Marginal tax rate applied to the debt interest tax shield.
    equity_fraction : float, optional
        Equity share of total capital. Defaults to ``1 - debt_fraction``.
        When provided, must equal ``1 - debt_fraction`` within a small tolerance.

    Returns
    -------
    float
        Computed WACC as a decimal (e.g. ``0.09`` for 9 %).

    Raises
    ------
    ValueError
        If any input is non-finite, either fraction is negative, or the
        fractions do not sum to ``1.0``.

    Examples
    --------
    >>> round(wacc(debt_fraction=0.4, debt_cost=0.08, equity_cost=0.12, tax_rate=0.21), 5)
    0.09728
    """
    resolved_equity = 1.0 - debt_fraction if equity_fraction is None else equity_fraction
    for name, value in (
        ("debt_fraction", debt_fraction),
        ("debt_cost", debt_cost),
        ("equity_fraction", resolved_equity),
        ("equity_cost", equity_cost),
        ("tax_rate", tax_rate),
    ):
        _validate_finite(value, name)
    if debt_fraction < 0.0 or resolved_equity < 0.0:
        raise ValueError("WACC fractions must be non-negative")
    if not isclose(debt_fraction + resolved_equity, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("WACC fractions must sum to 1.0")
    return equity_cost * resolved_equity + debt_cost * debt_fraction * (1.0 - tax_rate)


@dataclass(frozen=True)
class ProjectValuation:
    """Project-level default valuation configuration.

    Parameters
    ----------
    discount_rate : float
        Project-wide default discount rate used for NPV and LCOE calculations.
    """

    discount_rate: float

    def __post_init__(self) -> None:
        _validate_finite(self.discount_rate, "discount_rate")

    @classmethod
    def from_discount_rate(cls, rate: float) -> ProjectValuation:
        """Build a valuation config from an explicit discount rate."""
        return cls(discount_rate=rate)


__all__ = ["ProjectValuation", "wacc"]
