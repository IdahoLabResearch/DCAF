"""Project configuration value objects."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite


def _validate_finite(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is not finite (inf or NaN)."""
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class CapitalStructure:
    """Capital structure assumptions used for project-level discounting.

    Parameters
    ----------
    debt_fraction : float
        Debt share of total capital. Fractions must sum to ``1.0``.
    cost_of_debt : float
        Nominal cost of debt.
    equity_fraction : float
        Equity share of total capital. Fractions must sum to ``1.0``.
    cost_of_equity : float
        Nominal cost of equity.
    tax_rate : float, optional
        Tax rate used to compute the after-tax debt component of WACC. If not
        provided here, the builder will use the project tax rate when available.
    """

    debt_fraction: float
    cost_of_debt: float
    equity_fraction: float
    cost_of_equity: float
    tax_rate: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("debt_fraction", self.debt_fraction),
            ("cost_of_debt", self.cost_of_debt),
            ("equity_fraction", self.equity_fraction),
            ("cost_of_equity", self.cost_of_equity),
        ):
            _validate_finite(value, name)
        if self.tax_rate is not None:
            _validate_finite(self.tax_rate, "tax_rate")
        if self.debt_fraction < 0.0 or self.equity_fraction < 0.0:
            raise ValueError("capital structure fractions must be non-negative")
        total_fraction = self.debt_fraction + self.equity_fraction
        if not isclose(total_fraction, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("capital structure fractions must sum to 1.0")

    @property
    def coe(self) -> float:
        """Return the configured cost of equity.

        Returns
        -------
        float
            Equity cost stored on the capital structure.
        """
        return self.cost_of_equity

    @property
    def wacc(self) -> float:
        """Return the weighted average cost of capital.

        Returns
        -------
        float
            Weighted average cost of capital using the after-tax debt
            component.

        Raises
        ------
        ValueError
            If ``tax_rate`` is not configured.
        """
        if self.tax_rate is None:
            raise ValueError("tax_rate is required to compute wacc")
        return (
            self.cost_of_equity * self.equity_fraction
            + self.cost_of_debt * self.debt_fraction * (1.0 - self.tax_rate)
        )

    @property
    def discount_rate(self) -> float:
        """Return the backward-compatible discount-rate alias.

        Returns
        -------
        float
            The same value returned by :attr:`wacc`.

        Raises
        ------
        ValueError
            If ``tax_rate`` is not configured.
        """
        return self.wacc


__all__ = ["CapitalStructure"]
