# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Resolved analysis context and cross-domain component accumulation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace as dc_replace
from datetime import date
from math import isclose
from typing import Literal

from dcaf.project._builder_config import (
    CapacityGenerationConfig,
    ConstructionScheduleConfig,
    EscalationSettings,
    ProjectConfig,
    constant_annual_escalation_rate,
    effective_escalation,
)
from dcaf.project.timeline import ProjectTimeline
from dcaf.streams.cashflows import CashFlowStream
from dcaf.streams.generation import GenerationStream


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Resolved configuration and helpers for one project analysis compile."""

    source_config: ProjectConfig
    timeline: ProjectTimeline = field(init=False)
    config: ProjectConfig = field(init=False)

    def __post_init__(self) -> None:
        # object.__setattr__ bypasses the frozen-dataclass write guard, since these
        # derived fields must be computed after init but the class stays immutable.
        timeline = self.resolve_timeline(self.source_config)
        object.__setattr__(self, "timeline", timeline)
        object.__setattr__(self, "config", dc_replace(self.source_config, timeline=timeline))

    def require_timeline_date(
        self,
        field_name: Literal["construction_start", "operations_start", "operations_end"],
    ) -> date:
        """Return the named timeline date or raise ``ValueError`` if it is not set."""
        value = getattr(self.timeline, field_name)
        if value is None:
            raise ValueError(f"timeline.{field_name} is required for this project configuration")
        return value

    def effective_escalation(self, local: EscalationSettings) -> EscalationSettings:
        """Return the local escalation settings or the project default."""
        return effective_escalation(local, self.config.default_escalation)

    @staticmethod
    def resolve_timeline(config: ProjectConfig) -> ProjectTimeline:
        """Assemble an internal timeline from generation and construction configs."""
        operations_start: date | None = None
        operations_end: date | None = None
        construction_start: date | None = None

        gen = config.generation
        if isinstance(gen, CapacityGenerationConfig):
            if gen.operations_start is not None:
                operations_start = gen.operations_start
            if gen.operations_end is not None:
                operations_end = gen.operations_end
        elif isinstance(gen, GenerationStream):
            if gen.entries:
                operations_start = min(entry.period_start for entry in gen.entries)
                operations_end = max(entry.period_end for entry in gen.entries)

        con = config.construction
        if isinstance(con, ConstructionScheduleConfig):
            if con.construction_start is not None:
                construction_start = con.construction_start

        return ProjectTimeline(
            construction_start=construction_start,
            operations_start=operations_start,
            operations_end=operations_end,
            frequency=config.frequency,
            timing=config.timing,
            day_count_convention=config.day_count_convention,
        )


@dataclass(slots=True)
class ComponentAccumulator:
    """Accumulate generated component streams while skipping empty streams."""

    streams: dict[str, CashFlowStream] = field(default_factory=dict)

    def add(self, key: str, stream: CashFlowStream) -> None:
        """Insert a generated stream when it contains entries."""
        if stream.entries:
            self._insert(key, stream)

    def add_named(self, prefix: str, name: str, stream: CashFlowStream) -> None:
        """Insert a generated stream using the default or named component key."""
        key = prefix if name == "default" else f"{prefix}:{name}"
        self.add(key, stream)

    def add_custom(self, name: str, stream: CashFlowStream) -> None:
        """Insert a caller-provided stream, preserving existing empty-stream behavior."""
        self._insert(name, stream)

    def _insert(self, key: str, stream: CashFlowStream) -> None:
        """Insert one component stream without allowing replacement."""
        if key in self.streams:
            raise ValueError(f"cashflow component key {key!r} was produced more than once")
        self.streams[key] = stream


def infer_levelized_cost_escalation_rate(context: AnalysisContext) -> float | None:
    """Infer a shared annual escalation rate for constant-dollar LCOE when possible."""
    config = context.config
    inferred_rates: list[float] = []

    def collect(local: EscalationSettings) -> bool:
        effective = context.effective_escalation(local)
        rate = constant_annual_escalation_rate(effective)
        if rate is None:
            return False
        inferred_rates.append(rate)
        return True

    if (
        isinstance(config.construction, ConstructionScheduleConfig)
        and config.construction.overnight_cost != 0.0
    ):
        if not collect(config.construction.escalation):
            return None
    for recurring_cost in config.fixed_opex_items.values():
        if recurring_cost.amount != 0.0:
            if not collect(recurring_cost.escalation):
                return None
    if config.ptc is not None and config.ptc.rate_per_unit != 0.0:
        if not collect(config.ptc.escalation):
            return None

    if not inferred_rates:
        return 0.0

    first_rate = inferred_rates[0]
    if any(
        not isclose(rate, first_rate, rel_tol=0.0, abs_tol=1e-12) for rate in inferred_rates[1:]
    ):
        return None
    return first_rate
