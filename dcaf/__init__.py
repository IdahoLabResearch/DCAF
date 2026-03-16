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
from dcaf.construction import (
    ConstructionFinancing,
    ConstructionSpendBuilder,
    ConstructionSpendConfig,
    SpendProfile,
    construction_spend_schedule,
)
from dcaf.depreciation import (
    MACRS_MID_QUARTER_RATES,
    MACRS_RATES,
    macrs_schedule,
)
from dcaf.escalation import (
    CompositeEscalation,
    ConstantRateEscalation,
    EscalationBuilder,
    EscalationSegment,
    IndexSeriesEscalation,
)
from dcaf.generation import (
    Generation,
    GenerationGroup,
    GenerationStream,
)
from dcaf.opex import fixed_opex
from dcaf.tax_incentives import itc, itc_adjusted_basis, ptc
from dcaf.tax_liability import compute_taxable_income, tax_liability
from dcaf.spend_curves import (
    BELL_CURVE,
    FLAT_CURVE,
    LINEAR_CURVE,
    RAMPED_CURVE,
    TRIANGLE_CURVE,
)
from dcaf.types import (
    InterestTreatment,
    MACRSConvention,
    MACRSPropertyClass,
    Period,
    SpendSchedule,
    SpendScheduleName,
)


__all__ = [
    "AmortizationBuilder",
    "AmortizationSchedule",
    "CashFlow",
    "CashFlowGroup",
    "CashFlowStream",
    "CashFlowTags",
    "ConstructionFinancing",
    "ConstructionSpendBuilder",
    "ConstructionSpendConfig",
    "CompositeEscalation",
    "ConstantRateEscalation",
    "SpendProfile",
    "construction_spend_schedule",
    "DayCountConvention",
    "EscalationBuilder",
    "EscalationSegment",
    "BELL_CURVE",
    "FLAT_CURVE",
    "Generation",
    "GenerationGroup",
    "GenerationStream",
    "IndexSeriesEscalation",
    "InterestTreatment",
    "LINEAR_CURVE",
    "MACRS_MID_QUARTER_RATES",
    "MACRS_RATES",
    "MACRSConvention",
    "MACRSPropertyClass",
    "compute_taxable_income",
    "RAMPED_CURVE",
    "SpendScheduleName",
    "SpendSchedule",
    "TRIANGLE_CURVE",
    "fixed_opex",
    "itc",
    "itc_adjusted_basis",
    "macrs_schedule",
    "ptc",
    "Period",
    "tax_liability",
]
