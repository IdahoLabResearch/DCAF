# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Example ``EnergyProject`` analysis for a nuclear plant uprate.

This example models an uprate project that adds 220 MW of generating capacity
to an existing nuclear plant. It uses the high-level ``EnergyProject`` builder
to assemble construction spending, generation, market revenue, tax credits, and
financing into a single analysis.

Stated inputs from the example brief
------------------------------------
- Construction starts on 2027-01-01 and lasts 5 years.
- The uprate adds 220 MW of capacity.
- Total project lifespan is 40 years from construction start.
- Overnight capital cost is $600 million.
- Financing is 50% debt / 50% equity.
- Both cost of debt and cost of equity are 10%.
- Electricity sells for $45/MWh in 2025 dollars.
- Costs and revenues escalate uniformly at 2.5% annually.
- The project claims PTC credits.

Additional modeling assumptions
-------------------------------
- Capacity factor is assumed to be 92%, representative of a high-performing
  nuclear asset.
- The PTC is modeled using an illustrative base value of $27.50/MWh for the
  first 10 operating years, stated in 2025 dollars.
- A 21% tax rate is included for taxes.
- WACC is computed from the financing inputs and used as the discount rate for
  metrics. It is not part of the project configuration itself.
- Two refueling outages on the existing 1000 MW baseline plant are extended
  by 10 days each to perform uprate work. Each extension incurs lost revenue
  at the baseline capacity, a fixed mobilization cost, and per-day replacement
  power costs. These appear as distinct line items via
  :meth:`EnergyProject.construction_outage`.
"""

from datetime import date

from dcaf import EnergyProject


# USER DEFINED ANALYSIS CONSTANTS
REFERENCE_DATE = date(2025, 1, 1)
CONSTRUCTION_START = date(2027, 1, 1)
OPERATIONS_START = date(2032, 1, 1)
OPERATIONS_END = date(2067, 1, 1)

UPRATE_CAPACITY_MW = 220.0
CAPACITY_FACTOR = 0.92

OVERNIGHT_CAPEX = 600_000_000.0
POWER_PRICE_PER_MWH = 45.0
ESCALATION_RATE = 0.025

DEBT_FRACTION = 0.50
EQUITY_FRACTION = 0.50
COST_OF_DEBT = 0.10
COST_OF_EQUITY = 0.10
TAX_RATE = 0.21

PTC_RATE_PER_MWH = 27.50
PTC_YEARS = 10
DEBT_TERM_YEARS = 20

# CONSTRUCTION OUTAGE INPUTS — extensions to two refueling outages on the
# existing baseline plant during the EPU construction window.
BASELINE_CAPACITY_MW = 1000.0
BASELINE_CAPACITY_FACTOR = 0.92
OUTAGE_FIXED_COST = 500_000.0  # mobilization / craft labor surge
OUTAGE_COST_PER_DAY = 50_000.0  # replacement-power premium beyond lost revenue


# PROJECT DEFINITION
project = (
    EnergyProject()
    .default_escalation(
        rate=ESCALATION_RATE,
        amount_reference_date=REFERENCE_DATE,
    )
    .generation(
        capacity_mw=UPRATE_CAPACITY_MW,
        capacity_factor=CAPACITY_FACTOR,
        operations_start=OPERATIONS_START,
        operations_end=OPERATIONS_END,
        label="Uprate Generation",
    )
    .construction(
        overnight_cost=OVERNIGHT_CAPEX,
        spend_profile="flat",
        construction_start=CONSTRUCTION_START,
        period="year",
    )
    .construction_financing(
        debt_fraction=DEBT_FRACTION,
        construction_interest_rate=COST_OF_DEBT,
        amortization_rate=COST_OF_DEBT,
        amortization_term=DEBT_TERM_YEARS,
    )
    .tax(rate=TAX_RATE)
    .generation_revenue(
        price=POWER_PRICE_PER_MWH,
        label="Electricity Revenue",
    )
    .production_tax_credit(
        rate_per_unit=PTC_RATE_PER_MWH,
        years=PTC_YEARS,
        label="PTC Credit",
    )
    .construction_outage(
        name="refueling_1",
        start=date(2028, 4, 1),
        end=date(2028, 4, 11),
        capacity_mw=BASELINE_CAPACITY_MW,
        capacity_factor=BASELINE_CAPACITY_FACTOR,
        fixed_cost=OUTAGE_FIXED_COST,
        cost_per_day=OUTAGE_COST_PER_DAY,
        lost_revenue_label="Refuel #1 Lost Revenue",
        fixed_cost_label="Refuel #1 Mobilization",
        daily_cost_label="Refuel #1 Replacement Power",
    )
    .construction_outage(
        name="refueling_2",
        start=date(2030, 10, 1),
        end=date(2030, 10, 11),
        capacity_mw=BASELINE_CAPACITY_MW,
        capacity_factor=BASELINE_CAPACITY_FACTOR,
        fixed_cost=OUTAGE_FIXED_COST,
        cost_per_day=OUTAGE_COST_PER_DAY,
        lost_revenue_label="Refuel #2 Lost Revenue",
        fixed_cost_label="Refuel #2 Mobilization",
        daily_cost_label="Refuel #2 Replacement Power",
    )
)

# run the analysis and generate an annual pro-forma
analysis = project.analyze()
pro_forma = analysis.pro_forma(period="year")

discount_rate = (
    analysis.valuation.discount_rate if analysis.valuation is not None else COST_OF_EQUITY
)
valuation_date = CONSTRUCTION_START
# TODO: wrap LCOE calculation to get similar ergonomics as NPV and IRR
cashflows = analysis.cashflows.cash_only()
generation = analysis.generation
discounted_generation = generation.discounted_sum(
    rate=discount_rate,
    valuation_date=valuation_date,
)
npv = cashflows.npv(
    rate=discount_rate,
    valuation_date=valuation_date,
)
levelized_cost = (
    -cashflows.outflows().npv(
        rate=discount_rate,
        valuation_date=valuation_date,
    )
    / discounted_generation
    if discounted_generation > 0.0
    else None
)

print(f"Construction start: {CONSTRUCTION_START.isoformat()}")
print(f"Operations start:   {OPERATIONS_START.isoformat()}")
print(f"Operations end:     {OPERATIONS_END.isoformat()}")
print(f"Operating years:    {analysis.timeline.operating_years:.2f}")
print(f"Discount rate:      {discount_rate:.4%}")
print()
print(f"Total generation (MWh):      {analysis.generation.sum():,.0f}")
print(f"Construction cash flow ($):  {analysis.cashflow_components['construction'].sum():,.0f}")
print(f"Revenue cash flow ($):       {analysis.cashflow_components['revenue'].sum():,.0f}")
print(f"PTC cash flow ($):           {analysis.cashflow_components['ptc'].sum():,.0f}")
print(f"Debt service cash flow ($):  {analysis.cashflow_components['debt_service'].sum():,.0f}")
for outage_name in ("refueling_1", "refueling_2"):
    component = analysis.cashflow_components[f"construction_outage:{outage_name}"]
    print(f"Outage impact ({outage_name}) ($):  {component.sum():,.0f}")
print()
print(f"NPV ($):                     {npv:,.0f}")
print(f"Discounted generation (MWh): {discounted_generation:,.0f}")
print(
    f"Levelized cost ($/MWh):     {levelized_cost:,.2f}"
    if levelized_cost is not None
    else "Levelized cost ($/MWh):     n/a"
)
