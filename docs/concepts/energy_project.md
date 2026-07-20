# The EnergyProject builder

`EnergyProject` is the primary interface to DCAF. It is a fluent, immutable builder:
each configuration method returns a *new* project, leaving the original unchanged, so
configurations are safe to reuse and branch.

```python
from datetime import date
from dcaf import EnergyProject

base = EnergyProject().generation(
    capacity_mw=220,
    operations_start=date(2032, 1, 1),
    operations_end=date(2067, 1, 1),
)

# Branch without mutating `base`
high_price = base.generation_revenue(price=50.0).tax(rate=0.21)
low_price = base.generation_revenue(price=40.0).tax(rate=0.21)
```

## Configuration methods

The builder groups naturally into the pieces of a project. All arguments are
keyword-only.

### Valuation and global settings

| Method | Purpose |
|--------|---------|
| `EnergyProject(frequency=, timing=, day_count_convention=)` | Period granularity, event-timing, and [day-count convention](conventions.md). |
| `.discount_rate(rate=)` | Set the project discount rate directly. |
| `.wacc(debt_fraction=, debt_cost=, equity_fraction=, equity_cost=, tax_rate=)` | Compute and set the discount rate as a weighted average cost of capital. |
| `.default_escalation(rate=, ...)` | Default escalation applied to revenue/cost items. |

### Generation

| Method | Purpose |
|--------|---------|
| `.generation(capacity_mw=, capacity_factor=, operations_start=, operations_end=)` | Capacity-based generation over the operating life. |
| `.generation_stream(stream=)` | Supply a pre-built `GenerationStream`. |
| `.generation_outage(start=, end=, ...)` | Reduce generation over an outage window. |

### Revenue and operating costs

| Method | Purpose |
|--------|---------|
| `.generation_revenue(price=...)` | Revenue = generation × price. |
| `.generation_revenue_contract(name=, contract=)` | Contracted generation revenue such as PPAs. |
| `.generation_revenue_remainder(name=, price=)` | Revenue from generation not allocated to contracts. |
| `.fixed_opex(amount=, frequency=, ...)` | Recurring fixed operating cost. |
| `.variable_cost(rate_per_unit=, ...)` | Per-MWh variable operating cost. |

### Construction and financing

| Method | Purpose |
|--------|---------|
| `.construction(overnight_cost=, spend_profile=, construction_start=, period=)` | Capital spend schedule. |
| `.construction_stream(stream=)` | Supply a pre-built construction `CashFlowStream`. |
| `.construction_financing(debt_fraction=, amortization_rate=, amortization_term=, ...)` | Debt funding and amortized debt service. |
| `.debt_schedule(schedule=)` | Supply a pre-built `AmortizationSchedule`. |
| `.construction_outage(start=, end=, capacity_mw=, capacity_factor=, ...)` | Lost-revenue / replacement-power cost during construction. |

### Tax and incentives

| Method | Purpose |
|--------|---------|
| `.tax(rate=, allow_refund=)` | Apply a tax rate to taxable income. |
| `.depreciation_macrs(property_class=, convention=)` | MACRS depreciation deductions. |
| `.depreciation_vdb(life=, ...)` | Variable-declining-balance depreciation. |
| `.investment_tax_credit(rate=)` | One-time ITC at placed-in-service. |
| `.production_tax_credit(rate_per_unit=, years=, escalation=)` | Per-MWh PTC over an eligibility window. |

### Custom components

| Method | Purpose |
|--------|---------|
| `.add_cashflow_stream(name=, stream=)` | Attach any custom `CashFlowStream` as a named component. |

## Compiling and reading results

| Method | Returns |
|--------|---------|
| `.analyze()` | {py:class}`~dcaf.ProjectAnalysis` — all components, generation, taxes. |
| `.cashflows()` | The combined `CashFlowStream` (convenience). |
| `.components()` | The named `CashFlowGroup` (convenience). |
| `.metrics(discount_rate=, valuation_date=)` | {py:class}`~dcaf.ProjectMetrics` — NPV, IRR, LCOE. |
| `.pro_forma(period=)` | {py:class}`~dcaf.ProjectProForma` — period statement. |

`ProjectAnalysis` is the hub. From it you can read `analysis.cashflow_components`
(named components), `analysis.generation`, `analysis.taxable_income`, and
`analysis.taxes`, then derive metrics and pro-formas as many times as you like with
different discount rates or valuation dates.

```python
analysis = project.analyze()

m08 = analysis.metrics(discount_rate=0.08, valuation_date=date(2032, 1, 1))
m12 = analysis.metrics(discount_rate=0.12, valuation_date=date(2032, 1, 1))
print(m08.npv, m12.npv)
```

```{seealso}
- [Guides: high-level project](../guides/high_level_project.md) for a full
  walkthrough with financing, escalation, and outages.
- The [Project API reference](../api/project.md) for complete signatures.
```
