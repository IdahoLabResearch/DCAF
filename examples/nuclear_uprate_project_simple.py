"""Example ``EnergyProject`` analysis for a nuclear plant uprate (simplified).

This example uses much the same configuration as the example in nuclear_uprate_project.py.
However, this example showcases what a simpler analysis of the same system might look like.
These simplifications include:
- Overnight CAPEX cost instead of a multi-year construction spend schedule
- No additional label, carrier, or asset annotations
- No cost or revenue escalation
"""

from datetime import date

from dcaf import EnergyProject, wacc

START_DATE = date(2032, 1, 1)
END_DATE   = date(2066, 12, 31)


# PROJECT DEFINITION
project = (
    EnergyProject()
    .generation(
        capacity_mw=220,  # NOTE: capacity factor assumed to be 1.0 if not specified
        operations_start=START_DATE,
        operations_end=END_DATE,
    )
    .construction(overnight_cost=600e6)
    .tax(rate=0.21)
    .revenue_from_generation(sell_price_per_unit=45.00)
    .production_tax_credit(rate_per_unit=27.50)
)

# run the analysis and generate an annual pro-forma
analysis = project.analyze()
pro_forma = analysis.pro_forma(period="year")

discount_rate = 0.10
metrics = analysis.metrics(discount_rate=discount_rate, valuation_date=START_DATE)


print(f"Operations start:   {START_DATE.isoformat()}")
print(f"Operations end:     {END_DATE.isoformat()}")
print(f"Operating years:    {analysis.timeline.operating_years:.2f} (365-day convention)")
print(f"Discount rate:      {discount_rate:.4%}")
print()
print(f"Total generation (MWh):      {analysis.generation.sum():,.0f}")
print(f"Construction cash flow ($):  {analysis.cashflow_components['default:construction'].sum():,.0f}")
print(f"Revenue cash flow ($):       {analysis.cashflow_components['default:revenue'].sum():,.0f}")
print(f"PTC cash flow ($):           {analysis.cashflow_components['default:ptc'].sum():,.0f}")
print()
print(f"NPV ($):                 {metrics.npv:,.0f}")
print(f"Levelized cost ($/MWh):  {metrics.levelized_cost:,.2f}")
