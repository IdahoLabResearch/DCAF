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
    vdb,
    vdb_schedule,
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
from dcaf.project import (
    CapitalStructure,
    EnergyProject,
    ProjectAnalysis,
    ProjectMetrics,
    ProjectProForma,
    ProjectProFormaRow,
    ProjectTimeline,
)
from dcaf.tax_incentives import itc, itc_adjusted_basis, ptc
from dcaf.tax_liability import compute_taxable_income, tax_liability
from dcaf.types import (
    InterestTreatment,
    MACRSConvention,
    MACRSPropertyClass,
    Period,
    ProFormaCategory,
    SpendSchedule,
    SpendScheduleName,
    TaxTreatment,
    VDBConvention,
)


__all__ = [
    "AmortizationBuilder",
    "AmortizationSchedule",
    "CashFlow",
    "CashFlowGroup",
    "CashFlowStream",
    "ConstructionFinancing",
    "ConstructionSpendBuilder",
    "ConstructionSpendConfig",
    "CompositeEscalation",
    "ConstantRateEscalation",
    "CapitalStructure",
    "SpendProfile",
    "construction_spend_schedule",
    "DayCountConvention",
    "EnergyProject",
    "EscalationBuilder",
    "EscalationSegment",
    "Generation",
    "GenerationGroup",
    "GenerationStream",
    "IndexSeriesEscalation",
    "InterestTreatment",
    "MACRS_MID_QUARTER_RATES",
    "MACRS_RATES",
    "MACRSConvention",
    "MACRSPropertyClass",
    "compute_taxable_income",
    "SpendScheduleName",
    "SpendSchedule",
    "fixed_opex",
    "itc",
    "itc_adjusted_basis",
    "macrs_schedule",
    "ptc",
    "Period",
    "ProFormaCategory",
    "ProjectAnalysis",
    "ProjectMetrics",
    "ProjectProForma",
    "ProjectProFormaRow",
    "ProjectTimeline",
    "TaxTreatment",
    "tax_liability",
    "vdb",
    "VDBConvention",
    "vdb_schedule",
]
