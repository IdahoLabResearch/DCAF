# High-level project

This is the recommended end-to-end workflow: describe the whole project with the
`EnergyProject` builder, then read metrics and a pro-forma. It mirrors the
`examples/nuclear_uprate_project.py` script — a 220 MW uprate with a dated
construction timeline, escalation, debt financing, a PTC, and two construction
outages.

## Build the project

```python
from datetime import date
from dcaf import EnergyProject

project = (
    EnergyProject()
    .default_escalation(rate=0.025, amount_reference_date=date(2025, 1, 1))
    .generation(
        capacity_mw=220.0,
        capacity_factor=0.92,
        operations_start=date(2032, 1, 1),
        operations_end=date(2067, 1, 1),     # half-open: through end of 2066
        label="Uprate Generation",
    )
    .construction(
        overnight_cost=600_000_000.0,
        spend_profile="flat",
        construction_start=date(2027, 1, 1),
        period="year",
    )
    .construction_financing(
        debt_fraction=0.50,
        construction_interest_rate=0.10,
        amortization_rate=0.10,
        amortization_term=20,
    )
    .tax(rate=0.21)
    .revenue_from_generation(sell_price_per_unit=45.0, label="Electricity Revenue")
    .production_tax_credit(rate_per_unit=27.50, years=10, label="PTC Credit")
)
```

Each method adds a named cash-flow component. Because the builder is immutable, you
can assign intermediate stages to variables and branch from them.

## Set the discount rate from WACC

You can set the discount rate directly with `.discount_rate(rate=...)`, or derive a
weighted average cost of capital from the financing structure:

```python
project = project.wacc(
    debt_fraction=0.50, debt_cost=0.10,
    equity_fraction=0.50, equity_cost=0.10,
    tax_rate=0.21,
)
```

## Analyze and read results

```python
analysis = project.analyze()

metrics = analysis.metrics(valuation_date=date(2027, 1, 1))
print(metrics)                       # NPV, IRR (xirr), LCOE, generation totals

# Per-component totals — nothing is hidden
for name, stream in analysis.cashflow_components.items():
    print(f"{name:>28}: ${stream.sum():,.0f}")

# Annual financial statement
pro_forma = analysis.pro_forma(period="year")
pro_forma.to_csv("uprate_pro_forma.csv")
```

`analysis.metrics(...)` can be called repeatedly with different discount rates or
valuation dates without recompiling.

## Where to go next

- [Escalation & financing](escalation_and_financing.md)
- [Tax & incentives](tax_and_incentives.md)
- [Outages](outages.md)
- [Levelized cost](levelized_cost.md)
```{seealso}
[Concepts: the EnergyProject builder](../concepts/energy_project.md) for the full
method reference.
```
