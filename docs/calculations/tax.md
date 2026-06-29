# Tax & incentives

DCAF models income tax on an accrual basis and supports the two principal clean-energy
credits. All of this lives in {py:mod}`dcaf.tax`.

## Taxable income

{py:func}`dcaf.tax.compute_taxable_income` combines a revenue stream and a deductible
stream, groups the flows by year, and nets them per period:

$$
\text{taxable income}_y = \sum (\text{taxable}_y) + \sum (\text{deductible}_y)
$$

Deductible amounts are negative, so the sum is revenue minus deductions. The result
is an **accrual** stream (`is_cash=False`) dated at each period end; losses (negative
income) are preserved.

## Tax liability

{py:func}`dcaf.tax.tax_liability` applies a scalar rate to taxable income:

$$
\text{tax}_y = -\,\text{rate} \times \text{taxable income}_y
$$

- With `allow_refund=False` (default) only positive income generates a liability;
  losses produce no cash.
- With `allow_refund=True`, losses generate positive (refund) cash flows.

In the builder, `.tax(rate=, allow_refund=)` wires revenue and every deductible
component (opex, interest, depreciation, …) into this calculation automatically.

## Production Tax Credit (PTC)

{py:func}`dcaf.tax.ptc` converts eligible generation into positive credit cash flows:

$$
\text{PTC}_y = \text{MWh}_y \times \text{rate} \times (1 + \text{escalation})^{\,y}
$$

Eligibility is limited to the first `years` calendar years of generation.
PTC flows are categorized as tax credits with neutral tax treatment, so they
appear in project cash flows but do not increase taxable income.

```python
from datetime import date
from dcaf.streams import Generation, GenerationStream
from dcaf.tax import ptc

gen = GenerationStream([
    Generation(1000.0, date(2030, 1, 1)),
    Generation(1000.0, date(2031, 1, 1)),
])
result = ptc(gen, rate_per_mwh=10.0, years=5, escalation=0.02)
[round(cf.amount, 1) for cf in result.entries]
# -> [10000.0, 10200.0]   # second year escalated 2%
```

## ITC and basis reduction

{py:func}`dcaf.tax.itc` computes a one-time Investment Tax Credit from capital
expenditure:

$$
\text{ITC} = \text{total capital basis} \times \text{rate}
$$

Per the IRS 50% basis-reduction rule, taking the ITC reduces the basis available for
depreciation. {py:func}`dcaf.tax.itc_adjusted_basis` returns:

$$
\text{adjusted basis} = \text{total basis} \times \left(1 - \frac{\text{rate}}{2}\right)
$$

So a $100 M basis with a 30% ITC yields a $30 M credit and an $85 M depreciable basis.
The typical workflow is `itc()` → `itc_adjusted_basis()` → `macrs_schedule(adjusted
basis, ...)`.

```{seealso}
[Tax API reference](../api/tax.md) · [Depreciation](depreciation.md) ·
[Guides: tax & incentives](../guides/tax_and_incentives.md).
```
