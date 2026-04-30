"""Builder-level DCAF analysis for a nuclear plant uprate.

This example demonstrates the customizable builder class APIs available in DCAF's
mid-level layer. It models the same nuclear uprate scenario as the other examples,
but highlights four areas where the builder APIs expose configuration that the
high-level ``EnergyProject`` interface does not:

**Custom construction spend profile**
    A three-phase piecewise profile is passed to ``ConstructionSpendBuilder.schedule()``,
    reflecting the front-loaded engineering phase, peak construction, and final
    commissioning work that characterize large nuclear projects.

**Structured amortization via ``AmortizationBuilder``**
    The debt service schedule uses ``AmortizationSchedule.builder()`` to chain:

    - A 2-year interest-free grace period at the start of operations, during which
      only principal is repaid.
    - A rate step-down from 10% to 7% in year 10, representing a refinancing
      event once the asset has established its operating track record.

**Bespoke hybrid tax incentive**
    A custom incentive stream combines two components that do not correspond to
    either a standard ITC or PTC:

    - An upfront lump-sum credit equal to 20% of the total capitalized cost
      (overnight cost plus capitalized construction-period interest) at COD.
    - A per-MWh production credit of $15/MWh for the first 5 operating years.

    These are assembled as raw ``CashFlow`` objects so the credit structure is
    fully visible and can be adapted to any hybrid incentive design.

**Construction-outage decomposition**
    Two extended refueling outages are modeled side-by-side:

    - ``generator_outage`` returns a negative ``GenerationStream`` capturing the
      lost MWh, available for downstream physical-quantity analysis.
    - ``construction_outage`` returns a ``CashFlowStream`` with distinct
      lost-revenue, fixed-cost, and per-day-cost line items.
"""

from datetime import date

from dcaf.finance.amortization import AmortizationSchedule
from dcaf.finance.construction import ConstructionSpendBuilder
from dcaf.finance.escalation import ConstantRateEscalation
from dcaf.finance.outage import construction_outage, generator_outage
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.streams.generation import GenerationStream
from dcaf.tax.depreciation import macrs_schedule
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
INITIAL_DEBT_RATE = 0.10  # rate for years 1–9
REDUCED_DEBT_RATE = 0.07  # rate from year 10 onward (refinancing)
COST_OF_EQUITY = 0.10
TAX_RATE = 0.21
DEBT_TERM_YEARS = 20
INTEREST_FREE_YEARS = 2  # grace period: no interest in years 1 and 2
RATE_STEP_DOWN_YEAR = 10  # zero-based period index where rate drops

# Hybrid tax incentive parameters
HYBRID_ITC_RATE = 0.20  # fraction of total capitalized cost at COD
HYBRID_PTC_RATE_PER_MWH = 15.0  # $/MWh for the first operating years
HYBRID_PTC_YEARS = 5

# Construction outage parameters — extensions to baseline refueling outages.
BASELINE_CAPACITY_MW = 1000.0
BASELINE_CAPACITY_FACTOR = 0.92
OUTAGE_FIXED_COST = 500_000.0
OUTAGE_COST_PER_DAY = 50_000.0
OUTAGE_WINDOWS = (
    ("refueling_1", date(2028, 4, 1), date(2028, 4, 11)),
    ("refueling_2", date(2030, 10, 1), date(2030, 10, 11)),
)


# CUSTOM CONSTRUCTION SPEND PROFILE
# Three-phase piecewise profile reflecting nuclear project spending patterns.
# Each breakpoint is (duration_fraction, spend_fraction), where spend_fraction
# is the share of total project cost allocated to that segment. Fractions must
# sum to 1.0 (the terminal (1.0, 0.0) entry is required by the format).
#
#   Phase 1 (  0–25% of duration): Engineering, licensing, and procurement — 15%
#   Phase 2 ( 25–75% of duration): Civil, mechanical, and electrical construction — 60%
#   Phase 3 ( 75–100% of duration): Systems integration, testing, commissioning — 25%
#                                                                    sum = 1.00
CUSTOM_SPEND_SCHEDULE = (
    (0.00, 0.15),  # Phase 1
    (0.25, 0.60),  # Phase 2
    (0.75, 0.25),  # Phase 3
    (1.00, 0.00),
)

# ESCALATION POLICY — shared across construction spend and revenue
escalation_policy = ConstantRateEscalation(
    reference_date=REFERENCE_DATE,
    rate=ESCALATION_RATE,
    period="year",
)

# CONSTRUCTION SPEND SCHEDULE
# ConstructionSpendBuilder allows the spend profile, financing, and escalation
# to be assembled independently and in any order before calling build().
construction = (
    ConstructionSpendBuilder(
        total_cost=OVERNIGHT_CAPEX,
        start_date=CONSTRUCTION_START,
        end_date=OPERATIONS_START,
        period="year",
    )
    .schedule(CUSTOM_SPEND_SCHEDULE)
    .financing(
        DEBT_FRACTION,
        interest_rate=INITIAL_DEBT_RATE,
        treatment="capitalize",
    )
    .escalation_policy(escalation_policy)
    .build()
)

