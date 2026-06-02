# DCAF: Discounted Cash Flow Analysis Framework

DCAF is a financial engineering library for nuclear energy project cost modeling.
It computes NPV, IRR, and levelized cost; models financing, depreciation (MACRS/VDB),
and tax incentives (PTC/ITC); and produces period pro-formas — with a focus on
Extended Power Uprate (EPU) projects.

**`EnergyProject` is the front door.** It is a fluent, immutable builder that composes
generation, costs, financing, and tax treatment, then hands you NPV, IRR, LCOE, and a
pro-forma. The lower-level stream primitives it is built from are available too, for
advanced use cases.

```python
from datetime import date
from dcaf import EnergyProject

analysis = (
    EnergyProject()
    .generation(capacity_mw=220, operations_start=date(2032, 1, 1), operations_end=date(2067, 1, 1))
    .construction(overnight_cost=600e6)
    .revenue_from_generation(sell_price_per_unit=45.00)
    .production_tax_credit(rate_per_unit=27.50)
    .tax(rate=0.21)
    .analyze()
)

metrics = analysis.metrics(discount_rate=0.10, valuation_date=date(2032, 1, 1))
print(f"NPV: ${metrics.npv:,.0f}")                  # NPV: $318,988,351
print(f"LCOE: ${metrics.levelized_cost:,.2f}/MWh")  # LCOE: $23.30/MWh
```

New here? **[Get a working result in under 5 minutes →](getting_started.md)**

## How the documentation is organized

- **[Getting Started](getting_started.md)** — install and run your first analysis.
- **Concepts** — the mental model: `EnergyProject`, project timeline conventions, and
  the stream primitives underneath.
- **Guides** — task-focused walkthroughs built from the runnable `examples/`.
- **Calculations** — transparent documentation of the DCF model and every underlying
  formula (NPV, IRR, LCOE, depreciation, tax, financing, escalation).
- **API Reference** — auto-generated from the source docstrings.

```{toctree}
:hidden:
:caption: Getting Started

getting_started.md
```

```{toctree}
:hidden:
:maxdepth: 1
:caption: Concepts

concepts/overview.md
concepts/energy_project.md
concepts/conventions.md
concepts/streams.md
```

```{toctree}
:hidden:
:maxdepth: 1
:caption: Guides

guides/index.md
guides/high_level_project.md
guides/escalation_and_financing.md
guides/tax_and_incentives.md
guides/outages.md
guides/levelized_cost.md
```

```{toctree}
:hidden:
:maxdepth: 1
:caption: Calculations

calculations/index.md
calculations/dcf_npv.md
calculations/irr.md
calculations/lcoe.md
calculations/depreciation.md
calculations/tax.md
calculations/financing.md
calculations/escalation.md
calculations/conventions.md
```

```{toctree}
:hidden:
:maxdepth: 1
:caption: API Reference

api/index.md
```

```{toctree}
:hidden:
:caption: Reference

architecture_diagram.md
DCAF_reqs.md
```
