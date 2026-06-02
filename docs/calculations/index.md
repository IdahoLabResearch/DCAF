# Calculations

This section documents the DCF model and every underlying calculation so that no
result is a black box. Each page states the formula or algorithm, points to the
implementation, and links to the relevant API.

DCAF deliberately keeps a **single source of truth** for each calculation: the
project builder and stream methods all delegate to the same primitives documented
here.

| Topic | What it covers |
|-------|----------------|
| [DCF & NPV](dcf_npv.md) | The discounting formula at the heart of every metric. |
| [IRR](irr.md) | Internal rate of return via Newton-Raphson. |
| [LCOE](lcoe.md) | Levelized cost as the break-even price solve. |
| [Depreciation](depreciation.md) | MACRS rate tables and variable-declining-balance. |
| [Tax & incentives](tax.md) | Taxable income, liability, PTC, ITC, basis reduction. |
| [Financing](financing.md) | Level-payment amortization and interest/principal split. |
| [Escalation](escalation.md) | Compound growth of prices and costs over time. |
| [Day-count & year fractions](conventions.md) | How dates become the `t` used in discounting. |
