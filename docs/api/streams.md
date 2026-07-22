# Streams

Lower-level financial and generation primitives. `CashFlow` and `Generation` are
frozen entry values. `CashFlowStream` and `GenerationStream` hold those values in
public, mutable `entries` lists, while their stream-producing methods return new
containers instead of mutating the source. These are the building blocks the
[project builder](project.md) produces under the hood. Reach for them directly when
you need fine-grained control — custom data sources, bespoke incentive structures,
or transformations the builder does not expose.

```{eval-rst}
.. automodule:: dcaf.streams
```
