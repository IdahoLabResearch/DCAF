# Escalation

Escalation grows prices and costs over time. An escalation policy maps a target date
to a multiplicative factor relative to a reference date. The primitives live in
{py:mod}`dcaf.finance` (escalation).

## Compound factor

The core operation is compound growth ({py:func}`dcaf.shared.compound_factor`):

$$
\text{factor} = (1 + \text{rate})^{t}
$$

where $t$ is the elapsed time in periods (or years) between the reference date and
the target date, measured with the project [day-count convention](conventions.md).

```python
from dcaf.shared import compound_factor

round(compound_factor(0.08, 5), 4)
# -> 1.4693
```

## Policies

| Policy | Behavior |
|--------|----------|
| {py:class}`~dcaf.finance.ConstantRateEscalation` | A single constant rate compounded from a reference date. |
| {py:class}`~dcaf.finance.CompositeEscalation` | Piecewise segments, each its own rate over a date range. |
| {py:class}`~dcaf.finance.IndexSeriesEscalation` | Factors derived from a supplied index series. |
| {py:class}`~dcaf.finance.EscalationBuilder` | Fluent construction of piecewise/composite policies. |

All policies satisfy the {py:class}`~dcaf.finance.EscalationPolicy` protocol — a
`reference_date` plus a `factor(target_date)` method — so they are interchangeable
anywhere escalation is accepted.

## Where escalation is applied

- `EnergyProject.default_escalation(rate=...)` sets a project-wide default.
- Per-component cost and credit methods accept an `escalation=` argument, e.g.
  `.fixed_opex(amount=, escalation=...)` and `ptc(..., escalation=...)`.
- Simple generation revenue can use a float price; scheduled or callable revenue
  prices use `GenerationPrice`.
- Tax credits escalate too: `ptc(..., escalation=...)`.

`GenerationPrice.schedule(...)` is an exact-date lookup, not a sequence of price
change dates. Every generation settlement being priced must have a schedule entry
with the same date. Prices are not carried forward to later generation events.

A constant escalation rate can be supplied as a bare float; a policy object is used
when you need piecewise or index-based behavior.

```{seealso}
[Finance API reference](../api/finance.md) · [Guides: escalation & financing](../guides/escalation_and_financing.md).
```
