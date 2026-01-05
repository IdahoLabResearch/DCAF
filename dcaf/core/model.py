"""
``dcaf.core.model``
"""

from __future__ import annotations
from typing import List, Sequence

from .project import ProjectInputs, DCFResults
from .depreciation import build_depreciation_series
from .metrics import npv, irr


class DCFModel:
    """
    DCF model for large-scale energy projects with utility-specific enhancements.

    Workflow:
    - Create ProjectInputs
    - Create DCFModel(inputs, discount_rate or wacc)
    - Call evaluate() to get DCFResults
    """

    def __init__(
        self,
        inputs: ProjectInputs,
        discount_rate: float | Sequence[float],
    ) -> None:
        inputs.validate()
        self.inputs = inputs
        self.discount_rate = discount_rate

    def _build_depreciation(self) -> List[float]:
        return build_depreciation_series(
            self.inputs.depreciable_basis,
            self.inputs.depreciation_schedule,
            self.inputs.n_years,
        )

    def _apply_mask(self, series: List[float]) -> List[float]:
        """
        Apply cashflow_mask to a series (for revenue/opex).
        If no mask, return a copy.
        """
        mask = self.inputs.cashflow_mask
        if mask is None:
            return series[:]
        if len(mask) != len(series):
            raise ValueError("cashflow_mask length mismatch.")
        return [val if mask[i] else 0.0 for i, val in enumerate(series)]

    def evaluate(self) -> DCFResults:
        """Evaluate DCF model with enhanced utility metrics (DSCR, NOL, etc.)."""
        n = self.inputs.n_years
        tax_rate = self.inputs.tax_rate

        # Apply mask to revenue and opex for outages, etc.
        revenue = self._apply_mask(self.inputs.revenue)
        opex = self._apply_mask(self.inputs.opex)

        # Handle capex with salvage value and regulatory disallowance
        capex = self.inputs.capex[:]
        if self.inputs.regulatory_disallowance_rate > 0:
            # Apply disallowance to construction capex (not maintenance)
            for t in range(n):
                if capex[t] > 100_000_000:  # Assume major capex > $100M
                    disallowed = capex[t] * self.inputs.regulatory_disallowance_rate
                    capex[t] += disallowed  # Increases cash outflow (not recovered)
        
        if self.inputs.salvage_value != 0.0:
            capex[-1] -= self.inputs.salvage_value  # salvage reduces net capex (cash inflow)

        interest = self.inputs.interest_expense or [0.0] * n
        if len(interest) != n:
            raise ValueError("interest_expense must be length n_years")

        tax_holidays = self.inputs.tax_holidays or [False] * n
        ptc = self.inputs.ptc_cashflow or [0.0] * n
        amc = self.inputs.advanced_manufacturing_credit or [0.0] * n  # Section 45X

        # Handle bonus depreciation (first-year expensing)
        depreciation = self._build_depreciation()
        if self.inputs.bonus_depreciation_rate > 0 and self.inputs.depreciable_basis > 0:
            bonus_amount = self.inputs.depreciable_basis * self.inputs.bonus_depreciation_rate
            # Add bonus to year 0, reduce regular MACRS proportionally
            depreciation[0] += bonus_amount
            reduction_factor = (1 - self.inputs.bonus_depreciation_rate)
            for t in range(n):
                if t > 0:
                    depreciation[t] *= reduction_factor

        # Pre-allocate result arrays for better performance
        ebit = [0.0] * n
        ebt = [0.0] * n
        tax_liability = [0.0] * n
        net_income = [0.0] * n
        free_cash_flow = [0.0] * n
        # dscr = [0.0] * n
        nol_carryforward = [0.0] * n

        itc_amount = self.inputs.itc_amount
        itc_year = self.inputs.itc_year
        
        nol_balance = 0.0  # Track NOL carryforward balance

        for t in range(n):
            # Earnings before interest and tax
            ebit_t = revenue[t] - opex[t] - depreciation[t]
            ebit[t] = ebit_t

            # Earnings before tax (EBT)
            ebt_t = ebit_t - interest[t]
            ebt[t] = ebt_t

            # NOL Carryforward logic
            if ebt_t < 0:
                # Loss this year, add to NOL carryforward
                nol_balance += abs(ebt_t)
                taxable_income = 0.0
            else:
                # Profit this year, use NOL to offset
                if nol_balance > 0:
                    nol_used = min(nol_balance, ebt_t * 0.80)  # Can offset up to 80% of income
                    taxable_income = ebt_t - nol_used
                    nol_balance -= nol_used
                else:
                    taxable_income = ebt_t
            
            nol_carryforward[t] = nol_balance

            # Base tax on taxable income
            tax_before_incentives = taxable_income * tax_rate

            # ITC applied in a specific year as a tax offset
            itc_in_year = itc_amount if t == itc_year else 0.0

            # Apply PTC, AMC (45X), and ITC as tax offsets
            tax_after_credits = max(tax_before_incentives - ptc[t] - amc[t] - itc_in_year, 0.0)

            # Tax holiday override
            tax_t = 0.0 if tax_holidays[t] else tax_after_credits
            tax_liability[t] = tax_t

            ni_t = ebt_t - tax_t
            net_income[t] = ni_t

            # Free cash flow to firm (FCFF)
            free_cash_flow[t] = ni_t + depreciation[t] - capex[t]
            
        # Calculate cumulative cash flow and payback
        cumulative_cash_flow = []
        cumsum = 0.0
        payback_year = None
        for t, fcf in enumerate(free_cash_flow):
            cumsum += fcf
            cumulative_cash_flow.append(cumsum)
            if payback_year is None and cumsum > 0:
                payback_year = t

        project_npv = npv(free_cash_flow, self.discount_rate)
        project_irr = irr(free_cash_flow)

        return DCFResults(
            free_cash_flow=free_cash_flow,
            depreciation=depreciation,
            tax_liability=tax_liability,
            ebit=ebit,
            ebt=ebt,
            net_income=net_income,
            npv=project_npv,
            irr=project_irr,
            nol_carryforward=nol_carryforward,
            cumulative_cash_flow=cumulative_cash_flow,
            payback_year=payback_year,
        )
