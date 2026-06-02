# Day-count & year fractions

Discounting needs a number of years between two dates — the $t$ in $(1+r)^t$. DCAF
computes it in {py:func}`dcaf.shared.timedelta_fractional_years`, with the method
chosen by the project's [day-count convention](../concepts/conventions.md).

## The three conventions

Let `start` and `end` be two dates.

### `actual/365-fixed`

$$
t = \frac{(\text{end} - \text{start})\ \text{in days}}{365}
$$

Simple calendar days over a fixed 365-day year.

### `actual/365-no-leap`

$$
t = \frac{(\text{calendar days, excluding any Feb 29})}{365}
$$

Normalizes away leap days so every project year is exactly 365 counted days.

### `actual/actual` (default, ISDA)

Splits the interval at calendar-year boundaries and divides each segment by the
number of days in *its* year (366 in a leap year):

$$
t = \sum_{\text{segments}} \frac{\text{days in segment}}{\text{days in that segment's year}}
$$

This is the most calendar-accurate convention and is the DCAF default.

## Example

```python
from datetime import date
from dcaf.shared.time import timedelta_fractional_years

round(timedelta_fractional_years(date(2025, 1, 1), date(2025, 7, 2)), 4)
# -> 0.4986   (actual/actual)
```

Year fractions are signed: if `end` precedes `start`, $t$ is negative, which makes
NPV terms compound forward instead of discounting.

## Why it matters

The same convention flows into NPV, IRR, LCOE, escalation, construction interest, and
operating-year proration. Setting it once on the builder
(`EnergyProject(day_count_convention=...)`) keeps the entire analysis internally
consistent.

```{note}
`actual/360` and `30/360` are not implemented yet.
```

```{seealso}
[Concepts: conventions](../concepts/conventions.md) · [Shared API reference](../api/shared.md).
```
