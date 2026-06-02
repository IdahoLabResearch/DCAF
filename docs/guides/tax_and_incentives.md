# Tax & incentives

DCAF models income tax on revenue net of deductible expenses, and supports the two
principal clean-energy credits. With the builder, you declare each piece and DCAF
wires the taxable-income and liability calculation together.

## Tax rate

```python
project = project.tax(rate=0.21)                  # losses generate no liability
project = project.tax(rate=0.21, allow_refund=True)  # losses generate refunds
```

`.tax(...)` automatically nets all taxable revenue against every deductible component
(operating costs, debt interest, depreciation) per year. See
[Calculations: tax & incentives](../calculations/tax.md) for the formulas.

## Depreciation

Depreciation creates non-cash deductions that lower taxable income:

```python
# MACRS — choose a property class and convention
project = project.depreciation_macrs(property_class=15, convention="half-year")

# or variable declining balance
project = project.depreciation_vdb(life=20)
```

## Production Tax Credit

A per-MWh credit on eligible generation for a fixed number of years:

```python
project = project.production_tax_credit(rate_per_unit=27.50, years=10, escalation=0.025)
```

## Investment Tax Credit

A one-time credit as a fraction of capital cost:

```python
project = project.investment_tax_credit(rate=0.30)
```

```{important}
Taking the ITC reduces the depreciable basis by half the credit rate (the IRS 50%
basis-reduction rule). When you configure both an ITC and MACRS on the same project,
DCAF applies this automatically. The underlying functions — `itc()`,
`itc_adjusted_basis()`, `macrs_schedule()` — are documented in
[Calculations: tax & incentives](../calculations/tax.md#itc-and-basis-reduction).
```

## Inspecting the tax line

After `.analyze()`, the taxable income and tax cash flows are available directly:

```python
analysis = project.analyze()
print(analysis.taxable_income.sum())
print(analysis.taxes.sum())
```

```{seealso}
[Tax API reference](../api/tax.md) · [Calculations: depreciation](../calculations/depreciation.md).
```
