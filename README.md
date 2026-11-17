# DCAF

DCAF (Discounted Cash Flow Analysis Framework) is a Python library for the financial
analysis of capital-intensive energy projects. Describe a project in a few lines:
capacity, construction cost, financing, revenue, operating costs, and tax incentives.
DCAF gives you back NPV, IRR, levelized cost, and a full year-by-year pro forma.

DCAF was first built at Idaho National Laboratory to evaluate nuclear power uprates,
and it still handles those well. The same tools apply to any project where a large
upfront build is paid back by decades of generation.

## Installation

```bash
pip install dcaf-idaholab
# or: uv add dcaf-idaholab
```

DCAF supports Python 3.11 and newer. The package installs as `dcaf-idaholab` and
imports as `dcaf`:

```python
from dcaf import EnergyProject
```

## Getting Started

This example models a 220 MW plant. It costs $600M to build over five years, half of
it financed with debt. Once running, it sells power at $45/MWh for 30 years and
collects a Production Tax Credit for the first 10.

```python
from datetime import date
from dcaf import EnergyProject

project = (
    EnergyProject()
    .generation(
        capacity_mw=220,
        capacity_factor=0.92,
        operations_start=date(2032, 1, 1),
        operations_end=date(2062, 1, 1),  # exclusive end: 30 operating years
    )
    .construction(
        overnight_cost=600e6,
        construction_start=date(2027, 1, 1),
        spend_profile="bell",  # S-curve spending over the 5-year build
    )
    .construction_financing(debt_fraction=0.5, amortization_rate=0.06, amortization_term=20)
    .generation_revenue(price=45.00)  # $/MWh
    .fixed_opex(amount=8e6)  # $/year
    .production_tax_credit(rate_per_unit=27.50)  # $/MWh for the first 10 years
    .tax(rate=0.21)
    .depreciation_macrs(property_class=15)
)

analysis = project.analyze()
metrics = analysis.metrics(discount_rate=0.08, valuation_date=date(2027, 1, 1))

print(f"NPV:  ${metrics.npv / 1e6:,.1f} M")
print(f"IRR:  {metrics.xirr:.1%}")
print(f"LCOE: ${metrics.levelized_cost:,.2f}/MWh")

analysis.pro_forma(period="year").to_csv("pro_forma.csv")
```

```text
NPV:  $299.2 M
IRR:  17.4%
LCOE: $17.12/MWh
```

`pro_forma.csv` holds the full annual statement: revenue, operating costs, EBITDA,
depreciation, taxes, tax credits, debt service, and free cash flow to the firm and to
equity. Every number in it traces back to a named cash flow in `analysis`, so you can
check any result line by line.

## Overview

**Build a project in one expression.** `EnergyProject` is a fluent builder. Each
method adds one piece of the project and returns a new, immutable copy, so you can
set up a base case once and branch scenarios from it.

**Get standard metrics and pro formas.** NPV, IRR, and levelized cost (LCOE),
discounted to any valuation date. Pro formas aggregate by year, quarter, month, or
day and export to CSV.

**Model construction and financing in detail.**
- Spend curves (bell, flat, ramped, triangle, linear, upfront) with cost escalation
- Construction loans that accrue interest from each draw date, with capitalized or
  paid interest
- Amortizing debt, custom debt schedules, and WACC-based discounting

**Price revenue the way contracts are written.**
- Fixed prices, dated price schedules, or your own pricing function
- Power purchase agreements that buy a share of generation or a fixed volume, with
  the remainder sold at a merchant price
- Planned outages during construction or operations, with their lost revenue

**Handle tax and incentives.**
- Income tax with deductible costs and an interest tax shield
- MACRS (half-year and mid-quarter) and VDB depreciation
- Production Tax Credits (with escalation) and Investment Tax Credits

**Drop down a level when you need to.** The builder is made of public primitives:
`CashFlowStream`, `GenerationStream`, and the functions in `dcaf.finance`,
`dcaf.tax`, and `dcaf.metrics`. Use them on their own, or inject your own cash flows
into a project with `add_cashflow_stream()`.

## Learn More

- [Examples](https://github.com/IdahoLabResearch/DCAF/tree/main/examples): runnable
  scripts that model the same project at five levels of the API, plus a multi-PPA
  revenue example
- [Documentation](https://github.com/IdahoLabResearch/DCAF/tree/main/docs): concepts,
  task guides, and the math behind every calculation
