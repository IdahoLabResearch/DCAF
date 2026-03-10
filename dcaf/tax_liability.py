"""Tax liability calculation functions.

This module provides functions for computing taxable income and generating tax liability
cashflow streams using scalar tax rates. It follows the DCAF pattern of simple, composable
generator functions that work with CashFlowStream objects.

Functions:
    compute_taxable_income: Calculate net taxable income from revenue and deductions
    tax_liability: Apply tax rate to taxable income to generate tax payment cashflows
"""

from dcaf.cashflows import CashFlow, CashFlowStream, CashFlowTags
from dcaf.utils import period_end


def compute_taxable_income(
    revenue_stream: CashFlowStream,
    deductible_stream: CashFlowStream,
    label: str = "Taxable Income {n}",
) -> CashFlowStream:
    """Compute net taxable income from revenue and deductible streams.

    This function combines revenue and deductible cashflow streams, groups them by year,
    and calculates the net taxable income for each period. The result is an accrual-based
    stream (is_cash=False) representing taxable income, not actual cash flows.

    Args:
        revenue_stream: Stream of revenue cashflows (should have TAXABLE tag)
        deductible_stream: Stream of deductible cashflows (should have TAX_DEDUCTIBLE tag)
        label: Label template for taxable income flows. Use {n} for sequential numbering.

    Returns:
        CashFlowStream of taxable income amounts (can be positive or negative for losses),
        with is_cash=False and dates set to period end (December 31 for annual).

    Example:
        >>> from datetime import date
        >>> from dcaf import CashFlowStream, CashFlow, CashFlowTags
        >>> revenue = CashFlowStream([
        ...     CashFlow(100_000, date(2025, 6, 1), "Revenue", tags=frozenset({CashFlowTags.TAXABLE}))
        ... ])
        >>> deductions = CashFlowStream([
        ...     CashFlow(-20_000, date(2025, 3, 1), "Expense", tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}))
        ... ])
        >>> taxable_income = compute_taxable_income(revenue, deductions)
        >>> taxable_income[0].amount  # 100,000 + (-20,000) = 80,000
        80000.0
    """
    # Combine both streams
    combined = CashFlowStream.from_streams(revenue_stream, deductible_stream)

    # Return empty stream if no flows
    if not combined:
        return CashFlowStream()

    # Group by year and aggregate net amount per period
    grouped = combined.group_by(period="year")
    net_by_period = grouped.aggregate(lambda s: s.sum())

    return CashFlowStream([
        CashFlow(amount=net, date=period_end(period, "year"), label=label, is_cash=False, tags=frozenset())
        for period, net in net_by_period.items()
    ])


def tax_liability(
    taxable_income_stream: CashFlowStream,
    tax_rate: float,
    label: str = "Tax Liability {n}",
    tags: frozenset[CashFlowTags] = frozenset({CashFlowTags.EXPENSE}),
) -> CashFlowStream:
    """Apply scalar tax rate to taxable income to generate tax payment cashflows.

    This function takes a stream of taxable income amounts and applies a tax rate to
    generate tax liability cashflows. Only positive taxable income generates tax
    liability; negative amounts (losses) result in zero tax in this simple model.

    Args:
        taxable_income_stream: Stream of taxable income amounts from compute_taxable_income()
        tax_rate: Scalar tax rate to apply (e.g., 0.21 for 21% federal rate, or 0.26
                  for combined 21% federal + 5% state)
        label: Label template for tax liability flows. Use {n} for sequential numbering.
        tags: Tags to apply to tax liability flows. Defaults to {EXPENSE}.

    Returns:
        CashFlowStream of tax payment cashflows with negative amounts (outflows) and
        is_cash=True. Only positive taxable income generates tax liability.

    Note:
        This implementation does not model tax loss carryforwards or refunds.
        Negative taxable income (losses) produce no tax liability. This differs
        from the MPR tool, where a loss generates a tax credit (refund).

    Example:
        >>> from datetime import date
        >>> from dcaf import CashFlowStream, CashFlow
        >>> taxable_income = CashFlowStream([
        ...     CashFlow(100_000, date(2025, 1, 1), "Taxable Income", is_cash=False)
        ... ])
        >>> taxes = tax_liability(taxable_income, tax_rate=0.21)
        >>> taxes[0].amount  # -21,000 (negative = outflow)
        -21000.0
        >>> taxes[0].is_cash
        True
    """
    # Filter for positive taxable income only (negative = losses, no tax owed)
    positive_income = taxable_income_stream.inflows()

    # Return empty stream if no positive income
    if not positive_income:
        return CashFlowStream()

    # Apply tax rate and convert to negative (outflow)
    tax_flows = positive_income.apply(
        lambda cf: CashFlow(
            amount=cf.amount * tax_rate * -1,  # Negative = expense/outflow
            date=cf.date,
            label=label,
            is_cash=True,  # Tax payments are actual cash outflows
            tags=tags,
        )
    )

    return tax_flows
