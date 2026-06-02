# Guides

These guides are task-focused walkthroughs. They build on the five runnable scripts
in the repository's [`examples/`](https://github.com/) directory, which all model the
same scenario — a 220 MW nuclear uprate with a 5-year construction period, 35
operating years, 50/50 debt/equity financing, a 21% tax rate, and a 10-year PTC — at
**five levels of the API**, from the high-level builder down to raw stream
primitives.

## Choosing an abstraction level

Start at the top. Drop to a lower level only when the level above can't express what
you need.

| Example script | Level | Use it when… |
|----------------|-------|--------------|
| `nuclear_uprate_project_simple.py` | High-level builder, minimal config | Quick feasibility studies or sensitivity sweeps where exact timing doesn't matter. |
| `nuclear_uprate_project.py` | High-level builder, full config | **Most production analyses** — dated timeline, financing, escalation, named outages. |
| `nuclear_uprate_project_midlevel.py` | Mid-level functions | The builder's structure doesn't fit; you mix external data with library schedules. |
| `nuclear_uprate_project_builders.py` | Mid-level builder classes | Advanced config: custom spend profiles, structured debt service, bespoke incentives. |
| `nuclear_uprate_project_primitives.py` | Raw stream primitives | Any data shape; clearest view of what the higher layers produce under the hood. |

The first two use only `EnergyProject` — the primary interface. The last three drop
into the [stream primitives](../concepts/streams.md) and standalone functions, and
are **advanced** paths.

```{important}
The five examples produce *different* NPVs by design — each makes different modeling
choices (financing, escalation, depreciation). The divergence reflects those choices,
not errors.
```

## Guide topics

- [High-level project](high_level_project.md) — the recommended end-to-end workflow.
- [Escalation & financing](escalation_and_financing.md) — growing costs and debt service.
- [Tax & incentives](tax_and_incentives.md) — depreciation, PTC, ITC, and taxes.
- [Outages](outages.md) — modeling lost generation and replacement power.
- [Levelized cost](levelized_cost.md) — computing and interpreting LCOE.
