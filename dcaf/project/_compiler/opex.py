# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Fixed OPEX and variable-cost build functions."""

from __future__ import annotations

from datetime import date

from dcaf.finance.escalation import ConstantRateEscalation, EscalationPolicy
from dcaf.project._builder_config import EscalationSettings, FixedOpexConfig, VariableCostConfig
from dcaf.project._compiler import scheduling
from dcaf.project._compiler.context import AnalysisContext, ComponentAccumulator
from dcaf.shared.types import DayCountConvention, ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.streams.generation import GenerationStream


def build_fixed_opex(context: AnalysisContext, fixed: FixedOpexConfig) -> CashFlowStream:
    """Build a fixed OPEX cash-flow stream from *fixed* configuration.

    Each period's amount is scaled by the escalation factor and the
    partial-period fraction.
    """
    config = context.config
    frequency = fixed.frequency if fixed.frequency is not None else config.timeline.frequency
    start = (
        fixed.start
        if fixed.start is not None
        else context.require_timeline_date("operations_start")
    )
    timing = fixed.timing or config.timeline.timing
    ops_start = config.timeline.operations_start
    ops_end = config.timeline.operations_end
    schedule = scheduling.operating_schedule(
        context,
        "fixed_opex",
        start=start,
        periods=fixed.periods,
        frequency=frequency,
        timing=timing,
        phase_start=ops_start,
        phase_end=ops_end,
    )
    escalation = context.effective_escalation(fixed.escalation)
    escalation_policy = recurring_policy(
        start,
        escalation,
        config.day_count_convention,
    )
    entries: list[CashFlow] = []
    for modeled_period in schedule:
        entries.append(
            CashFlow(
                amount=(
                    -abs(fixed.amount)
                    * escalation_policy.factor(modeled_period.event_date)
                    * modeled_period.fraction
                ),
                date=modeled_period.event_date,
                label=fixed.label,
                is_cash=True,
                pro_forma_category=ProFormaCategory.OPERATING_COST,
                tax_treatment=TaxTreatment.DEDUCTIBLE,
            )
        )
    return CashFlowStream(entries)


def build_fixed_opex_components(
    context: AnalysisContext, accumulator: ComponentAccumulator
) -> None:
    """Build every configured fixed OPEX item and register it under its named key."""
    for cost_name, cost_config in context.config.fixed_opex_items.items():
        accumulator.add_named(
            "fixed_opex",
            cost_name,
            build_fixed_opex(context, cost_config),
        )


def build_variable_cost(
    context: AnalysisContext,
    variable: VariableCostConfig,
    generation: GenerationStream,
) -> CashFlowStream:
    """Build a variable cost stream by applying *variable* rate to generation.

    Returns an empty stream when generation is unavailable.
    """
    config = context.config
    if not generation.entries:
        return CashFlowStream()
    escalation = context.effective_escalation(variable.escalation)
    if escalation.policy is not None:
        return generation.to_cost(
            rate_per_mwh=variable.rate_per_unit,
            label=variable.label,
            escalation_policy=escalation.policy,
            frequency=config.timeline.frequency,
            timing=config.timeline.timing,
            day_count_convention=config.day_count_convention,
        )
    return generation.to_cost(
        rate_per_mwh=variable.rate_per_unit,
        label=variable.label,
        escalation=escalation.escalation,
        escalation_period=escalation.escalation_period,
        amount_reference_date=escalation.amount_reference_date,
        day_count_convention=config.day_count_convention,
        frequency=config.timeline.frequency,
        timing=config.timeline.timing,
    )


def build_variable_cost_components(
    context: AnalysisContext,
    generation: GenerationStream,
    accumulator: ComponentAccumulator,
) -> None:
    """Build every configured variable cost item and register it under its named key."""
    for vc_name, vc_config in context.config.variable_cost_items.items():
        accumulator.add_named(
            "variable_cost",
            vc_name,
            build_variable_cost(context, vc_config, generation),
        )


def recurring_policy(
    start: date,
    escalation: EscalationSettings,
    day_count_convention: DayCountConvention,
) -> EscalationPolicy:
    """Resolve recurring-cost escalation settings into a concrete policy."""
    if escalation.policy is not None:
        return escalation.policy
    return ConstantRateEscalation(
        reference_date=start
        if escalation.amount_reference_date is None
        else escalation.amount_reference_date,
        rate=escalation.escalation,
        period=escalation.escalation_period,
        day_count_convention=day_count_convention,
    )
