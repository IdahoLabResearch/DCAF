"""
``dcaf.core.reporting``
"""

from typing import List
from .project import ProjectInputs, DCFResults


def print_pro_forma(
    inputs: ProjectInputs,
    results: DCFResults,
    year_labels: List[str] | None = None,
) -> None:
    """
    Print a detailed pro-forma income statement with tax calculation transparency.

    Includes:
    - Revenue, OPEX, Depreciation
    - EBIT, Interest, EBT
    - Tax liability with credits (PTC, AMC, ITC)
    - Net Income, Capex, Free Cash Flow
    - DSCR, NOL Carryforward
    - Cumulative Cash Flow
    """
    n = inputs.n_years
    if year_labels is None:
        year_labels = [f"Year {t}" for t in range(n)]

    # Apply cashflow mask to revenue/opex for display
    mask = inputs.cashflow_mask or [True] * n
    revenue = [inputs.revenue[t] if mask[t] else 0.0 for t in range(n)]
    opex = [inputs.opex[t] if mask[t] else 0.0 for t in range(n)]
    
    capex = inputs.capex[:]
    interest = inputs.interest_expense or [0.0] * n
    ptc = inputs.ptc_cashflow or [0.0] * n
    amc = inputs.advanced_manufacturing_credit or [0.0] * n
    tax_holidays = inputs.tax_holidays or [False] * n

    print("\n" + "=" * 100)
    print("PRO-FORMA INCOME STATEMENT & CASH FLOW")
    print("=" * 100)

    # Header
    header = f"{'Item':<30}"
    for label in year_labels:
        header += f"{label:>12}"
    print(header)
    print("-" * 100)

    # Revenue
    rev_line = f"{'Revenue':<30}"
    for val in revenue:
        rev_line += f"{val / 1e6:>11.1f}M"
    print(rev_line)

    # OPEX
    opex_line = f"{'Operating Expenses':<30}"
    for val in opex:
        opex_line += f"{val / 1e6:>11.1f}M"
    print(opex_line)

    # Depreciation
    dep_line = f"{'Depreciation':<30}"
    for val in results.depreciation:
        dep_line += f"{val / 1e6:>11.1f}M"
    print(dep_line)

    print("-" * 100)

    # EBIT
    ebit_line = f"{'EBIT':<30}"
    for val in results.ebit:
        ebit_line += f"{val / 1e6:>11.1f}M"
    print(ebit_line)

    # Interest Expense
    int_line = f"{'Interest Expense':<30}"
    for val in interest:
        int_line += f"{val / 1e6:>11.1f}M"
    print(int_line)

    print("-" * 100)

    # EBT
    ebt_line = f"{'Earnings Before Tax (EBT)':<30}"
    for val in results.ebt:
        ebt_line += f"{val / 1e6:>11.1f}M"
    print(ebt_line)

    # Tax Liability (after credits)
    tax_line = f"{'Tax Liability':<30}"
    for val in results.tax_liability:
        tax_line += f"{val / 1e6:>11.1f}M"
    print(tax_line)

    # Show tax credits applied
    if any(ptc):
        ptc_line = f"{'  (PTC Credit Applied)':<30}"
        for val in ptc:
            ptc_line += f"{val / 1e6:>11.1f}M"
        print(ptc_line)
    
    if any(amc):
        amc_line = f"{'  (45X AMC Applied)':<30}"
        for val in amc:
            amc_line += f"{val / 1e6:>11.1f}M"
        print(amc_line)

    if inputs.itc_amount > 0:
        itc_line = f"{'  (ITC Applied in Year {inputs.itc_year})':<30}"
        for t in range(n):
            val = inputs.itc_amount if t == inputs.itc_year else 0.0
            itc_line += f"{val / 1e6:>11.1f}M"
        print(itc_line)

    # Tax holidays
    holiday_line = f"{'  Tax Holiday Active?':<30}"
    for flag in tax_holidays:
        holiday_line += f"{'Yes':>12}" if flag else f"{'No':>12}"
    print(holiday_line)

    print("-" * 100)

    # Net Income
    ni_line = f"{'Net Income':<30}"
    for val in results.net_income:
        ni_line += f"{val / 1e6:>11.1f}M"
    print(ni_line)

    # Add back depreciation (non-cash)
    print()
    dep_addback_line = f"{'+ Depreciation (non-cash)':<30}"
    for val in results.depreciation:
        dep_addback_line += f"{val / 1e6:>11.1f}M"
    print(dep_addback_line)

    # Subtract capex
    capex_line = f"{'- Capital Expenditures':<30}"
    for val in capex:
        capex_line += f"{val / 1e6:>11.1f}M"
    print(capex_line)

    print("=" * 100)

    # Free Cash Flow
    fcf_line = f"{'FREE CASH FLOW':<30}"
    for val in results.free_cash_flow:
        fcf_line += f"{val / 1e6:>11.1f}M"
    print(fcf_line)

    # Cumulative Cash Flow
    cum_line = f"{'Cumulative Cash Flow':<30}"
    for val in results.cumulative_cash_flow:
        cum_line += f"{val / 1e6:>11.1f}M"
    print(cum_line)

    print("=" * 100)

    # NOL Carryforward
    nol_line = f"{'NOL Carryforward':<30}"
    for val in results.nol_carryforward:
        nol_line += f"{val / 1e6:>11.1f}M"
    print(nol_line)

    print("=" * 100)

    # Summary Metrics
    print("\nSUMMARY METRICS:")
    print(f"  NPV (at discount rate): ${results.npv / 1e6:,.1f}M")
    print(f"  IRR: {results.irr * 100:.2f}%")
    if results.payback_year is not None:
        print(f"  Payback Period: {results.payback_year} years")