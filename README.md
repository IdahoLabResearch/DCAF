# DCAF

The Discounted Cash Flow Analysis Framework is a professional financial engineering
library for nuclear energy project cost and metric calculations, with a focus on
Extended Power Uprate (EPU) projects.

## Installation

Install the `dcaf-idaholab` distribution from PyPI:

```bash
uv add dcaf-idaholab
# or: pip install dcaf-idaholab
```

The Python import package remains `dcaf`:

```python
from dcaf import EnergyProject
```

## Overview

DCAF provides high-fidelity cost modeling and financial analysis tools
specifically designed for nuclear energy projects. The library enables detailed
financial analysis including:

- Net Present Value (NPV), Discounted Cashflow (DCF) and Internal Rate of Return
  (IRR) calculations
- Financing cost modeling with accurate interest calculations
- Tax and incentive analysis
  - Production Tax Credits
  - Investment Tax Credits
  - MACRS and VDB depreciation scheduling
- Pro-forma generation for detailed financial projections
- Comprehensive cost modeling for EPU projects

## Day Count Conventions

Project analyses default to `actual/actual`, which uses real calendar days and
splits annual-rate calculations by calendar year so leap-year days use a 366-day
denominator. Use `actual/365-no-leap` when you intentionally want normalized
365-day project years that exclude Feb. 29 from elapsed-day counts. Use
`actual/365-fixed` when you need actual calendar days divided by 365.

The project-wide default is set with `EnergyProject(day_count_convention=...)`
or `.day_count_convention(...)`, and it flows into NPV, IRR, LCOE, escalation,
construction interest, operating-year proration, and capacity-hour generation.

Bank-loan and bond-style conventions such as `actual/360` and `30/360` are
useful future additions, but they are not implemented yet.
