# Getting Started

This page takes you from nothing to a real NPV and levelized cost in about five
minutes. You only need `EnergyProject` — the high-level builder that is the primary
interface to DCAF.

## 1. Install

DCAF uses the [uv](https://docs.astral.sh/uv/) package manager and targets Python
3.13+.

From a clone of the repository:

```bash
git clone <repository-url> dcaf
cd dcaf
uv sync
```

This creates a virtual environment with DCAF and its dependencies. Run anything in
it with `uv run` (e.g. `uv run python`), or activate `.venv` directly.

Once DCAF is published as a package, you can instead add it to your own project:

```bash
uv add dcaf      # or: pip install dcaf
```

## 2. Build and analyze a project

Create `first_analysis.py`. The example below models a 220 MW nuclear uprate: a
$600 M overnight capital cost, 35 operating years, power sold at $45/MWh, and a
Production Tax Credit of $27.50/MWh.

```python
from datetime import date
from dcaf import EnergyProject

project = (
    EnergyProject()
    .generation(
        capacity_mw=220,
        operations_start=date(2032, 1, 1),
        operations_end=date(2067, 1, 1),  # exclusive end: through end of 2066
    )
    .construction(overnight_cost=600e6)
    .generation_revenue(price=45.00)
    .production_tax_credit(rate_per_unit=27.50)
    .tax(rate=0.21)
)

analysis = project.analyze()
metrics = analysis.metrics(discount_rate=0.10, valuation_date=date(2032, 1, 1))

print(f"Total generation: {analysis.generation.sum():,.0f} MWh")
print(f"NPV:              ${metrics.npv:,.0f}")
print(f"LCOE:             ${metrics.levelized_cost:,.2f}/MWh")
```

Run it:

```bash
uv run python first_analysis.py
```

Expected output:

```text
Total generation: 67,499,520 MWh
NPV:              $318,988,351
LCOE:             $23.30/MWh
```

That's a complete discounted-cash-flow analysis. Each builder method
(`.generation`, `.construction`, `.generation_revenue`, …) adds a cash-flow
component; `.analyze()` compiles them into a {py:class}`~dcaf.ProjectAnalysis`, and
`.metrics()` discounts everything to your valuation date.

```{tip}
Dates use **half-open `[start, end)`** intervals. To model operations through the
end of 2066, set `operations_end=date(2067, 1, 1)` — the first day *after* the
interval. See [Conventions](concepts/conventions.md).
```

## 3. Inspect the cash flows

The analysis exposes every component, so nothing is a black box:

```python
# Named components: generation revenue, construction, PTC, taxes, ...
for name, stream in analysis.cashflow_components.items():
    print(f"{name:>16}: ${stream.sum():,.0f}")

# A year-by-year financial statement
pro_forma = analysis.pro_forma(period="year")
print(pro_forma)              # tab-delimited table
pro_forma.to_csv("pro_forma.csv")
```

## Next steps

- **[Concepts: the EnergyProject builder](concepts/energy_project.md)** — every
  configuration method and what it adds.
- **[Guides](guides/index.md)** — task-focused walkthroughs (financing, tax
  incentives, outages, LCOE), built from the runnable `examples/`.
- **[Calculations](calculations/index.md)** — the exact math behind NPV, IRR, LCOE,
  depreciation, tax, and financing.
- **[Concepts: streams](concepts/streams.md)** — the lower-level primitives for when
  the builder doesn't fit your scenario.
