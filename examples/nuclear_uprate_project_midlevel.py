"""Mid-level DCAF analysis for a nuclear plant uprate.

This example assembles the same uprate scenario as ``nuclear_uprate_project.py``
using mid-level building blocks instead of the top-level ``EnergyProject`` builder.
Each financial sub-system is configured independently:

- ``construction_spend_schedule`` controls spend timing profile and construction-period debt
- ``AmortizationSchedule.build`` produces a post-COD debt service schedule
- ``GenerationStream.from_capacity`` builds the generation profile
- ``construction_outage`` produces lost-revenue and replacement-cost cashflows for
  each refueling outage extension during construction
- ``macrs_schedule`` generates the depreciation deduction stream
- ``ptc`` converts generation to Production Tax Credit cashflows
- ``compute_taxable_income`` / ``tax_liability`` assembles and taxes net income
- ``CashFlowStream.from_streams`` merges all components for valuation

This level of the API is appropriate when scenario logic doesn't fit neatly into the
high-level builder — for example, when mixing external data with library-generated
schedules, or when fine-grained control over depreciation conventions or amortization
structure is required.
"""

from datetime import date

from dcaf.finance.amortization import AmortizationSchedule
from dcaf.finance.construction import ConstructionFinancing, construction_spend_schedule
from dcaf.finance.escalation import ConstantRateEscalation
from dcaf.finance.outage import construction_outage
from dcaf.streams.cashflows import CashFlowStream
from dcaf.streams.generation import GenerationStream
from dcaf.tax.depreciation import macrs_schedule
from dcaf.tax.incentives import ptc
from dcaf.tax.liability import compute_taxable_income, tax_liability


# ANALYSIS CONSTANTS
REFERENCE_DATE = date(2025, 1, 1)
CONSTRUCTION_START = date(2027, 1, 1)
OPERATIONS_START = date(2032, 1, 1)
OPERATIONS_END = date(2067, 1, 1)

UPRATE_CAPACITY_MW = 220.0
CAPACITY_FACTOR = 0.92
OPERATING_YEARS = 35

OVERNIGHT_CAPEX = 600_000_000.0
ESCALATION_RATE = 0.025
POWER_PRICE_PER_MWH = 45.0

DEBT_FRACTION = 0.50
COST_OF_DEBT = 0.10
COST_OF_EQUITY = 0.10
TAX_RATE = 0.21
DEBT_TERM_YEARS = 20

PTC_RATE_PER_MWH = 27.50
PTC_YEARS = 10

# CONSTRUCTION OUTAGE INPUTS — extensions to two refueling outages on the
# existing baseline plant during the EPU construction window.
BASELINE_CAPACITY_MW = 1000.0
BASELINE_CAPACITY_FACTOR = 0.92
OUTAGE_FIXED_COST = 500_000.0
OUTAGE_COST_PER_DAY = 50_000.0
OUTAGE_WINDOWS = (
    ("refueling_1", date(2028, 4, 1), date(2028, 4, 11)),
    ("refueling_2", date(2030, 10, 1), date(2030, 10, 11)),
)


# ESCALATION POLICY — shared across construction spend and revenue
escalation_policy = ConstantRateEscalation(
    reference_date=REFERENCE_DATE,
    rate=ESCALATION_RATE,
    period="year",
)

# CONSTRUCTION SPEND SCHEDULE
# Bell-curve spend profile; 50% of each draw is debt-funded with interest capitalized.
construction_financing = ConstructionFinancing.debt(
    debt_fraction=DEBT_FRACTION,
    interest_rate=COST_OF_DEBT,
    treatment="capitalize",
)
construction = construction_spend_schedule(
    total_cost=OVERNIGHT_CAPEX,
    start_date=CONSTRUCTION_START,
    end_date=OPERATIONS_START,
    period="year",
    profile="bell",
    financing=construction_financing,
    escalation_policy=escalation_policy,
)

# DEBT SERVICE (post-COD)
# Principal is estimated from overnight cost; capitalized interest increases
# the actual balance slightly, which is not modeled here for brevity.
debt_principal = OVERNIGHT_CAPEX * DEBT_FRACTION
debt_service = AmortizationSchedule.build(
    principal=debt_principal,
    annual_rate=COST_OF_DEBT,
    term=DEBT_TERM_YEARS,
    start_date=OPERATIONS_START,
    frequency="year",
)

