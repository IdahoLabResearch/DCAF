# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Financial building blocks used by project and stream models.

Escalation and spend-curve helpers are re-exported here for convenience.
Other submodules (amortization, construction, opex, outage) should be imported
directly to avoid circular dependencies with ``dcaf.streams``::

    from dcaf.finance.amortization import AmortizationSchedule
    from dcaf.finance.construction import ConstructionSpendBuilder
    from dcaf.finance.opex import fixed_opex
    from dcaf.finance.outage import generator_outage, construction_outage
"""

from dcaf.finance._spend_curves import get_spend_profile, get_spend_profiles
from dcaf.finance.escalation import (
    CompositeEscalation,
    ConstantRateEscalation,
    EscalationBuilder,
    EscalationPolicy,
    EscalationSegment,
    IndexSeriesEscalation,
)

__all__ = [
    "CompositeEscalation",
    "ConstantRateEscalation",
    "EscalationBuilder",
    "EscalationPolicy",
    "EscalationSegment",
    "IndexSeriesEscalation",
    "get_spend_profile",
    "get_spend_profiles",
]
