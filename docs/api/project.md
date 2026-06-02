# Project

The high-level project modeling API — **the library front door**. `EnergyProject`
is a fluent, immutable builder that composes generation, costs, financing, and tax
treatment into a `ProjectAnalysis`, from which you derive `ProjectMetrics` (NPV, IRR,
LCOE) and a `ProjectProForma`. Most workflows use only this subpackage.

These names are also re-exported from the package root, so `from dcaf import
EnergyProject` works.

```{eval-rst}
.. automodule:: dcaf.project
```
