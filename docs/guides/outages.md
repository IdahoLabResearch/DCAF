# Outages

Outages reduce or interrupt generation and carry costs — lost revenue, mobilization,
and replacement power. DCAF distinguishes two cases.

## Construction outages

During an Extended Power Uprate, refueling outages on the *existing baseline plant*
are often extended to perform uprate work. The dominant cost is lost revenue at the
**baseline** capacity (not the uprate). `.construction_outage(...)` models the lost
revenue plus optional fixed and per-day costs as distinct, labeled line items:

```python
project = project.construction_outage(
    name="refueling_1",
    start=date(2028, 4, 1),
    end=date(2028, 4, 11),            # half-open: a 10-day extension
    capacity_mw=1000.0,               # baseline plant, not the 220 MW uprate
    capacity_factor=0.92,
    fixed_cost=500_000.0,             # mobilization / craft labor surge
    cost_per_day=50_000.0,            # replacement-power premium
    lost_revenue_label="Refuel #1 Lost Revenue",
    fixed_cost_label="Refuel #1 Mobilization",
    daily_cost_label="Refuel #1 Replacement Power",
)
```

Each outage becomes a component named `construction_outage:<name>`, so you can call
`.construction_outage(...)` multiple times and read each back individually:

```python
analysis = project.analyze()
analysis.cashflow_components["construction_outage:refueling_1"].sum()
```

## Generation outages

For outages during the operating period, `.generation_outage(start=, end=, ...)`
reduces the project's generation over the window, which flows through to revenue and
any per-MWh credits.

## Advanced: physical vs. financial views

At the stream level, {py:func}`dcaf.finance.outage.generator_outage` produces the
physical (negative MWh) representation and
{py:func}`dcaf.finance.outage.construction_outage` produces the financial
(lost-revenue + cost) representation of the same event — useful when you need to wire
an outage's tax shield into a hand-assembled deductions stream.

```{seealso}
[Finance API reference](../api/finance.md) · [Concepts: conventions](../concepts/conventions.md)
for the half-open interval rule.
```
