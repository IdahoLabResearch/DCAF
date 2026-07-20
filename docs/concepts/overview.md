# Overview

DCAF turns a project description into discounted-cash-flow results. The mental
model is a short pipeline:

```text
        EnergyProject  (the front door — fluent, immutable builder)
                │
                │  .analyze()
                ▼
        ProjectAnalysis  (every cash-flow component + generation, taxes)
                │
        ┌───────┴────────┐
        ▼                ▼
   ProjectMetrics    ProjectProForma
   (NPV, IRR, LCOE)  (period-by-period statement)
```

## Start at the top: `EnergyProject`

`EnergyProject` is the **primary interface** and the right starting point for almost
everything. You describe a project by chaining configuration methods —
`.generation(...)`, `.construction(...)`, `.generation_revenue(...)`,
`.tax(...)`, and so on — and then call `.analyze()`.

It is a *fluent, immutable builder*: every method returns a new project, so you can
branch and reuse configurations safely. See
[The EnergyProject builder](energy_project.md) for the full method reference.

## What you get back

- {py:class}`~dcaf.ProjectAnalysis` — the compiled project. It holds the generation
  profile, every named cash-flow component (`analysis.cashflow_components`), the
  taxable income and taxes, and the combined `analysis.cashflows`.
- {py:class}`~dcaf.ProjectMetrics` — `analysis.metrics(...)` discounts everything to a
  valuation date and returns NPV, IRR (`xirr`), total/discounted generation, and
  levelized cost.
- {py:class}`~dcaf.ProjectProForma` — `analysis.pro_forma(period="year")` aggregates
  the cash flows into a period-by-period financial statement you can print or export
  with `.to_csv(...)`.

## Underneath: streams

Everything the builder produces is made of **streams** — `CashFlowStream` (dated
monetary amounts) and `GenerationStream` (dated MWh). These are the lower-level
primitives. You rarely touch them through the builder path, but they are fully
public for advanced use cases: external data, bespoke incentive structures, or
transformations the builder doesn't expose. See [Streams](streams.md).

## Transparency

DCAF is designed so nothing is a black box. Every metric traces back to a documented
formula, and you can always inspect the individual components that feed it. The
[Calculations](../calculations/index.md) section documents the DCF model and every
underlying calculation — NPV, IRR, LCOE, depreciation, tax, financing, and
escalation.

```{seealso}
The [architecture diagram](../architecture_diagram.md) shows the full class model
and how the layers relate.
```
