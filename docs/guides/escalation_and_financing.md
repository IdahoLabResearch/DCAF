# Escalation & financing

Two of the biggest drivers of a project's economics are how costs and prices grow
over time (escalation) and how construction is funded (financing). Both are
first-class options on the builder.

## Escalation

A project-wide default escalation is applied to revenue and cost items relative to a
reference date:

```python
project = project.default_escalation(rate=0.025, amount_reference_date=date(2025, 1, 1))
```

Any per-component method can override the default with its own `escalation=`
argument — a bare float for a constant rate, or an
{py:class}`~dcaf.finance.EscalationPolicy` for piecewise/index-based growth:

```python
from dcaf.finance import ConstantRateEscalation

project = (
    project
    .revenue_from_generation(sell_price_per_unit=45.0, escalation=0.025)
    .fixed_opex(amount=10_000_000.0, escalation=0.03)
)
```

The underlying math — $(1 + \text{rate})^{t}$ — is documented in
[Calculations: escalation](../calculations/escalation.md).

## Construction financing

`.construction_financing(...)` funds a fraction of construction with debt,
capitalizes (or pays) construction-period interest, and builds an amortized debt
service schedule:

```python
project = project.construction_financing(
    debt_fraction=0.50,
    construction_interest_rate=0.10,
    amortization_rate=0.10,
    amortization_term=20,        # years of debt service
)
```

This adds `debt_proceeds` funding entries at the construction cost dates and
`debt_service` interest and principal components during operations. The proceeds
are tax-neutral. Interest is tax deductible, so it automatically reduces taxable
income (the interest tax shield).

### Bring your own schedule

For non-standard debt — interest-only periods, a refinancing rate change, or an
externally-supplied schedule — build an `AmortizationSchedule` directly and attach
it:

```python
from dcaf.finance.amortization import AmortizationSchedule

schedule = (
    AmortizationSchedule.builder(
        principal=300_000_000.0, annual_rate=0.10, term=20,
        start_date=date(2032, 1, 1), frequency="year",
    )
    .interest_only(2)
    .rate_change(from_period=10, annual_rate=0.07)
    .build()
)
project = project.debt_schedule(schedule)
```

The amortization formulas (level payment, interest/principal split, builder rules)
are documented in [Calculations: financing](../calculations/financing.md).

```{seealso}
[Finance API reference](../api/finance.md).
```
