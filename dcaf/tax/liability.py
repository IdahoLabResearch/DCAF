"""Tax liability calculation functions.

This module provides functions for computing taxable income and generating tax liability
cashflow streams using scalar tax rates. It follows the DCAF pattern of simple, composable
generator functions that work with CashFlowStream objects.

Functions:
    compute_taxable_income: Calculate net taxable income from revenue and deductions
    tax_liability: Apply tax rate to taxable income to generate tax payment cashflows
"""

from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.shared.types import ProFormaCategory, TaxTreatment, normalize_cashflow_classification
from dcaf.shared.formatting import format_label
from dcaf.shared.time import period_end


def compute_taxable_income(
    revenue_stream: CashFlowStream,
    deductible_stream: CashFlowStream,
    label: str = "Taxable Income",
) -> CashFlowStream:
    """Compute net taxable income from revenue and deductible streams.

    This function combines revenue and deductible cashflow streams, groups them by year,
    and calculates the net taxable income for each period. The result is an accrual-based
    stream (is_cash=False) representing taxable income, not actual cash flows.

    Args:
        revenue_stream: Stream of taxable revenue cashflows.
        deductible_stream: Stream of tax-deductible cashflows.
        label: Label template for taxable income flows. Use {n} for sequential numbering.

    Returns:
        CashFlowStream of taxable income amounts (can be positive or negative for losses),
        with is_cash=False and dates set to period end (December 31 for annual).

    Example:
        >>> from datetime import date
        >>> from dcaf import CashFlowStream, CashFlow
        >>> revenue = CashFlowStream([
        ...     CashFlow(100_000, date(2025, 6, 1), "Revenue", tax_treatment="taxable")
        ... ])
        >>> deductions = CashFlowStream([
        ...     CashFlow(-20_000, date(2025, 3, 1), "Expense", tax_treatment="deductible")
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

    return CashFlowStream(
        [
            CashFlow(
                amount=net,
                date=period_end(period, "year"),
                label=format_label(label, period_num),
                is_cash=False,
                pro_forma_category=None,
                tax_treatment=TaxTreatment.NONE,
            )
            for period_num, (period, net) in enumerate(net_by_period.items(), start=1)
        ]
    )


def tax_liability(
    taxable_income_stream: CashFlowStream,
    tax_rate: float,
    label: str = "Tax Liability",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.TAX,
    tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
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
        pro_forma_category: Pro-forma category for tax-liability flows. Defaults to ``"tax"``.
        tax_treatment: Tax treatment for tax-liability flows. Defaults to ``"none"``.

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

    resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category,
        tax_treatment,
    )
    # Apply tax rate and convert to negative (outflow)
    tax_flows = CashFlowStream(
        [
            CashFlow(
                amount=cf.amount * tax_rate * -1,  # Negative = expense/outflow
                date=cf.date,
                label=format_label(label, i),
                is_cash=True,  # Tax payments are actual cash outflows
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
            for i, cf in enumerate(positive_income.entries, start=1)
        ]
    )

    return tax_flows
