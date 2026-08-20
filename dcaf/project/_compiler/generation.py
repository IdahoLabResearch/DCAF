# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Generation-domain build functions."""

from __future__ import annotations

from dcaf.project._builder_config import CapacityGenerationConfig
from dcaf.project._compiler import scheduling
from dcaf.project._compiler.context import AnalysisContext
from dcaf.shared.time import elapsed_hours
from dcaf.streams.generation import Generation, GenerationStream


def build_generation(context: AnalysisContext) -> GenerationStream:
    """Build the generation stream from project configuration.

    Returns an empty stream when generation is unconfigured.
    """
    config = context.config
    generation = config.generation
    if generation is None:
        if config.generation_outages:
            raise ValueError("generation_outage requires generation to be configured")
        return GenerationStream()
    if isinstance(generation, GenerationStream):
        base_generation = generation
    else:
        start = (
            generation.start
            if generation.start is not None
            else context.require_timeline_date("operations_start")
        )
        ops_start = config.timeline.operations_start
        ops_end = config.timeline.operations_end
        schedule = scheduling.operating_schedule(
            context,
            "generation",
            start=start,
            periods=generation.periods,
            frequency=config.timeline.frequency,
            phase_start=ops_start,
            phase_end=ops_end,
        )
        if schedule:
            period_start = schedule[0].start
            period_end = schedule[-1].end
            hours = elapsed_hours(
                period_start,
                period_end,
                config.day_count_convention,
            )
            base_generation = GenerationStream(
                [
                    Generation(
                        amount_mwh=(generation.capacity_mw * generation.capacity_factor * hours),
                        label=generation.label,
                        period_start=period_start,
                        period_end=period_end,
                    )
                ]
            )
        else:
            base_generation = GenerationStream()

    outage_generation = build_generation_outages(context)
    if not outage_generation.entries:
        return base_generation
    return GenerationStream.from_streams(base_generation, outage_generation).sort()


def build_generation_outages(context: AnalysisContext) -> GenerationStream:
    """Build negative generation entries for configured modeled outages."""
    config = context.config
    if not config.generation_outages:
        return GenerationStream()

    generation = config.generation
    capacity_defaults: CapacityGenerationConfig | None = (
        generation if isinstance(generation, CapacityGenerationConfig) else None
    )
    ops_start = config.timeline.operations_start
    ops_end = config.timeline.operations_end

    outage_streams: list[GenerationStream] = []
    for outage in config.generation_outages:
        if ops_start is not None and outage.start < ops_start:
            raise ValueError(f"generation_outage {outage.name!r} starts before operations_start")
        if ops_end is not None and outage.end > ops_end:
            raise ValueError(f"generation_outage {outage.name!r} ends after operations_end")

        capacity_mw = outage.capacity_mw
        if capacity_mw is None and capacity_defaults is not None:
            capacity_mw = capacity_defaults.capacity_mw
        if capacity_mw is None:
            raise ValueError(
                f"generation_outage {outage.name!r} requires capacity_mw "
                "when capacity-based generation is not configured"
            )

        capacity_factor = outage.capacity_factor
        if capacity_factor is None and capacity_defaults is not None:
            capacity_factor = capacity_defaults.capacity_factor
        if capacity_factor is None:
            raise ValueError(
                f"generation_outage {outage.name!r} requires capacity_factor "
                "when capacity-based generation is not configured"
            )

        outage_streams.append(
            GenerationStream.from_outage(
                capacity_mw=capacity_mw,
                capacity_factor=capacity_factor,
                start=outage.start,
                end=outage.end,
                capacity_reduction=outage.capacity_reduction,
                label=outage.label,
                day_count_convention=config.day_count_convention,
            )
        )
    return GenerationStream.from_streams(*outage_streams)
