# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Revenue and generation-linked pricing build functions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from dcaf.finance.escalation import ConstantRateEscalation, EscalationPolicy
from dcaf.project._builder_config import (
    CustomGenerationLinkedPolicyConfig,
    GenerationRevenueContractConfig,
    GenerationRevenueRemainderConfig,
)
from dcaf.project._compiler.context import AnalysisContext
from dcaf.project._contract_settlements import settle_generation_contracts, settlement_event
from dcaf.project.contracts import GenerationPrice
from dcaf.shared.types import ProFormaCategory, TaxTreatment, normalize_cashflow_classification
from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.streams.generation import (
    Generation,
    GenerationStream,
    _GenerationSettlement,
    _generation_settlements,
)


def build_revenue(context: AnalysisContext, generation: GenerationStream) -> CashFlowStream:
    """Build the revenue stream from generation and market config.

    Returns an empty stream when generation is empty or no market price is set.
    """
    market = context.config.market
    if market is None:
        return CashFlowStream()
    settlements = _project_generation_settlements(context, generation.entries)
    price_escalation = _resolve_price_escalation(context, settlements, market.price)
    return _revenue_cashflows_from_generation(
        name="revenue",
        settlements=settlements,
        price=market.price,
        price_escalation=price_escalation,
        label=market.label,
        pro_forma_category=market.pro_forma_category,
        tax_treatment=market.tax_treatment,
    )


def build_revenue_basis(context: AnalysisContext, generation: GenerationStream) -> CashFlowStream:
    """Build the unit-price basis for whole-project levelized cost."""
    config = context.config
    if not generation.entries or config.market is None:
        return CashFlowStream()
    return generation.to_revenue(
        price_per_mwh=1.0,
        label="Revenue",
        frequency=config.timeline.frequency,
        timing=config.timeline.timing,
        day_count_convention=config.day_count_convention,
    )


def build_generation_linked_policy_streams(
    context: AnalysisContext,
    generation: GenerationStream,
) -> tuple[tuple[str, CashFlowStream], ...]:
    """Build named streams for configured generation-linked policies."""
    config = context.config
    if not config.generation_linked_policies:
        return ()
    if not generation.entries:
        return ()

    settlements_by_contract, remainder_settlements = settle_generation_contracts(
        generation,
        {
            registration.name: registration.contract
            for registration in config.generation_linked_policies
            if isinstance(registration, GenerationRevenueContractConfig)
        },
        frequency=config.timeline.frequency,
        timing=config.timeline.timing,
        day_count_convention=config.day_count_convention,
    )

    streams: list[tuple[str, CashFlowStream]] = []
    for registration in config.generation_linked_policies:
        if isinstance(registration, GenerationRevenueContractConfig):
            price_escalation = _resolve_price_escalation(
                context,
                list(settlements_by_contract[registration.name]),
                registration.contract.price,
            )
            stream = _revenue_cashflows_from_generation(
                name=registration.name,
                settlements=settlements_by_contract[registration.name],
                price=registration.contract.price,
                price_escalation=price_escalation,
                label=registration.contract.label,
                pro_forma_category=registration.contract.pro_forma_category,
                tax_treatment=registration.contract.tax_treatment,
            )
        elif isinstance(registration, GenerationRevenueRemainderConfig):
            price_escalation = _resolve_price_escalation(
                context,
                list(remainder_settlements),
                registration.price,
            )
            stream = _revenue_cashflows_from_generation(
                name=registration.name,
                settlements=remainder_settlements,
                price=registration.price,
                price_escalation=price_escalation,
                label=registration.label,
                pro_forma_category=registration.pro_forma_category,
                tax_treatment=registration.tax_treatment,
            )
        elif isinstance(registration, CustomGenerationLinkedPolicyConfig):
            stream = registration.policy.cashflows(generation)
        else:
            continue
        streams.append((registration.name, stream))
    return tuple(streams)


def _generation_revenue_price_escalation(
    context: AnalysisContext,
    settlements: list[_GenerationSettlement],
) -> EscalationPolicy | None:
    """Resolve the shared scalar-price escalation for revenue and outage fallback."""
    config = context.config
    escalation = config.default_escalation
    if escalation.policy is not None:
        return escalation.policy
    reference_date = escalation.amount_reference_date
    if reference_date is None:
        if not settlements:
            return None
        reference_date = min(entry.date for entry in settlements)
    return ConstantRateEscalation(
        reference_date=reference_date,
        rate=escalation.escalation,
        period=escalation.escalation_period,
        day_count_convention=config.day_count_convention,
    )


def _resolve_price_escalation(
    context: AnalysisContext,
    settlements: list[_GenerationSettlement],
    price: float | GenerationPrice,
) -> EscalationPolicy | None:
    """Resolve the escalation policy for a scalar or generation price.

    Scalar prices inherit the project default escalation. GenerationPrice
    instances inherit the project default only when ``apply_escalation`` is
    True.
    """
    if isinstance(price, float):
        return _generation_revenue_price_escalation(context, settlements)
    if isinstance(price, GenerationPrice) and price.apply_escalation:
        return _generation_revenue_price_escalation(context, settlements)
    return None


def _project_generation_settlements(
    context: AnalysisContext,
    entries: list[Generation],
    *,
    clip_start: date | None = None,
    clip_end: date | None = None,
) -> list[_GenerationSettlement]:
    """Settle generation using the project's financial calendar conventions."""
    config = context.config
    return _generation_settlements(
        entries,
        frequency=config.timeline.frequency,
        timing=config.timeline.timing,
        day_count_convention=config.day_count_convention,
        clip_start=clip_start,
        clip_end=clip_end,
    )


def _validate_price_schedule_alignment(
    *,
    name: str,
    price: GenerationPrice,
    settlement_dates: set[date],
) -> None:
    """Require each scheduled-price date to correspond to a component settlement."""
    if price.mode != "schedule":
        return

    available_dates = _format_generation_dates(settlement_dates)
    for scheduled_date, _price in price.price_schedule:
        if scheduled_date not in settlement_dates:
            raise ValueError(
                f"{name} price schedule contains {scheduled_date.isoformat()}, but the project "
                "component has no settlement on that date. Available component settlement "
                f"dates: {available_dates}. Remove the price entry or update the schedule."
            )


def _revenue_cashflows_from_generation(
    *,
    name: str,
    settlements: Sequence[_GenerationSettlement],
    price: float | GenerationPrice,
    price_escalation: EscalationPolicy | None,
    label: str,
    pro_forma_category: ProFormaCategory | str | None,
    tax_treatment: TaxTreatment | str,
) -> CashFlowStream:
    if isinstance(price, GenerationPrice):
        _validate_price_schedule_alignment(
            name=name,
            price=price,
            settlement_dates={entry.date for entry in settlements},
        )
    entries: list[CashFlow] = []
    category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category,
        tax_treatment,
    )
    for entry in settlements:
        event = settlement_event(name, entry)
        escalation_factor = 1.0 if price_escalation is None else price_escalation.factor(entry.date)
        resolved_price = price.resolve(event) if isinstance(price, GenerationPrice) else price
        entries.append(
            CashFlow(
                amount=event.delivered_mwh * resolved_price * escalation_factor,
                date=entry.date,
                label=label,
                is_cash=True,
                pro_forma_category=category,
                tax_treatment=resolved_tax_treatment,
            )
        )
    return CashFlowStream(entries)


def _format_generation_dates(generation_dates: Iterable[date]) -> str:
    dates = sorted(generation_dates)
    if not dates:
        return "none"
    return ", ".join(event_date.isoformat() for event_date in dates)
