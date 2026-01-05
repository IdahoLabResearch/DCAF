"""
``dcaf.core.project``
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ProjectInputs:
    """
    Container for project economic inputs on an annual basis.

    All list-valued fields must have length `n_years`.

    Required:
    ---------
    n_years : int
    revenue : List[float]
        Gross revenues (e.g. from regulated rates, PPA, etc.).
    opex : List[float]
        Operating expenditures (excluding depreciation & interest).
    capex : List[float]
        Capital expenditures (as cash outflows).
    tax_rate : float
        Marginal corporate tax rate (0-1).
    depreciable_basis : float
        Total basis subject to depreciation.
    depreciation_schedule : Dict[int, float]
        Maps year index → fraction of depreciable_basis.

    Optional:
    ---------
    salvage_value : float
        Residual value realized in the final year (after tax effects ignored here).
    interest_expense : Optional[List[float]]
        Interest expense per year; if None, assumed zero.
    tax_holidays : Optional[List[bool]]
        If True in year t, no tax is charged that year.
    cashflow_mask : Optional[List[bool]]
        If False in year t, revenue and opex are zeroed (e.g., outages).
    ptc_cashflow : Optional[List[float]]
        Production tax credits by year, treated as offset to tax liability.
    itc_amount : float
        Investment tax credit amount applied in year `itc_year`.
    itc_year : int
        Year index to apply ITC.
    advanced_manufacturing_credit : Optional[List[float]]
        Section 45X credit (1.3¢/kWh for nuclear).
    bonus_depreciation_rate : float
        First-year bonus depreciation (0.0 to 1.0).
    fuel_cycle_cost : Optional[List[float]]
        Nuclear fuel cycle costs.
    nrc_fees : Optional[List[float]]
        NRC licensing and inspection fees.
    insurance_cost : Optional[List[float]]
        Nuclear liability and property insurance.
    decommissioning_contribution : Optional[List[float]]
        Annual decommissioning fund contributions.
    principal_payment : Optional[List[float]]
        Principal repayment schedule.
    regulatory_disallowance_rate : float
        Percentage of capex potentially disallowed (0-1).
    """

    n_years: int

    revenue: List[float]
    opex: List[float]
    capex: List[float]

    tax_rate: float
    depreciable_basis: float
    depreciation_schedule: Dict[int, float]

    salvage_value: float = 0.0

    interest_expense: Optional[List[float]] = None
    tax_holidays: Optional[List[bool]] = None
    cashflow_mask: Optional[List[bool]] = None

    ptc_cashflow: Optional[List[float]] = None
    itc_amount: float = 0.0
    itc_year: int = 0

    # Advanced incentives
    advanced_manufacturing_credit: Optional[List[float]] = None
    bonus_depreciation_rate: float = 0.0

    # Nuclear-specific OPEX (optional detailed breakdown)
    fuel_cycle_cost: Optional[List[float]] = None
    nrc_fees: Optional[List[float]] = None
    insurance_cost: Optional[List[float]] = None
    decommissioning_contribution: Optional[List[float]] = None

    # Debt service and financing
    principal_payment: Optional[List[float]] = None

    # Regulatory and risk
    regulatory_disallowance_rate: float = 0.0

    def validate(self) -> None:
        """
        Validate consistency of list lengths.
        """
        n = self.n_years
        for name, arr in [
            ("revenue", self.revenue),
            ("opex", self.opex),
            ("capex", self.capex),
        ]:
            if len(arr) != n:
                raise ValueError(f"{name} must have length n_years={n}")

        if self.interest_expense is not None and len(self.interest_expense) != n:
            raise ValueError("interest_expense must have length n_years")

        if self.tax_holidays is not None and len(self.tax_holidays) != n:
            raise ValueError("tax_holidays must have length n_years")

        if self.cashflow_mask is not None and len(self.cashflow_mask) != n:
            raise ValueError("cashflow_mask must have length n_years")

        if self.ptc_cashflow is not None and len(self.ptc_cashflow) != n:
            raise ValueError("ptc_cashflow must have length n_years")

        if self.itc_year < 0 or self.itc_year >= n:
            raise ValueError("itc_year must be in [0, n_years-1]")

        # Validate new fields
        if (
            self.advanced_manufacturing_credit is not None
            and len(self.advanced_manufacturing_credit) != n
        ):
            raise ValueError("advanced_manufacturing_credit must have length n_years")

        if self.fuel_cycle_cost is not None and len(self.fuel_cycle_cost) != n:
            raise ValueError("fuel_cycle_cost must have length n_years")

        if self.nrc_fees is not None and len(self.nrc_fees) != n:
            raise ValueError("nrc_fees must have length n_years")

        if self.insurance_cost is not None and len(self.insurance_cost) != n:
            raise ValueError("insurance_cost must have length n_years")

        if (
            self.decommissioning_contribution is not None
            and len(self.decommissioning_contribution) != n
        ):
            raise ValueError("decommissioning_contribution must have length n_years")

        if self.principal_payment is not None and len(self.principal_payment) != n:
            raise ValueError("principal_payment must have length n_years")


@dataclass
class DCFResults:
    """
    Results from DCF evaluation with enhanced utility metrics.
    """

    free_cash_flow: List[float]
    depreciation: List[float]
    tax_liability: List[float]
    ebit: List[float]
    ebt: List[float]
    net_income: List[float]
    npv: float
    irr: Optional[float]

    # Enhanced utility metrics
    nol_carryforward: List[float]
    cumulative_cash_flow: List[float]
    payback_year: Optional[int]