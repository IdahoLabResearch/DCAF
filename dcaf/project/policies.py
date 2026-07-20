"""Public protocol for generation-linked project cash-flow policies."""

from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

from dcaf.streams.cashflows import CashFlowStream
from dcaf.streams.generation import GenerationStream


@runtime_checkable
class GenerationLinkedCashFlowPolicy(Protocol):
    """Protocol for custom policies that convert project generation to cashflows.

    Implement this protocol when a project component should derive cashflows
    from the compiled generation stream.
    """

    def cashflows(self, generation: GenerationStream) -> CashFlowStream:
        """Return cashflows produced from the compiled project generation stream.

        Parameters
        ----------
        generation : GenerationStream
            Generation stream produced by the project compiler.

        Returns
        -------
        CashFlowStream
            Cashflows to include as the registered project component.
        """
        ...


def coerce_generation_linked_policy(policy: object) -> GenerationLinkedCashFlowPolicy:
    """Validate and return a generation-linked cashflow policy.

    Parameters
    ----------
    policy : object
        Candidate policy object.

    Returns
    -------
    GenerationLinkedCashFlowPolicy
        The validated policy object.

    Raises
    ------
    TypeError
        If ``policy`` does not provide ``cashflows(generation)``.
    """
    if not isinstance(policy, GenerationLinkedCashFlowPolicy):
        raise TypeError("policy must provide cashflows(generation)")
    return cast(GenerationLinkedCashFlowPolicy, policy)


__all__ = ["GenerationLinkedCashFlowPolicy"]
