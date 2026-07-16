# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Primitives-level DCAF analysis for a nuclear plant uprate.

This example constructs the same uprate scenario using only the core stream
primitives — ``CashFlow``, ``CashFlowStream``, ``Generation``, and
``GenerationStream`` — without calling any tax, finance, or project module
functions.

Every cashflow and generation entry is assembled directly, with manual
calculations standing in for the library utilities used at higher API levels.
This is the most flexible approach: it imposes no structural assumptions and
can accommodate externally-supplied data tables, custom depreciation schedules,
or analysis shapes that do not map naturally onto the builder APIs.

Modeling notes
--------------
- Construction draws are equal annual payments (no spend profile).
- Revenue escalation is applied as a simple compound factor per year.
- PTC is a flat per-MWh credit for the first 10 operating years (no escalation).
- Taxes are approximated as a flat rate on revenue only; PTC is modeled as a
  separate tax-credit cashflow using the same project-delta treatment as ITC.
  depreciation and interest deductions are omitted for brevity.
- Debt service is a level-payment annuity (no interest/principal split).
- Construction outage impact is built as raw ``CashFlow`` objects with manually
  computed lost MWh × price plus fixed and per-day cost components, mirroring
  what ``construction_outage`` produces under the hood.
"""

from datetime import date, timedelta

from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.streams.generation import Generation, GenerationStream


# ANALYSIS CONSTANTS
CONSTRUCTION_START = date(2027, 1, 1)
OPERATIONS_START = date(2032, 1, 1)
OPERATIONS_END = date(2067, 1, 1)

CAPACITY_MW = 220.0
CAPACITY_FACTOR = 0.92
HOURS_PER_YEAR = 8_760.0
OPERATING_YEARS = 35

OVERNIGHT_CAPEX = 600_000_000.0
CONSTRUCTION_YEARS = 5
POWER_PRICE_PER_MWH = 45.0
ESCALATION_RATE = 0.025

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
HOURS_PER_DAY = 24.0
OUTAGE_FIXED_COST = 500_000.0
OUTAGE_COST_PER_DAY = 50_000.0
OUTAGE_WINDOWS = (
    ("Refuel #1", date(2028, 4, 1), date(2028, 4, 11)),
    ("Refuel #2", date(2030, 10, 1), date(2030, 10, 11)),
)


# CONSTRUCTION — equal annual draws
annual_draw = OVERNIGHT_CAPEX / CONSTRUCTION_YEARS
construction = CashFlowStream(
    [
        CashFlow(
            amount=-annual_draw,
            date=date(CONSTRUCTION_START.year + i, 1, 1),
            label=f"Construction Draw {i + 1}",
            pro_forma_category=ProFormaCategory.CAPITAL_COST,
            tax_treatment=TaxTreatment.NONE,
        )
        for i in range(CONSTRUCTION_YEARS)
    ]
)

# GENERATION — one entry per operating year
annual_mwh = CAPACITY_MW * CAPACITY_FACTOR * HOURS_PER_YEAR
generation = GenerationStream(
    [
        Generation(
            amount_mwh=annual_mwh,
            date=date(OPERATIONS_START.year + i, 1, 1),
            label=f"Generation {OPERATIONS_START.year + i}",
        )
        for i in range(OPERATING_YEARS)
    ]
)

# REVENUE — price escalated by a compound factor for each operating year
revenue = CashFlowStream(
    [
        CashFlow(
            amount=gen.amount_mwh * POWER_PRICE_PER_MWH * (1 + ESCALATION_RATE) ** i,
            date=gen.date,
            label=f"Electricity Revenue {gen.date.year}",
            pro_forma_category=ProFormaCategory.REVENUE,
            tax_treatment=TaxTreatment.TAXABLE,
        )
        for i, gen in enumerate(generation)
    ]
)

# PRODUCTION TAX CREDIT — flat rate for the first 10 operating years
ptc_credits = CashFlowStream(
    [
        CashFlow(
            amount=gen.amount_mwh * PTC_RATE_PER_MWH,
            date=gen.date,
            label=f"PTC Credit {gen.date.year}",
            pro_forma_category=ProFormaCategory.TAX_CREDIT,
            tax_treatment=TaxTreatment.NONE,
        )
        for i, gen in enumerate(generation)
        if i < PTC_YEARS
    ]
)

# CONSTRUCTION OUTAGE IMPACT — manually assemble three CashFlow objects per
# outage (lost revenue, mobilization, replacement power) for parity with what
# ``construction_outage`` produces.
outage_cashflows: list[CashFlow] = []
for label, outage_start, outage_end in OUTAGE_WINDOWS:
    days = (outage_end - outage_start).days
    booking_date = outage_end - timedelta(days=1)  # last inclusive outage day ("end" timing)
    lost_mwh = BASELINE_CAPACITY_MW * BASELINE_CAPACITY_FACTOR * HOURS_PER_DAY * days
    outage_cashflows.extend(
        [
            CashFlow(
                amount=-lost_mwh * POWER_PRICE_PER_MWH,
                date=booking_date,
                label=f"{label} Lost Revenue",
                pro_forma_category=ProFormaCategory.OPERATING_COST,
                tax_treatment=TaxTreatment.DEDUCTIBLE,
            ),
            CashFlow(
                amount=-OUTAGE_FIXED_COST,
                date=booking_date,
                label=f"{label} Mobilization",
                pro_forma_category=ProFormaCategory.OPERATING_COST,
                tax_treatment=TaxTreatment.DEDUCTIBLE,
            ),
            CashFlow(
                amount=-OUTAGE_COST_PER_DAY * days,
                date=booking_date,
                label=f"{label} Replacement Power",
                pro_forma_category=ProFormaCategory.OPERATING_COST,
                tax_treatment=TaxTreatment.DEDUCTIBLE,
            ),
        ]
    )
outage_impact = CashFlowStream(outage_cashflows)

# DEBT SERVICE — level-payment annuity (interest and principal combined)
# PMT = P * r / (1 - (1 + r)^-n)
debt_principal = OVERNIGHT_CAPEX * DEBT_FRACTION
annual_debt_payment = debt_principal * COST_OF_DEBT / (1 - (1 + COST_OF_DEBT) ** -DEBT_TERM_YEARS)
debt_service = CashFlowStream(
    [
        CashFlow(
            amount=-annual_debt_payment,
            date=date(OPERATIONS_START.year + i, 1, 1),
            label=f"Debt Service {OPERATIONS_START.year + i}",
            pro_forma_category=ProFormaCategory.FINANCING_PRINCIPAL,
            tax_treatment=TaxTreatment.NONE,
        )
        for i in range(DEBT_TERM_YEARS)
    ]
)

# TAXES — flat rate on revenue only (deductions not modeled here)
taxes = CashFlowStream(
    [
        CashFlow(
            amount=cf.amount * -TAX_RATE,
            date=cf.date,
            label=f"Tax {cf.date.year}",
            pro_forma_category=ProFormaCategory.TAX,
            tax_treatment=TaxTreatment.NONE,
        )
        for cf in revenue
        if cf.amount > 0
    ]
)

# ASSEMBLE ALL CASHFLOWS
cashflows = CashFlowStream.from_streams(
    construction,
    revenue,
    ptc_credits,
    debt_service,
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
print(f"Debt service cash flow ($):  {debt_service.sum():,.0f}")
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
