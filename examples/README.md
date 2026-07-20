# DCAF Examples

The five nuclear-uprate examples model approximately the same scenario: a 220 MW
nuclear plant uprate with a 5-year construction period, 35 operating years, 50/50
debt/equity financing, a 21% tax rate, and a Production Tax Credit for the first 10
operating years. They are designed to show how the same analysis can be expressed at
five different levels of the DCAF API, from the most concise high-level builder down
to raw stream primitives.

`ppa_revenue_policies.py` is a separate, focused example of generation allocation
and pricing across multiple PPAs plus merchant remainder revenue.

---

## `nuclear_uprate_project_simple.py` — High-level builder, minimal config

The simplest entry point. Uses `EnergyProject` with only the required arguments:
capacity, overnight capital cost, sell price, and a tax rate. No escalation, no
construction timeline, no financing structure, no labels. This is appropriate for
quick feasibility studies or sensitivity sweeps where exact timing does not matter.

A single `.construction_outage(...)` call models a 10-day extension to a
baseline-plant refueling outage during construction.

**What it demonstrates:** the minimum viable `EnergyProject` configuration; the
`analysis.metrics()` convenience method for NPV and LCOE; the simplest form of
outage modeling.

---

## `nuclear_uprate_project.py` — High-level builder, full config

The same `EnergyProject` builder with all configuration options engaged: a dated
construction timeline, a bell-curve spend profile, escalation tied to a reference
date, debt/equity financing with a multi-year amortization term, carrier and source
annotations, two named construction outages with mobilization and replacement-power
costs, and per-component cash flow introspection. This is the recommended
starting point for most production analyses.

**What it demonstrates:** the full breadth of `EnergyProject` configuration;
`default_escalation`, `construction_financing`, asset annotations, multiple named
`construction_outage` invocations with custom labels, and manual NPV / LCOE
computation from `analysis.cashflows` and `analysis.generation`.

---

## `nuclear_uprate_project_midlevel.py` — Mid-level functions and builder classes

Bypasses `EnergyProject` entirely and assembles the analysis from the individual
financial sub-system functions:

| Component | Function / class |
|-----------|-----------------|
| Construction spend | `construction_spend_schedule` + `ConstructionFinancing` |
| Escalation | `ConstantRateEscalation` |
| Debt service | `AmortizationSchedule.build` |
| Generation | `GenerationStream.from_capacity` |
| Revenue | `GenerationStream.to_revenue` |
| Construction outages | `construction_outage` |
| Depreciation | `macrs_schedule` |
| Tax credits | `ptc` |
| Taxes | `compute_taxable_income` + `tax_liability` |
| Assembly | `CashFlowStream.from_streams` |

This level is appropriate when the high-level builder's structure does not fit the
scenario — for example, when mixing externally-supplied data with library schedules,
or when fine-grained control over depreciation conventions, amortization granularity,
or interest treatment is required.

**What it demonstrates:** explicit escalation policy sharing; MACRS property class
and convention selection; the interest/principal decomposition on `AmortizationSchedule`;
manual taxable income assembly from revenue, credits, depreciation, and interest streams.

---

## `nuclear_uprate_project_builders.py` — Mid-level builder classes, advanced config

Also bypasses `EnergyProject`, but focuses on the fluent builder APIs rather than the
direct function API used in the midlevel example. Four features are highlighted:

**Custom construction spend profile** — a three-phase piecewise schedule is passed to
`ConstructionSpendBuilder.schedule()`, allocating 15% of spend to the engineering and
procurement phase, 60% to main construction, and 25% to commissioning. The builder
derives the total capitalized cost (overnight cost plus capitalized interest) from the
resulting stream, which is used as the basis for the hybrid incentive below.

**Structured debt service** — `AmortizationSchedule.builder()` chains two rules before
calling `build()`: `.interest_free(to_period=1)` waives interest in the first two
operating years, and `.rate_change(from_period=10, annual_rate=0.07)` simulates a
refinancing event that reduces the rate from 10% to 7% in year 10.

**Bespoke hybrid tax incentive** — a custom credit structure combines an upfront
ITC-style lump sum (20% of total capitalized cost at COD) with a short-term PTC-style
per-MWh credit ($15/MWh for the first 5 operating years). Both components are
assembled directly as `CashFlow` objects, with explicit `pro_forma_category` and
`tax_treatment` fields, rather than delegating to `itc()` or `ptc()`.

**Construction-outage decomposition** — `generator_outage()` is invoked alongside
`construction_outage()` to obtain both the physical-quantity (negative MWh)
representation and the financial (lost-revenue + replacement-cost) representation
of the same outage event. The cashflow form is wired into the deductions stream so
its tax shield is captured.

**What it demonstrates:** `ConstructionSpendBuilder` chaining; custom `SpendProfile`
via `SpendProfile.custom()`; `AmortizationBuilder` rules (`interest_free`,
`rate_change`); total capitalized cost derivation from a constructed stream;
hand-assembled `CashFlow` objects for non-standard incentive structures; combined
use of `generator_outage` and `construction_outage` for outage modeling.

---

## `nuclear_uprate_project_primitives.py` — Raw stream primitives

Builds the entire analysis from `CashFlow`, `CashFlowStream`, `Generation`, and
`GenerationStream` objects directly, with no calls to any tax, finance, or project
module functions. Construction draws, generation entries, revenue, PTC credits, debt
service, and taxes are all constructed by hand using list comprehensions and manual
formulas (compound escalation factor, PMT annuity).

This level imposes no structural assumptions and can accommodate any data shape or
external data source. It is also the clearest illustration of what the higher-level
APIs produce under the hood.

**What it demonstrates:** direct `CashFlow` and `Generation` dataclass construction
with explicit `pro_forma_category` and `tax_treatment` fields; manual escalation via
compound factor; the PMT formula for level-payment debt service; manual outage
cashflow assembly (lost MWh × price plus fixed and per-day costs) mirroring what
`construction_outage()` produces internally; `CashFlowStream.from_streams` for
assembly; `.cash_only()`, `.inflows()`, `.outflows()`, `.npv()`, and `.discounted_sum()`
for valuation.

---

## `ppa_revenue_policies.py` — Multiple PPAs and generation pricing

Uses one fixed-MWh PPA with an exact-date `GenerationPrice.schedule`, one
fraction-of-generation PPA with a callable price, and a fixed-price merchant
remainder policy. The script verifies that the two contracts and remainder account
for every MWh on every generation date.

**What it demonstrates:** multiple simultaneous contracts; fixed and fractional
generation allocation; scheduled, callable, and fixed generation prices; exact-date
schedule semantics; and merchant remainder revenue.

---

## Output comparison

The five examples produce different numerical results because each makes different
modeling choices. The table below shows approximate NPV at a 10% discount rate from
the construction start date:

| Example | NPV |
|---------|-----|
| `_simple` | ~$307 M (no financing, no escalation) |
| `_full` (high-level) | ~$37 M (full financing and escalation) |
| `_midlevel` | ~$186 M (MACRS deductions lower tax burden vs. high-level) |
| `_builders` | ~$162 M (custom incentive; lower debt cost reduces service vs. midlevel) |
| `_primitives` | ~−$36 M (no depreciation deduction; escalation on revenue only) |

All five examples include construction-outage modeling (10-day extensions to two
baseline refueling outages during construction). The outage line items are
visible in each example's per-component cashflow output.

The divergence is intentional — it reflects the modeling choices made at each level,
not errors. The primitives example in particular omits depreciation deductions and
interest deductibility to keep the manual tax approximation simple.
