"""
Fixed operating expense cash flows.

Provides ``fixed_opex()`` as a thin facade over ``CashFlowStream.from_recurring()``.
For variable OPEX, use ``GenerationStream.to_cost()`` directly.
"""

from datetime import date

from .cashflows import CashFlowStream, CashFlowTags
from .types import Period

def fixed_opex(
    amount: float,
    start: date,
    periods: int,
    frequency: Period = "year",
    escalation: float = 0.0,
    label: str = "Fixed OPEX {n}",
    tags: frozenset[CashFlowTags] = frozenset(
        {CashFlowTags.EXPENSE, CashFlowTags.OPEX, CashFlowTags.TAX_DEDUCTIBLE}
    ),
) -> CashFlowStream:
    """
    Create a stream of recurring fixed operating expense cashflows.

    Parameters
    ----------
    amount : float
        Base cost for the first period (positive or negative; always stored as negative).
    start : date
        Date of the first cashflow.
    periods : int
        Number of cashflows to generate.
    frequency : Period
        Payment frequency. One of ``"day"``, ``"month"``, ``"quarter"``, ``"year"``.
        Default is ``"year"``.
    escalation : float
        Per-period compound escalation rate (e.g. ``0.025`` for 2.5%). Default is ``0.0``.
    label : str
        Label template; ``{n}`` is replaced with the 1-based period index.
        Default is ``"Fixed OPEX {n}"``.
    tags : frozenset[CashFlowTags]
        Tags applied to every cashflow. Default is ``{EXPENSE, OPEX, TAX_DEDUCTIBLE}``.

    Returns
    -------
    CashFlowStream
        Recurring negative cashflows representing the fixed OPEX cost.

    Examples
    --------
    >>> from datetime import date
    >>> from dcaf.opex import fixed_opex
    >>> stream = fixed_opex(amount=500_000, start=date(2025, 1, 1), periods=3, escalation=0.025)
    >>> [(f.date.year, f.amount) for f in stream.entries]
    [(2025, -500000.0), (2026, -512500.0), (2027, -525312.5)]
    """
    return CashFlowStream.from_recurring(
        amount=-abs(amount),
        start=start,
        periods=periods,
        frequency=frequency,
        escalation=escalation,
        label=label,
        tags=tags,
    )
