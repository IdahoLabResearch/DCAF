"""Stream-oriented financial and generation primitives."""

from dcaf.streams.cashflows import CashFlow, CashFlowGroup, CashFlowStream
from dcaf.streams.generation import Generation, GenerationGroup, GenerationStream

__all__ = [
    "CashFlow",
    "CashFlowGroup",
    "CashFlowStream",
    "Generation",
    "GenerationGroup",
    "GenerationStream",
]
