# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Cross-configuration validation for a project compile."""

from __future__ import annotations

from dcaf.project._builder_config import (
    GenerationRevenueContractConfig,
    GenerationRevenueRemainderConfig,
)
from dcaf.project._compiler.context import AnalysisContext


def validate_generation_revenue_configuration(context: AnalysisContext) -> None:
    """Validate cross-method generation revenue configuration constraints."""
    config = context.config
    generation_revenue_policies = tuple(
        registration
        for registration in config.generation_linked_policies
        if isinstance(
            registration,
            (GenerationRevenueContractConfig, GenerationRevenueRemainderConfig),
        )
    )
    contract_policies = tuple(
        registration
        for registration in generation_revenue_policies
        if isinstance(registration, GenerationRevenueContractConfig)
    )
    remainder_policies = tuple(
        registration
        for registration in generation_revenue_policies
        if isinstance(registration, GenerationRevenueRemainderConfig)
    )

    if config.market is not None and generation_revenue_policies:
        raise ValueError(
            "generation_revenue cannot be combined with "
            "generation_revenue_contract or generation_revenue_remainder"
        )

    if config.generation is None and (config.market is not None or generation_revenue_policies):
        raise ValueError("generation revenue requires generation to be configured")

    seen_names: set[str] = set()
    for registration in config.generation_linked_policies:
        if registration.name in seen_names:
            raise ValueError(
                f"generation-linked policy name {registration.name!r} is already configured"
            )
        seen_names.add(registration.name)

    if len(remainder_policies) > 1:
        raise ValueError("only one generation_revenue_remainder may be configured")
    if contract_policies and not remainder_policies:
        raise ValueError("generation_revenue_contract requires generation_revenue_remainder")
    if remainder_policies and not contract_policies:
        raise ValueError(
            "generation_revenue_remainder requires at least one generation_revenue_contract"
        )


def validate_component_keys(context: AnalysisContext) -> None:
    """Reject configured features that would produce the same component key.

    Every build function registers its output into the compile-time
    accumulator under a string key (e.g. ``"construction"``,
    ``"fixed_opex:default"``), and `ProjectAnalysis.cashflow_components`
    exposes that dict directly. If two configured features computed the same
    key, one would silently overwrite the other when the accumulator inserts
    it. This function walks the configuration *before* anything is built and
    predicts every key each configured feature *would* produce, so a
    collision is reported with both offending feature names — as a
    `ValueError` naming the configuration mistake — instead of surfacing
    later as a missing or overwritten cashflow component.

    ``reserve(key, owner)`` records who claims a key and raises if it was
    already claimed by someone else; ``ledger`` is the resulting
    key -> owner-description map, discarded once validation finishes.
    """
    config = context.config
    ledger: dict[str, str] = {}

    def reserve(key: str, owner: str) -> None:
        if existing_owner := ledger.get(key):
            raise ValueError(
                f"cashflow component key {key!r} is produced by both {existing_owner} and {owner}"
            )
        ledger[key] = owner

    # Single-instance features: each is present at most once, so it always
    # reserves one fixed key if configured at all.
    if config.construction is not None:
        reserve("construction", "construction")
    if config.market is not None:
        reserve("revenue", "generation_revenue")
    if config.itc_rate is not None:
        reserve("itc", "investment_tax_credit")
    if config.ptc is not None:
        reserve("ptc", "production_tax_credit")
    if config.depreciation is not None:
        reserve("depreciation", "depreciation")
    if config.construction_debt is not None or config.debt_schedule is not None:
        reserve("debt_service", "debt")
    if config.tax_rate is not None:
        reserve("project:tax_liability", "tax")

    # Multi-instance features: configured as {name: config} mappings, keyed
    # by the domain module's own naming convention (bare prefix for the
    # "default" entry, "prefix:name" otherwise — mirrors
    # ComponentAccumulator.add_named, which every domain module's
    # build_*_components() calls with the same prefix/name pair).
    for name in config.fixed_opex_items:
        key = "fixed_opex" if name == "default" else f"fixed_opex:{name}"
        reserve(key, f"fixed_opex {name!r}")
    for name in config.variable_cost_items:
        key = "variable_cost" if name == "default" else f"variable_cost:{name}"
        reserve(key, f"variable_cost {name!r}")
    for name in config.construction_outages:
        key = "construction_outage" if name == "default" else f"construction_outage:{name}"
        reserve(key, f"construction_outage {name!r}")
    for registration in config.generation_linked_policies:
        reserve(registration.name, f"generation-linked policy {registration.name!r}")
    for name in config.custom_cashflows:
        reserve(name, f"custom cashflow stream {name!r}")
