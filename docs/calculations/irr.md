# IRR

The internal rate of return is the discount rate at which a project's NPV equals
zero. DCAF solves for it numerically in {py:func}`dcaf.metrics.irr`.

## What it solves

Find $r$ such that:

$$
\text{NPV}(r) = \sum_i \frac{C_i}{(1 + r)^{t_i}} = 0
$$

Only cash entries (`is_cash=True`) participate, and the stream must contain **both**
inflows and outflows — otherwise no root exists and a `ValueError` is raised.

## Algorithm

DCAF uses a **centroid-seeded Newton-Raphson** iteration:

1. **Initial guess.** Treat all inflows as one lump at their amount-weighted centroid
   date, and all outflows as another. Under that two-lump approximation the NPV = 0
   condition has a closed form:

   $$
   r_0 = \left(\frac{\sum C_\text{in}}{\sum |C_\text{out}|}\right)^{\frac{1}{t_\text{in} - t_\text{out}}} - 1
   $$

   (falling back to `0.1` if the centroids coincide or the ratio overflows).

2. **Newton-Raphson refinement.** Iterate $r \leftarrow r - \text{NPV}(r) /
   \text{NPV}'(r)$, where the derivative is computed in the same pass:

   $$
   \text{NPV}'(r) = -\sum_i \frac{t_i\,C_i}{(1 + r)^{t_i + 1}}
   $$

3. **Convergence.** Stops when $|\text{NPV}(r)| < \text{tol} \cdot \sum_i |C_i|$. The
   tolerance is scaled by total magnitude so it works for both small and very large
   streams. Rates are clamped above $-1$.

## Worked example

```python
from datetime import date
from dcaf.streams import CashFlow, CashFlowStream
from dcaf.metrics import irr

stream = CashFlowStream([
    CashFlow(-1000.0, date(2025, 1, 1)),
    CashFlow(  600.0, date(2026, 1, 1)),
    CashFlow(  600.0, date(2027, 1, 1)),
])
round(irr(stream), 4)
# -> 0.1307
```

In the project workflow, IRR is surfaced as `ProjectMetrics.xirr` (it is `None` when
the cash-only project stream has no solvable rate).

```{seealso}
[Metrics API reference](../api/metrics.md) · [DCF & NPV](dcf_npv.md).
```