# TOTAL CAPITALIZED COST
# The overnight cost plus any capitalized construction-period interest; used as
# the basis for the upfront ITC-style component of the hybrid incentive.
total_capitalized_cost = abs(construction.sum())

# DEBT SERVICE
# AmortizationSchedule.builder() returns an AmortizationBuilder for rule chaining:
#   .interest_free()   — zero interest rate for selected periods
#   .rate_change()     — update the annual rate from a given period onward
debt_principal = OVERNIGHT_CAPEX * DEBT_FRACTION
debt_service = (
    AmortizationSchedule.builder(
        principal=debt_principal,
        annual_rate=INITIAL_DEBT_RATE,
        term=DEBT_TERM_YEARS,
        start_date=OPERATIONS_START,
        frequency="year",
    )
    .interest_free(to_period=INTEREST_FREE_YEARS - 1)
    .rate_change(from_period=RATE_STEP_DOWN_YEAR, annual_rate=REDUCED_DEBT_RATE)
    .build()
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

# REVENUE
revenue = generation.to_revenue(
    price_per_mwh=POWER_PRICE_PER_MWH,
    label="Electricity Revenue",
    escalation_policy=escalation_policy,
)

# MACRS DEPRECIATION — 15-year property class, half-year convention
depreciation = macrs_schedule(
    cost_basis=OVERNIGHT_CAPEX,
    placed_in_service=OPERATIONS_START,
    property_class=15,
    convention="half-year",
    label="MACRS Depreciation",
)

# HYBRID TAX INCENTIVE
# Part A: Upfront ITC-style credit — 20% of total capitalized cost at COD.
# Assembled as a single CashFlow so the basis, rate, and date are all explicit.
upfront_credit = CashFlow(
    amount=total_capitalized_cost * HYBRID_ITC_RATE,
    date=OPERATIONS_START,
    label="Hybrid ITC Credit",
    pro_forma_category=ProFormaCategory.TAX_CREDIT,
    tax_treatment=TaxTreatment.NONE,
)

# Part B: Per-MWh production credit for the first 5 operating years.
# Built as a CashFlowStream comprehension over the generation entries so the
# credit amount, date, and eligibility window are directly tied to the physical
# generation data.
generation_credit = CashFlowStream(
    [
        CashFlow(
            amount=gen.amount_mwh * HYBRID_PTC_RATE_PER_MWH,
            date=gen.date,
            label=f"Hybrid PTC Credit {gen.date.year}",
            pro_forma_category=ProFormaCategory.TAX_CREDIT,
            tax_treatment=TaxTreatment.TAXABLE,
        )
        for i, gen in enumerate(generation)
        if i < HYBRID_PTC_YEARS
    ]
)

hybrid_credits = CashFlowStream.from_streams(upfront_credit.to_stream(), generation_credit)

# CONSTRUCTION OUTAGES
# ``generator_outage`` produces a physical-quantity stream of negative MWh,
# useful when an analyst wants to inspect lost generation independently of its
# financial impact. ``construction_outage`` produces the corresponding
# operating-cost cashflows (lost revenue + replacement-power + mobilization).
outage_generation_streams = [
    generator_outage(
        capacity_mw=BASELINE_CAPACITY_MW,
        capacity_factor=BASELINE_CAPACITY_FACTOR,
        start=outage_start,
        end=outage_end,
        label=f"{name} Lost MWh",
    )
    for name, outage_start, outage_end in OUTAGE_WINDOWS
]
total_outage_mwh = sum(stream.sum() for stream in outage_generation_streams)

outage_cashflow_streams = [
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
outage_impact = CashFlowStream.from_streams(*outage_cashflow_streams)

# TAXES
# Taxable revenue = electricity revenue + PTC portion of hybrid credits
# Deductions      = MACRS depreciation + interest payments + outage costs
taxable_revenue = CashFlowStream.from_streams(revenue, generation_credit)
deductions = CashFlowStream.from_streams(depreciation, debt_service.interest, outage_impact)
taxable_income = compute_taxable_income(taxable_revenue, deductions)
taxes = tax_liability(taxable_income, tax_rate=TAX_RATE)

# ASSEMBLE ALL CASHFLOWS
cashflows = CashFlowStream.from_streams(
    construction,
    revenue,
    hybrid_credits,
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
print(f"Total capitalized cost ($):  {total_capitalized_cost:,.0f}")
print(f"Discount rate:               {discount_rate:.4%}")
print()
print(f"Total generation (MWh):      {total_generation:,.0f}")
print(f"Construction cash flow ($):  {construction.sum():,.0f}")
print(f"Revenue cash flow ($):       {revenue.sum():,.0f}")
print(f"Hybrid ITC credit ($):       {upfront_credit.amount:,.0f}")
print(f"Hybrid PTC credits ($):      {generation_credit.sum():,.0f}")
print(f"Debt service cash flow ($):  {debt_service.total.sum():,.0f}")
print(f"  Interest-free years:       {INTEREST_FREE_YEARS}")
print(f"  Rate after year {RATE_STEP_DOWN_YEAR}:        {REDUCED_DEBT_RATE:.4%}")
print(f"Outage lost gen (MWh):       {total_outage_mwh:,.0f}")
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
