"""Financial building blocks used by project and stream models."""

from dcaf.finance._spend_curves import get_spend_profile, get_spend_profiles
from dcaf.finance.amortization import AmortizationBuilder, AmortizationSchedule
from dcaf.finance.construction import (
    ConstructionFinancing,
    ConstructionSpendBuilder,
    ConstructionSpendConfig,
    SpendProfile,
    construction_spend_schedule,
)
from dcaf.finance.escalation import (
    CompositeEscalation,
    ConstantRateEscalation,
    EscalationBuilder,
    EscalationPolicy,
    EscalationSegment,
    IndexSeriesEscalation,
)
from dcaf.finance.opex import fixed_opex

__all__ = [
    "AmortizationBuilder",
    "AmortizationSchedule",
    "CompositeEscalation",
    "ConstantRateEscalation",
    "ConstructionFinancing",
    "ConstructionSpendBuilder",
    "ConstructionSpendConfig",
    "EscalationBuilder",
    "EscalationPolicy",
    "EscalationSegment",
    "IndexSeriesEscalation",
    "SpendProfile",
    "construction_spend_schedule",
    "fixed_opex",
    "get_spend_profile",
    "get_spend_profiles",
]
