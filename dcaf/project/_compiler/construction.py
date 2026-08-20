# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Construction spend, construction-debt-financing basis, and construction-outage build functions."""

from __future__ import annotations

from dcaf.finance.construction import (
    ConstructionCashFlows,
    ConstructionFinancing,
    ConstructionSpendBuilder,
)
from dcaf.finance.outage import construction_outage as construction_outage_helper
from dcaf.project._builder_config import (
    ConstructionFinancingConfig,
    ConstructionOutageConfig,
)
from dcaf.project._compiler import revenue
from dcaf.project._compiler.context import AnalysisContext, ComponentAccumulator
from dcaf.project.contracts import GenerationPrice
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.streams.generation import GenerationStream


def build_construction(context: AnalysisContext) -> ConstructionCashFlows:
    """Build aggregate construction flows and their debt-funding bases.

    A stream override populates only the aggregate because it cannot be
    combined with construction debt. When no spend profile is given, the
    overnight cost is a single pro-rata flow on the COD date. Otherwise the
    construction builder separates scheduled spend from fully financed
    capitalized interest while retaining paid interest only in the aggregate.
    """
    config = context.config
    construction = config.construction
    if construction is None:
        return ConstructionCashFlows(
            total=CashFlowStream(),
            pro_rata_debt_basis=CashFlowStream(),
            full_debt_basis=CashFlowStream(),
        )
    if isinstance(construction, CashFlowStream):
        if config.construction_debt is not None:
            raise ValueError(
                "construction stream overrides cannot be combined with construction debt"
            )
        return ConstructionCashFlows(
            total=construction,
            pro_rata_debt_basis=CashFlowStream(),
            full_debt_basis=CashFlowStream(),
        )

    # Resolve COD date: explicit > operations_start
    cod = (
        construction.cod_date
        if construction.cod_date is not None
        else context.require_timeline_date("operations_start")
    )

    # Overnight-only path: no spend profile, book as single cash flow at COD
    if construction.spend_profile is None:
        construction_spend = CashFlowStream(
            [
                CashFlow(
                    amount=-abs(construction.overnight_cost),
                    date=cod,
                    label="Construction",
                    is_cash=True,
                    pro_forma_category=ProFormaCategory.CAPITAL_COST,
                )
            ]
        )
        return ConstructionCashFlows(
            total=construction_spend,
            pro_rata_debt_basis=construction_spend,
            full_debt_basis=CashFlowStream(),
        )

    # Spend-profile path: distribute cost over construction period
    start = (
        construction.construction_start
        if construction.construction_start is not None
        else context.require_timeline_date("construction_start")
    )
    end = construction.construction_end if construction.construction_end is not None else cod

    # Convert the project configuration to the construction builder's
    # financing configuration.
    financing = construction_financing(config.construction_debt)

    escalation = context.effective_escalation(construction.escalation)
    builder = ConstructionSpendBuilder(
        total_cost=construction.overnight_cost,
        start_date=start,
        end_date=end,
        period=construction.period,
        profile=construction.spend_profile,
        timing=construction.timing or config.timeline.timing,
        financing=financing,
        escalation=escalation.escalation,
        escalation_period=escalation.escalation_period,
        amount_reference_date=escalation.amount_reference_date,
        day_count_convention=config.day_count_convention,
    )
    if escalation.policy is not None:
        builder = builder.escalation_policy(escalation.policy)
    return builder.build_components()


def construction_financing(
    debt_config: ConstructionFinancingConfig | None,
) -> ConstructionFinancing:
    """Convert a construction debt config to a ConstructionFinancing for the spend schedule."""
    if debt_config is None:
        return ConstructionFinancing()
    return ConstructionFinancing(
        debt_fraction=debt_config.debt_fraction,
        interest_rate=debt_config.construction_interest_rate,
        interest_treatment=debt_config.interest_treatment,
        servicing_period=debt_config.servicing_period,
    )


def build_construction_outage(
    context: AnalysisContext,
    outage: ConstructionOutageConfig,
    generation: GenerationStream,
) -> CashFlowStream:
    """Build operating-cost cashflows for a construction outage on baseline generation."""
    config = context.config
    if outage.sell_price_per_unit is None:
        # TODO: Support schedule and callable prices after defining how an outage's
        # aggregate booking event should be priced.
        market = config.market
        if market is None or (
            isinstance(market.price, GenerationPrice)
            and (market.price.mode != "fixed" or market.price.fixed_price is None)
        ):
            raise ValueError(
                f"construction_outage {outage.name!r} requires sell_price_per_unit "
                "unless generation_revenue is configured with price or a fixed "
                "price_policy; "
                "scheduled and callable generation_revenue prices are not supported "
                "for construction outages"
            )
        price_per_mwh, escalation = revenue.resolve_scalar_market_price(
            context, market, generation.entries
        )
    else:
        price_per_mwh = outage.sell_price_per_unit
        escalation = context.effective_escalation(outage.escalation)

    return construction_outage_helper(
        capacity_mw=outage.capacity_mw,
        capacity_factor=outage.capacity_factor,
        start=outage.start,
        end=outage.end,
        sell_price_per_unit=price_per_mwh,
        capacity_reduction=outage.capacity_reduction,
        fixed_cost=outage.fixed_cost,
        cost_per_day=outage.cost_per_day,
        frequency=config.timeline.frequency,
        timing=outage.timing or config.timeline.timing,
        lost_revenue_label=outage.lost_revenue_label,
        fixed_cost_label=outage.fixed_cost_label,
        daily_cost_label=outage.daily_cost_label,
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        tax_treatment=TaxTreatment.DEDUCTIBLE,
        escalation=escalation.escalation,
        escalation_period=escalation.escalation_period,
        amount_reference_date=escalation.amount_reference_date,
        escalation_policy=escalation.policy,
        day_count_convention=config.day_count_convention,
    )


def build_construction_outage_components(
    context: AnalysisContext,
    generation: GenerationStream,
    accumulator: ComponentAccumulator,
) -> None:
    """Build every configured construction outage and register it under its named key."""
    for outage_name, outage_config in context.config.construction_outages.items():
        accumulator.add_named(
            "construction_outage",
            outage_name,
            build_construction_outage(context, outage_config, generation),
        )
