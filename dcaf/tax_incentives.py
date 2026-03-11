"""IRA tax incentive functions (Section 48E ITC).

This module provides functions for computing Investment Tax Credits (ITC) under the
Inflation Reduction Act. It follows the DCAF pattern of simple, composable generator
functions that work with CashFlowStream objects.

Functions:
    itc: Compute ITC credit cashflow from a CAPEX stream
    itc_adjusted_basis: Compute adjusted depreciable basis after ITC (IRS 50% basis-reduction rule)
"""

from datetime import date

from dcaf.cashflows import CashFlow, CashFlowStream, CashFlowTags


def itc(
    capex_stream: CashFlowStream,
    rate: float,
    placed_in_service: date,
    label: str = "ITC",
    tags: frozenset[CashFlowTags] = frozenset({CashFlowTags.REVENUE}),
) -> CashFlowStream:
    """Compute Investment Tax Credit (ITC) from a CAPEX stream.

    Sums total qualifying basis from capex_stream and applies the ITC rate to produce
    a single dollar-for-dollar tax credit cashflow dated at placed_in_service.

    The caller is responsible for assembling the complete CAPEX CashFlowStream (which
    may span multiple construction years) before calling this function. Multi-year
    construction is handled naturally by summing over the entire stream.

    Args:
        capex_stream: Stream of CAPEX cashflows (amounts stored as negatives; abs is taken)
        rate: ITC rate as a decimal (e.g., 0.30 for 30% Section 48E credit)
        placed_in_service: Date the asset is placed in service; the credit date
        label: Label for the resulting credit cashflow. Defaults to "ITC".
        tags: Tags to apply to the credit cashflow. Defaults to {REVENUE}.

    Returns:
        CashFlowStream containing a single ITC credit cashflow (positive amount, is_cash=True).
        Returns an empty stream if capex_stream is empty or rate is zero.

    Example:
        >>> from datetime import date
        >>> from dcaf import CashFlowStream, CashFlow, CashFlowTags
        >>> capex = CashFlowStream([
        ...     CashFlow(-10_000_000, date(2028, 6, 1), "Construction CAPEX",
        ...              tags=frozenset({CashFlowTags.CAPEX}))
        ... ])
        >>> credit = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))
        >>> credit[0].amount  # 10,000,000 * 0.30 = 3,000,000
        3000000.0
    """
    if not capex_stream.flows or rate == 0.0:
        return CashFlowStream()

    total_basis = abs(capex_stream.sum())
    credit_amount = total_basis * rate

    return CashFlowStream([
        CashFlow(
            amount=credit_amount,
            date=placed_in_service,
            label=label,
            is_cash=True,
            tags=tags,
        )
    ])


def itc_adjusted_basis(capex_stream: CashFlowStream, rate: float) -> float:
    """Compute adjusted depreciable basis after taking ITC.

    Applies the IRS 50% basis-reduction rule: the depreciable basis is reduced by
    half the ITC credit taken. This must be used for downstream MACRS calculations
    when ITC is claimed.

    Formula: total_basis × (1 - rate / 2)

    Source: https://www.law.cornell.edu/uscode/text/26/48E

    Args:
        capex_stream: Stream of CAPEX cashflows (amounts stored as negatives; abs is taken)
        rate: ITC rate as a decimal (e.g., 0.30 for 30%)

    Returns:
        Adjusted depreciable basis as a float. Returns 0.0 if capex_stream is empty.

    Example:
        >>> from datetime import date
        >>> from dcaf import CashFlowStream, CashFlow, CashFlowTags
        >>> capex = CashFlowStream([
        ...     CashFlow(-100_000_000, date(2028, 6, 1), "CAPEX",
        ...              tags=frozenset({CashFlowTags.CAPEX}))
        ... ])
        >>> itc_adjusted_basis(capex, rate=0.30)  # 100M * (1 - 0.15) = 85M
        85000000.0
    """
    if not capex_stream.flows:
        return 0.0

    total_basis = abs(capex_stream.sum())
    return total_basis * (1 - rate / 2)
