# Levelized cost

The levelized cost of energy (LCOE) is the break-even electricity price — the $/MWh
at which the project's NPV is zero. It is the natural way to compare projects with
different cost and generation profiles.

## From the project metrics

The simplest path: read it off `ProjectMetrics`.

```python
analysis = project.analyze()
metrics = analysis.metrics(discount_rate=0.10, valuation_date=date(2032, 1, 1))
print(f"LCOE: ${metrics.levelized_cost:,.2f}/MWh")
```

DCAF computes this by solving for the price that zeroes NPV, recomputing taxes at
each trial price (revenue changes taxable income). The full algorithm is in
[Calculations: LCOE](../calculations/lcoe.md). `levelized_cost` is `None` when there
is no discounted generation to levelize against.

## Interpreting the result

- An LCOE **below** your expected sale price means the project clears its cost of
  capital at that price — consistent with a positive NPV.
- Because taxes and credits are included, the LCOE reflects *after-incentive*
  economics. Removing a PTC, for example, raises the LCOE.

## Advanced: calling `lcoe()` directly

When you assemble components outside the builder, call {py:func}`dcaf.metrics.lcoe`
with a unit-price (`$1/MWh`) basis stream and your component group. This is the
stream-level path shown in `examples/nuclear_uprate_project_primitives.py`. See the
[worked example](../calculations/lcoe.md#worked-example).

```{seealso}
[Metrics API reference](../api/metrics.md) · [Calculations: DCF & NPV](../calculations/dcf_npv.md).
```
