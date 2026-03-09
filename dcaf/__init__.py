"""
DCAF - Discounted Cash Flow Analysis Framework

A Python package for financial modeling and cashflow analysis.
"""

from dcaf.amortization import (
    AmortizationBuilder,
    AmortizationSchedule,
)
from dcaf.cashflows import (
    CashFlow,
    CashFlowGroup,
    CashFlowStream,
    CashFlowTags,
    DayCountConvention,
)
from dcaf.depreciation import (
    MACRS_MID_QUARTER_RATES,
    MACRS_RATES,
    macrs_schedule,
)
from dcaf.generation import (
    Generation,
    GenerationGroup,
    GenerationStream,
)
from dcaf.opex import fixed_opex
from dcaf.types import MACRSConvention, MACRSPropertyClass


__all__ = [
    "AmortizationBuilder",
    "AmortizationSchedule",
    "CashFlow",
    "CashFlowGroup",
    "CashFlowStream",
    "CashFlowTags",
    "DayCountConvention",
    "Generation",
    "GenerationGroup",
    "GenerationStream",
    "MACRS_MID_QUARTER_RATES",
    "MACRS_RATES",
    "MACRSConvention",
    "fixed_opex",
    "MACRSPropertyClass",
    "macrs_schedule",
]
