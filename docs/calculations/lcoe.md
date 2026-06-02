# LCOE

The levelized cost of energy is the electricity price ($/MWh) at which a project
exactly breaks even — the price that drives project NPV to zero. DCAF computes it in
{py:func}`dcaf.metrics.lcoe`.

## What it solves

Find the price $p$ such that:

$$
\text{NPV}\big(\text{all costs} + p \times \text{generation basis} - \text{tax}(p)\big) = 0
$$

The solve is price-aware about taxes: at each trial price the revenue changes, so
taxable income — and therefore the tax cash flow — is recomputed.

## Algorithm

The objective function ({py:func}`dcaf.metrics.lcoe` → `_lcoe_objective`) evaluates
project NPV at a trial price by:

1. Removing existing revenue (`ProFormaCategory.REVENUE`) and the existing tax
   liability from the component streams.
2. Injecting synthetic revenue equal to `price × basis_stream`, where the basis is a
   `$1/MWh` revenue stream carrying the project's escalation path.
3. Recomputing taxes on the new taxable income.
4. Returning the cash NPV of the result.

This objective is monotonically increasing in price, so DCAF finds the root with a
**bracketing search followed by Brent's method** (inverse-quadratic / secant steps
with a bisection fallback). It returns `None` when the basis stream is empty or the
objective is not monotonic.

## Worked example

```python
from datetime import date
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow, CashFlowGroup, CashFlowStream, Generation, GenerationStream
from dcaf.metrics import lcoe

basis = GenerationStream([
    Generation(1000.0, date(2026, 12, 31)),
    Generation(1000.0, date(2027, 12, 31)),
]).to_revenue(price_per_mwh=1.0, tax_treatment=TaxTreatment.TAXABLE)

components = CashFlowGroup({
    "capex": CashFlowStream([
        CashFlow(-1500.0, date(2025, 1, 1), pro_forma_category=ProFormaCategory.CAPITAL_COST),
    ]),
    "opex": CashFlowStream([
        CashFlow(-200.0, date(2026, 12, 31), pro_forma_category=ProFormaCategory.OPERATING_COST, tax_treatment=TaxTreatment.DEDUCTIBLE),
        CashFlow(-200.0, date(2027, 12, 31), pro_forma_category=ProFormaCategory.OPERATING_COST, tax_treatment=TaxTreatment.DEDUCTIBLE),
    ]),
})

round(lcoe(basis, components, tax_rate=None, discount_rate=0.08, valuation_date=date(2025, 1, 1)), 2)
# -> 1.11  ($/MWh)
```

In the project workflow this is surfaced as `ProjectMetrics.levelized_cost`, computed
automatically from the analysis components.

```{seealso}
[Metrics API reference](../api/metrics.md) · [Guides: levelized cost](../guides/levelized_cost.md).
```
