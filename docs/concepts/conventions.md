# Conventions

A few conventions are applied consistently across DCAF. Knowing them prevents the
most common modeling mistakes.

## Date intervals are half-open `[start, end)`

Every date-bounded interval in the public API uses **inclusive start, exclusive
end** — matching Python's `range`, slicing, and `datetime` arithmetic.

To model the full calendar year 2026, write:

```python
operations_start=date(2026, 1, 1)
operations_end=date(2027, 1, 1)   # first day AFTER the interval
```

Do **not** use `date(2026, 12, 31)` to mean "the last day of 2026" — always use the
first day of the following period as the exclusive upper bound. This applies to
operations, construction, and outage windows, and to `date_range(...)` on streams.

```{note}
Integer *period indices* (for example `AmortizationBuilder.interest_free(from_period,
to_period)`) are inclusive on both ends, because they select discrete elements
rather than spans of time.
```

## Periods, frequency, and timing

- **Period / frequency** — one of `"day"`, `"month"`, `"quarter"`, `"year"`.
  Recurring items (opex, generation, debt service) are booked at this granularity.
- **Timing convention** — where within each period an event is dated:
  - `"end"` (default) books at the end of the calendar period, capped by the phase
    boundary.
  - `"begin"` books at the start, floored by the phase start.
  - `"middle"` books at the midpoint — a better approximation for continuous spend
    or generation when discounting.

These are set on the builder: `EnergyProject(frequency="year", timing="end")`.

## Day-count conventions

NPV, IRR, LCOE, escalation, construction interest, and operating-year proration all
convert calendar dates into **year fractions** using a configurable day-count
convention. The default is `"actual/actual"`.

| Convention | Year fraction `t` between two dates |
|------------|-------------------------------------|
| `"actual/actual"` (default) | ISDA: each calendar year contributes `days_in_segment / days_in_that_year` (366 in leap years). |
| `"actual/365-no-leap"` | `(calendar days, excluding Feb 29) / 365`. |
| `"actual/365-fixed"` | `(calendar days) / 365`. |

Set it project-wide with `EnergyProject(day_count_convention="actual/actual")` or
`.day_count_convention(...)`. It flows into every downstream calculation, so a single
setting keeps the whole analysis consistent.

```python
from datetime import date
from dcaf.shared.time import timedelta_fractional_years

timedelta_fractional_years(date(2025, 1, 1), date(2025, 7, 2))  # 0.4986 (actual/actual)
```

```{note}
Bank-loan/bond conventions such as `actual/360` and `30/360` are not implemented
yet.
```

```{seealso}
[Calculations: day-count and year fractions](../calculations/conventions.md) for the
exact ISDA algorithm, and the [Shared API reference](../api/shared.md) for the
underlying utilities.
```