# GENERATION
generation = GenerationStream.from_capacity(
    capacity_mw=UPRATE_CAPACITY_MW,
    capacity_factor=CAPACITY_FACTOR,
    start=OPERATIONS_START,
    periods=OPERATING_YEARS,
    frequency="year",
    label="Uprate Generation",
)

# REVENUE — escalated price applied to each generation entry
revenue = generation.to_revenue(
    price_per_mwh=POWER_PRICE_PER_MWH,
    label="Electricity Revenue",
    escalation_policy=escalation_policy,
)

# MACRS DEPRECIATION — 15-year property, half-year convention
depreciation = macrs_schedule(
    cost_basis=OVERNIGHT_CAPEX,
    placed_in_service=OPERATIONS_START,
    property_class=15,
    convention="half-year",
    label="MACRS Depreciation",
)

# CONSTRUCTION OUTAGES — one stream per refueling extension. The standalone
# helper returns a CashFlowStream containing distinct lost-revenue, fixed-cost,
# and per-day cost flows so each appears as a separate line item.
outage_streams = [
    construction_outage(
        capacity_mw=BASELINE_CAPACITY_MW,
        capacity_factor=BASELINE_CAPACITY_FACTOR,
        start=outage_start,
        end=outage_end,
        sell_price_per_unit=POWER_PRICE_PER_MWH,
        fixed_cost=OUTAGE_FIXED_COST,
        cost_per_day=OUTAGE_COST_PER_DAY,
        escalation_policy=escalation_policy,
        lost_revenue_label=f"{name} Lost Revenue",
        fixed_cost_label=f"{name} Mobilization",
        daily_cost_label=f"{name} Replacement Power",
    )
    for name, outage_start, outage_end in OUTAGE_WINDOWS
]
outage_impact = CashFlowStream.from_streams(*outage_streams)

# PRODUCTION TAX CREDIT
ptc_credits = ptc(
    generation_stream=generation,
    rate_per_mwh=PTC_RATE_PER_MWH,
    years=PTC_YEARS,
    escalation_policy=escalation_policy,
    label="PTC Credit",
)

# TAXES
# Taxable revenue = electricity revenue only; PTC is modeled as a tax credit
# cashflow, matching the ITC-style project-delta treatment.
# Deductions      = depreciation + interest payments + outage costs (deductible)
taxable_revenue = revenue
deductions = CashFlowStream.from_streams(depreciation, debt_service.interest, outage_impact)
taxable_income = compute_taxable_income(taxable_revenue, deductions)
taxes = tax_liability(taxable_income, tax_rate=TAX_RATE)

# ASSEMBLE ALL CASHFLOWS
cashflows = CashFlowStream.from_streams(
    construction,
    revenue,
    ptc_credits,
    debt_service.total,
    outage_impact,
    taxes,
)

# METRICS
valuation_date = CONSTRUCTION_START
discount_rate = COST_OF_EQUITY

total_generation = generation.sum()
discounted_generation = generation.discounted_sum(
    rate=discount_rate,
    valuation_date=valuation_date,
)
npv = cashflows.cash_only().npv(
    rate=discount_rate,
    valuation_date=valuation_date,
)
levelized_cost = (
    -cashflows.cash_only().outflows().npv(rate=discount_rate, valuation_date=valuation_date)
    / discounted_generation
    if discounted_generation > 0.0
    else None
)

print(f"Construction start:          {CONSTRUCTION_START.isoformat()}")
print(f"Operations start:            {OPERATIONS_START.isoformat()}")
print(f"Operations end:              {OPERATIONS_END.isoformat()}")
print(f"Discount rate:               {discount_rate:.4%}")
print()
print(f"Total generation (MWh):      {total_generation:,.0f}")
print(f"Construction cash flow ($):  {construction.sum():,.0f}")
print(f"Revenue cash flow ($):       {revenue.sum():,.0f}")
print(f"PTC cash flow ($):           {ptc_credits.sum():,.0f}")
print(f"Debt service cash flow ($):  {debt_service.total.sum():,.0f}")
print(f"Outage impact ($):           {outage_impact.sum():,.0f}")
print(f"Tax cash flow ($):           {taxes.sum():,.0f}")
print()
print(f"NPV ($):                     {npv:,.0f}")
print(f"Discounted generation (MWh): {discounted_generation:,.0f}")
print(
    f"Levelized cost ($/MWh):      {levelized_cost:,.2f}"
    if levelized_cost is not None
    else "Levelized cost ($/MWh):      n/a"
)
