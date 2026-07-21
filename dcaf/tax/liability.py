# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tax liability calculation functions.

This module provides functions for computing taxable income and generating tax liability
cashflow streams using scalar tax rates. It follows the DCAF pattern of simple, composable
generator functions that work with CashFlowStream objects.

Functions:
    compute_taxable_income: Calculate net taxable income from revenue and deductions
    tax_liability: Apply tax rate to taxable income to generate tax payment cashflows
"""

from dcaf.shared.time import period_end
from dcaf.shared.types import ProFormaCategory, TaxTreatment, normalize_cashflow_classification
from dcaf.shared.validation import validate_non_negative
from dcaf.streams.cashflows import CashFlow, CashFlowStream


def compute_taxable_income(
    revenue_stream: CashFlowStream, deductible_stream: CashFlowStream, label: str = "Taxable Income"
) -> CashFlowStream:
    """Compute net taxable income from revenue and deductible streams.

    This function combines revenue and deductible cashflow streams, groups them by year,
    and calculates the net taxable income for each period. The result is an accrual-based
    stream (is_cash=False) representing taxable income, not actual cash flows.

    Parameters
    ----------
    revenue_stream : CashFlowStream
        Stream of taxable revenue cashflows.
    deductible_stream : CashFlowStream
        Stream of tax-deductible cashflows.
    label : str, optional
        Label applied to every taxable income flow. Default is ``"Taxable Income"``.

    Returns
    -------
    CashFlowStream
        Taxable income amounts, which may be positive or negative, with
        ``is_cash=False`` and dates aligned to year end.

    Examples
    --------
        >>> from datetime import date
        >>> from dcaf.shared.types import TaxTreatment
        >>> from dcaf.streams import CashFlow, CashFlowStream
        >>> revenue = CashFlowStream([
        ...     CashFlow(100_000, date(2025, 6, 1), tax_treatment=TaxTreatment.TAXABLE),
        ...     CashFlow(120_000, date(2026, 6, 1), tax_treatment=TaxTreatment.TAXABLE),
        ... ])
        >>> deductions = CashFlowStream([
        ...     CashFlow(-20_000, date(2025, 3, 1), tax_treatment=TaxTreatment.DEDUCTIBLE),
        ...     CashFlow(-30_000, date(2026, 3, 1), tax_treatment=TaxTreatment.DEDUCTIBLE),
        ... ])
        >>> taxable_income = compute_taxable_income(revenue, deductions)
        >>> [(cf.date, cf.amount, cf.is_cash) for cf in taxable_income]
        [(datetime.date(2025, 12, 31), 80000.0, False), (datetime.date(2026, 12, 31), 90000.0, False)]
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
                label=label,
                is_cash=False,
                pro_forma_category=None,
                tax_treatment=TaxTreatment.NONE,
            )
            for period, net in net_by_period.items()
        ]
    )


def tax_liability(
    taxable_income_stream: CashFlowStream,
    tax_rate: float,
    label: str = "Tax Liability",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.TAX,
    tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
    allow_refund: bool = False,
) -> CashFlowStream:
    """Apply scalar tax rate to taxable income to generate tax payment cashflows.

    This function takes a stream of taxable income amounts and applies a tax rate to
    generate tax liability cashflows.

    Parameters
    ----------
    taxable_income_stream : CashFlowStream
        Stream of taxable income amounts from :func:`compute_taxable_income`.
    tax_rate : float
        Scalar tax rate (e.g. ``0.21`` for 21%).
    label : str, optional
        Label applied to every tax flow. Default is ``"Tax Liability"``.
    pro_forma_category : ProFormaCategory or str or None, optional
        Pro-forma category. Default is ``"tax"``.
    tax_treatment : TaxTreatment or str, optional
        Tax treatment. Default is ``"none"``.
    allow_refund : bool, optional
        When ``False`` (default), only positive taxable income generates a tax
        liability; losses produce zero tax. When ``True``, negative taxable
        income generates a positive cash flow (tax refund), enabling symmetric
        treatment for delta-to-baseline and levelized cost analyses.

    Returns
    -------
    CashFlowStream
        Tax payment cashflows (negative amounts for payments, positive for
        refunds when *allow_refund* is ``True``), with ``is_cash=True``.

    Notes
    -----
    This implementation does not model tax loss carryforwards or refunds.
    Negative taxable income (losses) produce no tax liability unless
    ``allow_refund=True``.

    Examples
    --------
        >>> from datetime import date
        >>> from dcaf.streams import CashFlow, CashFlowStream
        >>> taxable_income = CashFlowStream([
        ...     CashFlow(80_000, date(2025, 12, 31), "Taxable Income 1", is_cash=False),
        ...     CashFlow(90_000, date(2026, 12, 31), "Taxable Income 2", is_cash=False),
        ... ])
        >>> taxes = tax_liability(taxable_income, tax_rate=0.21)
        >>> [(cf.date, cf.amount) for cf in taxes]
        [(datetime.date(2025, 12, 31), -16800.0), (datetime.date(2026, 12, 31), -18900.0)]
    """
    validate_non_negative(tax_rate, "tax_rate")

    if allow_refund:
        income = taxable_income_stream
    else:
        income = taxable_income_stream.inflows()

    if not income:
        return CashFlowStream()

    resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category, tax_treatment
    )
    # Apply tax rate and convert to negative (outflow)
    tax_flows = CashFlowStream(
        [
            CashFlow(
                amount=cf.amount * tax_rate * -1,  # Negative = expense/outflow
                date=cf.date,
                label=label,
                is_cash=True,  # Tax payments are actual cash outflows
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
            for cf in income.entries
        ]
    )

    return tax_flows
