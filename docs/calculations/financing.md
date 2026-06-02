# Financing

Debt is modeled as a level-payment amortization schedule, decomposed into interest
and principal. The implementation is {py:class}`dcaf.finance.amortization.AmortizationSchedule`.

## Level payment

Given a `principal`, `annual_rate`, `term` (number of payments), and payment
`frequency`, the periodic rate is the annual rate divided by payments per year
(`year` → 1, `quarter` → 4, `month` → 12, `day` → 365). The fixed payment is the
standard annuity (PMT) formula:

$$
\text{PMT} = P \cdot \frac{i\,(1 + i)^n}{(1 + i)^n - 1}
$$

where $P$ is the principal, $i$ is the periodic rate, and $n$ is the term.

## Interest / principal decomposition

Each period splits the fixed payment into interest on the outstanding balance and the
remainder, which retires principal:

$$
\text{interest}_k = B_{k-1} \cdot i
\qquad
\text{principal}_k = \text{PMT} - \text{interest}_k
\qquad
B_k = B_{k-1} - \text{principal}_k
$$

So the payment is constant, interest decreases each period, principal increases, and
the invariant `interest + principal = total` holds every period.
`AmortizationSchedule` exposes the three streams separately (`.total`, `.interest`,
`.principal`), with interest tagged `TaxTreatment.DEDUCTIBLE` and principal
`TaxTreatment.NONE` — so interest automatically contributes to the
[tax shield](tax.md).

```python
from datetime import date
from dcaf.finance.amortization import AmortizationSchedule

schedule = AmortizationSchedule.build(
    principal=100_000.0, annual_rate=0.05, term=120, start_date=date(2026, 1, 15),
)
schedule.total.count()   # 120 equal payments
```

## Builder rules

For non-standard structures, {py:meth}`AmortizationSchedule.builder` returns a fluent
`AmortizationBuilder` supporting:

- `.interest_only(periods)` — principal is zero during the window; interest is
  constant on the full balance.
- `.interest_free(from_period, to_period)` — interest waived (principal still paid);
  note period indices here are **inclusive** on both ends.
- `.rate_change(from_period, annual_rate)` — re-prices the remaining balance, e.g. to
  model a refinancing.

In the project workflow, `.construction_financing(debt_fraction=,
amortization_rate=, amortization_term=)` builds and attaches the schedule for you;
`.debt_schedule(...)` accepts a pre-built one.

```{seealso}
[Finance API reference](../api/finance.md) · [Guides: escalation & financing](../guides/escalation_and_financing.md).
```
