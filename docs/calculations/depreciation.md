# Depreciation

Depreciation produces *accrual* deductions (`is_cash=False`) that reduce taxable
income without being cash outflows themselves. DCAF implements two methods in
{py:mod}`dcaf.tax`.

## MACRS

{py:func}`dcaf.tax.macrs_schedule` generates a Modified Accelerated Cost Recovery
System schedule from published IRS percentage tables. Each year's deduction is:

$$
D_y = \text{cost basis} \times \text{rate}_y
$$

- **Property classes:** 3, 5, 7, 10, 15, and 20 years.
- **Conventions:** `"half-year"` (default) and `"mid-quarter"`. The half-year
  convention treats assets as placed in service mid-year, producing one extra
  recovery year (e.g. a 5-year class yields 6 deductions). Rate tables live in
  {py:func}`dcaf.tax.get_macrs_rates` and
  {py:func}`dcaf.tax.get_macrs_mid_quarter_rates`.

The rates sum to 1.0, so the deductions recover the full basis.

```python
from datetime import date
from dcaf.tax import macrs_schedule

stream = macrs_schedule(1_000_000, date(2030, 1, 1), 5)
[(cf.date, cf.amount) for cf in stream[:3]]
# -> [(date(2030, 1, 1), -200000.0),   # 20.00%
#     (date(2031, 1, 1), -320000.0),   # 32.00%
#     (date(2032, 1, 1), -192000.0)]   # 19.20%
```

## Variable declining balance (VDB)

{py:func}`dcaf.tax.vdb` / {py:func}`dcaf.tax.vdb_schedule` implement Excel-compatible
declining-balance depreciation with an optional switch to straight-line. For each
period the deduction is the larger of:

- **Declining balance:** $\text{remaining basis} \times \dfrac{\text{factor}}{\text{life}}$
  (with `factor=2.0` giving double-declining balance), and
- **Straight line:** $\dfrac{\text{remaining basis} - \text{salvage}}{\text{life} - \text{elapsed}}$,

unless `no_switch=True` forces pure declining balance.

```python
from dcaf.tax import vdb

round(vdb(35000, 7500, 36, 10, 20), 2)
# -> 8603.8   (depreciation accrued over periods 10..20)
```

## Interaction with the ITC

When an Investment Tax Credit is taken, the depreciable basis is reduced first — see
[Tax & incentives](tax.md#itc-and-basis-reduction).

```{seealso}
[Tax API reference](../api/tax.md) · builder methods `.depreciation_macrs(...)` and
`.depreciation_vdb(...)`.
```
