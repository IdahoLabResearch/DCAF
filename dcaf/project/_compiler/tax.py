# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tax-incentive, depreciation, and project-tax-liability build functions."""

from __future__ import annotations

from dcaf.project._builder_config import MacrsDepreciationConfig, VdbDepreciationConfig
from dcaf.project._compiler import scheduling
from dcaf.project._compiler.context import AnalysisContext
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlowStream
from dcaf.streams.generation import GenerationStream
from dcaf.tax.depreciation import macrs_schedule, vdb_schedule
from dcaf.tax.incentives import itc, itc_adjusted_basis, ptc
from dcaf.tax.liability import compute_taxable_income, tax_liability


def build_itc(context: AnalysisContext, construction_stream: CashFlowStream) -> CashFlowStream:
    """Build an ITC cash-flow stream from capital-cost construction flows.

    Returns an empty stream when no ITC rate is configured or construction
    has no capital-cost entries.
    """
    config = context.config
    if config.itc_rate is None or not construction_stream.entries:
        return CashFlowStream()
    capex_basis = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
    if not capex_basis.entries:
        return CashFlowStream()
    return itc(
        capex_stream=capex_basis,
        rate=config.itc_rate,
        placed_in_service=context.require_timeline_date("operations_start"),
    )


def build_ptc(context: AnalysisContext, generation: GenerationStream) -> CashFlowStream:
    """Build a PTC cash-flow stream from generation and PTC configuration.

    Returns an empty stream when no PTC is configured or generation is empty.
    """
    config = context.config
    if config.ptc is None or not generation.entries:
        return CashFlowStream()
    ptc_config = config.ptc
    escalation = context.effective_escalation(ptc_config.escalation)
    if escalation.policy is not None:
        return ptc(
            generation_stream=generation,
            rate_per_mwh=ptc_config.rate_per_unit,
            years=ptc_config.years,
            label=ptc_config.label,
            escalation_policy=escalation.policy,
            day_count_convention=config.day_count_convention,
            frequency=config.timeline.frequency,
            timing=config.timeline.timing,
        )
    return ptc(
        generation_stream=generation,
        rate_per_mwh=ptc_config.rate_per_unit,
        years=ptc_config.years,
        label=ptc_config.label,
        escalation=escalation.escalation,
        escalation_period=escalation.escalation_period,
        amount_reference_date=escalation.amount_reference_date,
        day_count_convention=config.day_count_convention,
        frequency=config.timeline.frequency,
        timing=config.timeline.timing,
    )


def build_depreciation(
    context: AnalysisContext, construction_stream: CashFlowStream
) -> CashFlowStream:
    """Build a depreciation stream from construction capital costs and depreciation config.

    Applies ITC basis adjustment when an ITC rate is configured. Returns an
    empty stream when depreciation is unconfigured or the cost basis is zero.
    """
    project_config = context.config
    if project_config.depreciation is None or not construction_stream.entries:
        return CashFlowStream()
    capex_basis = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
    if not capex_basis.entries:
        return CashFlowStream()
    basis = (
        itc_adjusted_basis(capex_basis, project_config.itc_rate)
        if project_config.itc_rate is not None
        else abs(capex_basis.sum())
    )
    if basis == 0.0:
        return CashFlowStream()
    placed = context.require_timeline_date("operations_start")
    ops_start = project_config.timeline.operations_start
    ops_end = project_config.timeline.operations_end
    match project_config.depreciation:
        case MacrsDepreciationConfig() as depreciation_config:
            return scheduling.remap_event_dates(
                context,
                macrs_schedule(
                    cost_basis=basis,
                    placed_in_service=placed,
                    property_class=depreciation_config.property_class,
                    convention=depreciation_config.convention,
                    label=depreciation_config.label,
                ),
                frequency="year",
                phase_start=ops_start,
                phase_end=ops_end,
                truncate_after_phase_end=True,
                component_name="depreciation",
            )
        case VdbDepreciationConfig() as depreciation_config:
            return scheduling.remap_event_dates(
                context,
                vdb_schedule(
                    cost_basis=basis,
                    salvage_value=depreciation_config.salvage_value,
                    placed_in_service=placed,
                    life=depreciation_config.life,
                    frequency=depreciation_config.frequency,
                    factor=depreciation_config.factor,
                    switch_to_straight_line=depreciation_config.switch_to_straight_line,
                    convention=depreciation_config.convention,
                    schedule_dates=depreciation_config.schedule_dates,
                    valuation_rate=depreciation_config.valuation_rate,
                    valuation_date=depreciation_config.valuation_date,
                    terminal_catch_up=depreciation_config.terminal_catch_up,
                    label=depreciation_config.label,
                ),
                frequency=depreciation_config.frequency,
                phase_start=ops_start,
                phase_end=ops_end,
                truncate_after_phase_end=True,
                component_name="depreciation",
            )
        case _:
            raise AssertionError("Unexpected depreciation config")


def compute_project_taxes(
    context: AnalysisContext,
    component_streams: dict[str, CashFlowStream],
) -> tuple[CashFlowStream, CashFlowStream]:
    """Compute taxable income and tax liability across every built component stream."""
    config = context.config
    per_asset_taxable_components: list[CashFlowStream] = []
    per_asset_deductible_components: list[CashFlowStream] = []
    for stream in component_streams.values():
        if not stream.entries:
            continue
        taxable = stream.filter(tax_treatment=TaxTreatment.TAXABLE)
        deductible = stream.filter(tax_treatment=TaxTreatment.DEDUCTIBLE)
        if taxable.entries:
            per_asset_taxable_components.append(taxable)
        if deductible.entries:
            per_asset_deductible_components.append(deductible)

    revenue_for_tax = CashFlowStream.from_streams(*per_asset_taxable_components)
    deductions_for_tax = CashFlowStream.from_streams(*per_asset_deductible_components)
    taxable_income = compute_taxable_income(revenue_for_tax, deductions_for_tax)

    taxes = (
        tax_liability(
            taxable_income,
            tax_rate=config.tax_rate,
            allow_refund=config.tax_allow_refund,
        )
        if config.tax_rate is not None
        else CashFlowStream()
    )
    return taxable_income, taxes
