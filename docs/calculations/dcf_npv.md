# DCF & NPV

Net present value is the foundation of every DCAF metric. It discounts each dated
amount back to a common valuation date and sums the result.

## Formula

For each cash flow with amount $C_i$ on date $d_i$:

$$
\text{NPV} = \sum_i \frac{C_i}{(1 + r)^{t_i}}
$$

where:

- $r$ is the annual discount rate (a decimal, e.g. `0.10`),
- $t_i$ is the **year fraction** from the valuation date to $d_i$ under the chosen
  [day-count convention](conventions.md). $t_i$ may be negative for amounts before
  the valuation date, in which case the term compounds forward instead of
  discounting.

This is implemented once, in {py:func}`dcaf.metrics.npv`, and is the single primitive
behind `CashFlowStream.npv` and `GenerationStream.discounted_sum`:

```python
one_plus_r = 1.0 + rate
total = 0.0
for amount, d in values:
    t = timedelta_fractional_years(valuation_date, d, convention)
    total += amount / one_plus_r**t
return total
```

## Worked example

```python
from datetime import date
from dcaf.metrics import npv

# Invest $1,000 today, receive $1,100 in one year, discounted at 10%.
npv(
    [(-1000.0, date(2025, 1, 1)), (1100.0, date(2026, 1, 1))],
    rate=0.10,
    valuation_date=date(2025, 1, 1),
)
# -> 0.0  (the future $1,100 discounts exactly to $1,000)
```

## Choices that affect the result

- **Valuation date** — the reference point. Amounts on the valuation date are
  undiscounted; earlier amounts compound forward.
- **Discount rate** — set directly with `.discount_rate(rate=)` or derived from a
  [WACC](../api/project.md) via `.wacc(...)`.
- **Day-count convention** — determines $t_i$; see
  [day-count & year fractions](conventions.md).

```{seealso}
[Metrics API reference](../api/metrics.md).
```
