# Streams (advanced)

```{admonition} When do you need this?
:class: tip
For most work, use [`EnergyProject`](energy_project.md) — it builds and combines
streams for you. Reach for streams directly when the builder's structure doesn't fit:
ingesting external data, hand-building a bespoke incentive, or applying a
transformation the builder doesn't expose. Streams are also the clearest way to see
*what the builder produces under the hood*.
```

DCAF's primitives are two parallel families of immutable, dated values:

| Single value | Container | Grouped container |
|--------------|-----------|-------------------|
| `CashFlow` (dated `$`) | `CashFlowStream` | `CashFlowGroup[Key]` |
| `Generation` (dated MWh) | `GenerationStream` | `GenerationGroup[Key]` |

## CashFlow and CashFlowStream

A `CashFlow` is a frozen record: an `amount`, a `date`, a `label`, an `is_cash` flag
(cash vs. accrual items like depreciation), a `pro_forma_category` (how it appears in
a statement), and a `tax_treatment` (`TAXABLE`, `DEDUCTIBLE`, or `NONE`).

A `CashFlowStream` is a functional container. Every operation returns a *new* stream,
so chains never mutate the original:

```python
from datetime import date
from dcaf.streams import CashFlow, CashFlowStream

stream = CashFlowStream([
    CashFlow(-10_000.0, date(2026, 1, 1)),
    CashFlow(  4_000.0, date(2027, 1, 1)),
    CashFlow(  4_000.0, date(2028, 1, 1)),
    CashFlow(  5_000.0, date(2029, 1, 1)),
])

stream.npv(rate=0.10, valuation_date=date(2026, 1, 1))   # discounted total
stream.irr()                                             # internal rate of return
yearly = stream.sort().group_by(period="year").sum()     # grouped totals
```

Useful builders and operations: `from_streams(...)`, `from_recurring(...)`,
`filter(...)`, `apply(...)`, `scale(...)`, `sum()`, `group_by(...)`,
`cash_only()`, `inflows()`, `outflows()`, `npv(...)`, `irr(...)`.

## Generation and GenerationStream

`GenerationStream` mirrors `CashFlowStream` for physical energy (MWh) and bridges to
money via `to_revenue(price_per_mwh, escalation=...)` and `to_cost(rate_per_mwh,
...)`:

```python
from datetime import date
from dcaf.streams import GenerationStream

gen = GenerationStream.from_capacity(
    capacity_mw=1_200.0, capacity_factor=0.92, start=date(2030, 1, 1), periods=5,
)
revenue = gen.to_revenue(price_per_mwh=55.0, escalation=0.02)   # -> CashFlowStream
gen.sum()                                                       # total MWh
```

## Groups

`group_by(...)` (by a key function or a time `period`) returns a `CashFlowGroup` or
`GenerationGroup` — a dict-like container of sub-streams supporting `aggregate(...)`,
`apply_to_groups(...)`, `filter_groups(...)`, and `ungroup()` to flatten back.

## Immutability and composition

All data classes are frozen and all operations return new instances. This makes
streams safe to share, easy to reason about, and naturally composable — the same
properties the [`EnergyProject`](energy_project.md) builder relies on.

```{seealso}
- [Streams API reference](../api/streams.md) for full signatures.
- [Guides: levelized cost](../guides/levelized_cost.md) and the
  `nuclear_uprate_project_primitives.py` example for end-to-end stream-level
  analyses.
```
