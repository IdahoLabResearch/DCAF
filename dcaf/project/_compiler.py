# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Private compiler for turning project builder configuration into analysis results."""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace as dc_replace
from datetime import date
from math import isclose
from typing import Literal

from dateutil.relativedelta import relativedelta

from dcaf.finance.amortization import AmortizationSchedule, _calendarize_amortization_schedule
from dcaf.finance.construction import (
    ConstructionCashFlows,
    ConstructionFinancing,
    ConstructionSpendBuilder,
)
from dcaf.finance.escalation import ConstantRateEscalation, EscalationPolicy
from dcaf.finance.outage import construction_outage as construction_outage_helper
from dcaf.project._builder_config import (
    CapacityGenerationConfig,
    ConstructionFinancingConfig,
    ConstructionOutageConfig,
    ConstructionScheduleConfig,
    CustomGenerationLinkedPolicyConfig,
    EscalationSettings,
    FixedOpexConfig,
    GenerationRevenueContractConfig,
    GenerationRevenueRemainderConfig,
    MacrsDepreciationConfig,
    ProjectConfig,
    VariableCostConfig,
    VdbDepreciationConfig,
    constant_annual_escalation_rate,
    effective_escalation,
)
from dcaf.project.analysis import ProjectAnalysis
from dcaf.project._contract_settlements import settle_generation_contracts, settlement_event
from dcaf.project.contracts import GenerationPrice
from dcaf.project.timeline import ProjectTimeline
from dcaf.shared.time import (
    ScheduleTruncationWarning,
    elapsed_hours,
    elapsed_periods,
    event_date,
    period_windows,
    time_delta_per_period,
)
from dcaf.shared.types import (
    DayCountConvention,
    Period,
    ProFormaCategory,
    TaxTreatment,
    TimingConvention,
    normalize_cashflow_classification,
)
from dcaf.streams.cashflows import CashFlow, CashFlowGroup, CashFlowStream
from dcaf.streams.generation import (
    Generation,
    GenerationStream,
    _GenerationSettlement,
    _generation_settlements,
)
from dcaf.tax.depreciation import macrs_schedule, vdb_schedule
from dcaf.tax.incentives import itc, itc_adjusted_basis, ptc
from dcaf.tax.liability import compute_taxable_income, tax_liability


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Resolved configuration and helpers for one project analysis compile."""

    source_config: ProjectConfig
    timeline: ProjectTimeline = field(init=False)
    config: ProjectConfig = field(init=False)

    def __post_init__(self) -> None:
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


@dataclass(frozen=True)
class ScheduledPeriod:
    """One modeled operating period with an optional partial-period fraction.

    ``event_date`` is the booking date for the period, computed from the timing
    convention and phase boundaries. It defaults to ``start`` when not provided.
    """

    start: date
    end: date
    event_date: date
    fraction: float = 1.0


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


@dataclass(frozen=True, slots=True)
class ProjectCompiler:
    """Compile an immutable project configuration into a project analysis."""

    context: AnalysisContext

    @classmethod
    def from_config(cls, config: ProjectConfig) -> ProjectCompiler:
        """Build a compiler with a resolved analysis context."""
        return cls(AnalysisContext(config))

    @property
    def config(self) -> ProjectConfig:
        """Return the resolved config used by moved compile helpers."""
        return self.context.config

    def compile(self) -> ProjectAnalysis:
        """Compile all project configuration into a :class:`ProjectAnalysis`."""
        component_streams = ComponentAccumulator()
        levelized_revenue_basis = ComponentAccumulator()

        self.validate_generation_revenue_configuration()
        self._validate_component_keys()
        generation = self.build_generation()

        construction = self.build_construction()
        construction_stream = construction.total
        component_streams.add("construction", construction_stream)

        revenue_stream = self.build_revenue(generation)
        component_streams.add("revenue", revenue_stream)
        revenue_basis_stream = self.build_revenue_basis(generation)
        levelized_revenue_basis.add("revenue", revenue_basis_stream)

        for name, stream in self.build_generation_linked_policy_streams(generation):
            component_streams.add(name, stream)

        for cost_name, cost_config in self.config.fixed_opex_items.items():
            component_streams.add_named(
                "fixed_opex",
                cost_name,
                self.build_fixed_opex(cost_config),
            )

        for vc_name, vc_config in self.config.variable_cost_items.items():
            component_streams.add_named(
                "variable_cost",
                vc_name,
                self.build_variable_cost(vc_config, generation),
            )

        for outage_name, outage_config in self.config.construction_outages.items():
            component_streams.add_named(
                "construction_outage",
                outage_name,
                self.build_construction_outage(outage_config, generation),
            )

        itc_stream = self.build_itc(construction_stream)
        component_streams.add("itc", itc_stream)

        ptc_stream = self.build_ptc(generation)
        component_streams.add("ptc", ptc_stream)

        depreciation_stream = self.build_depreciation(construction_stream)
        component_streams.add("depreciation", depreciation_stream)

        debt_proceeds_stream = self.build_debt_proceeds(construction)
        component_streams.add("debt_proceeds", debt_proceeds_stream)

        debt_stream = self.build_debt(debt_proceeds_stream)
        component_streams.add("debt_service", debt_stream)

        for name, stream in self.config.custom_cashflows.items():
            component_streams.add_custom(name, stream)

        per_asset_taxable_components: list[CashFlowStream] = []
        per_asset_deductible_components: list[CashFlowStream] = []
        for stream in component_streams.streams.values():
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
                tax_rate=self.config.tax_rate,
                allow_refund=self.config.tax_allow_refund,
            )
            if self.config.tax_rate is not None
            else CashFlowStream()
        )
        component_streams.add("project:tax_liability", taxes)

        return ProjectAnalysis(
            timeline=self.context.timeline,
            valuation=self.config.valuation,
            generation=generation,
            cashflow_components=CashFlowGroup(component_streams.streams),
            taxable_income=taxable_income,
            taxes=taxes,
            tax_rate=self.config.tax_rate,
            levelized_revenue_basis=CashFlowGroup(levelized_revenue_basis.streams),
            levelized_cost_escalation_rate=self.infer_levelized_cost_escalation_rate(),
            tax_allow_refund=self.config.tax_allow_refund,
        )

    def validate_generation_revenue_configuration(self) -> None:
        """Validate cross-method generation revenue configuration constraints."""
        generation_revenue_policies = tuple(
            registration
            for registration in self.config.generation_linked_policies
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

        if self.config.market is not None and generation_revenue_policies:
            raise ValueError(
                "generation_revenue cannot be combined with "
                "generation_revenue_contract or generation_revenue_remainder"
            )

        if self.config.generation is None and (
            self.config.market is not None or generation_revenue_policies
        ):
            raise ValueError("generation revenue requires generation to be configured")

        seen_names: set[str] = set()
        for registration in self.config.generation_linked_policies:
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

    def _validate_component_keys(self) -> None:
        """Reject configured features that would produce the same component key."""
        ledger: dict[str, str] = {}

        def reserve(key: str, owner: str) -> None:
            if existing_owner := ledger.get(key):
                raise ValueError(
                    f"cashflow component key {key!r} is produced by both {existing_owner} and {owner}"
                )
            ledger[key] = owner

        if self.config.construction is not None:
            reserve("construction", "construction")
        if self.config.market is not None:
            reserve("revenue", "generation_revenue")
        if self.config.itc_rate is not None:
            reserve("itc", "investment_tax_credit")
        if self.config.ptc is not None:
            reserve("ptc", "production_tax_credit")
        if self.config.depreciation is not None:
            reserve("depreciation", "depreciation")
        if self.config.construction_debt is not None or self.config.debt_schedule is not None:
            reserve("debt_service", "debt")
        if self.config.tax_rate is not None:
            reserve("project:tax_liability", "tax")

        for name in self.config.fixed_opex_items:
            key = "fixed_opex" if name == "default" else f"fixed_opex:{name}"
            reserve(key, f"fixed_opex {name!r}")
        for name in self.config.variable_cost_items:
            key = "variable_cost" if name == "default" else f"variable_cost:{name}"
            reserve(key, f"variable_cost {name!r}")
        for name in self.config.construction_outages:
            key = "construction_outage" if name == "default" else f"construction_outage:{name}"
            reserve(key, f"construction_outage {name!r}")
        for registration in self.config.generation_linked_policies:
            reserve(registration.name, f"generation-linked policy {registration.name!r}")
        for name in self.config.custom_cashflows:
            reserve(name, f"custom cashflow stream {name!r}")

    def build_generation(self) -> GenerationStream:
        """Build the generation stream from project configuration.

        Returns an empty stream when generation is unconfigured.
        """
        generation = self.config.generation
        if generation is None:
            if self.config.generation_outages:
                raise ValueError("generation_outage requires generation to be configured")
            return GenerationStream()
        if isinstance(generation, GenerationStream):
            base_generation = generation
        else:
            start = (
                generation.start
                if generation.start is not None
                else self.require_timeline_date("operations_start")
            )
            ops_start = self.config.timeline.operations_start
            ops_end = self.config.timeline.operations_end
            schedule = self.operating_schedule(
                "generation",
                start=start,
                periods=generation.periods,
                frequency=self.config.timeline.frequency,
                phase_start=ops_start,
                phase_end=ops_end,
            )
            if schedule:
                period_start = schedule[0].start
                period_end = schedule[-1].end
                hours = elapsed_hours(
                    period_start,
                    period_end,
                    self.config.day_count_convention,
                )
                base_generation = GenerationStream(
                    [
                        Generation(
                            amount_mwh=(
                                generation.capacity_mw * generation.capacity_factor * hours
                            ),
                            label=generation.label,
                            period_start=period_start,
                            period_end=period_end,
                        )
                    ]
                )
            else:
                base_generation = GenerationStream()

        outage_generation = self.build_generation_outages()
        if not outage_generation.entries:
            return base_generation
        return GenerationStream.from_streams(base_generation, outage_generation).sort()

    def build_generation_outages(self) -> GenerationStream:
        """Build negative generation entries for configured modeled outages."""
        if not self.config.generation_outages:
            return GenerationStream()

        generation = self.config.generation
        capacity_defaults: CapacityGenerationConfig | None = (
            generation if isinstance(generation, CapacityGenerationConfig) else None
        )
        ops_start = self.config.timeline.operations_start
        ops_end = self.config.timeline.operations_end

        outage_streams: list[GenerationStream] = []
        for outage in self.config.generation_outages:
            if ops_start is not None and outage.start < ops_start:
                raise ValueError(
                    f"generation_outage {outage.name!r} starts before operations_start"
                )
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
                    day_count_convention=self.config.day_count_convention,
                )
            )
        return GenerationStream.from_streams(*outage_streams)

    def build_construction_outage(
        self,
        outage: ConstructionOutageConfig,
        generation: GenerationStream,
    ) -> CashFlowStream:
        """Build operating-cost cashflows for a construction outage on baseline generation."""
        if outage.sell_price_per_unit is None:
            # TODO: Support schedule and callable prices after defining how an outage's
            # aggregate booking event should be priced.
            market = self.config.market
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
            settlements = self._project_generation_settlements(generation.entries)
            if isinstance(market.price, GenerationPrice):
                assert market.price.fixed_price is not None
                price_per_mwh = market.price.fixed_price
                policy = self._resolve_price_escalation(settlements, market.price)
                escalation = (
                    EscalationSettings(policy=policy, explicit=True)
                    if policy is not None
                    else EscalationSettings(explicit=True)
                )
            else:
                price_per_mwh = market.price
                escalation = EscalationSettings(
                    policy=self._generation_revenue_price_escalation(settlements),
                    explicit=True,
                )
        else:
            price_per_mwh = outage.sell_price_per_unit
            escalation = self.context.effective_escalation(outage.escalation)

        return construction_outage_helper(
            capacity_mw=outage.capacity_mw,
            capacity_factor=outage.capacity_factor,
            start=outage.start,
            end=outage.end,
            sell_price_per_unit=price_per_mwh,
            capacity_reduction=outage.capacity_reduction,
            fixed_cost=outage.fixed_cost,
            cost_per_day=outage.cost_per_day,
            frequency=self.config.timeline.frequency,
            timing=outage.timing or self.config.timeline.timing,
            lost_revenue_label=outage.lost_revenue_label,
            fixed_cost_label=outage.fixed_cost_label,
            daily_cost_label=outage.daily_cost_label,
            pro_forma_category=ProFormaCategory.OPERATING_COST,
            tax_treatment=TaxTreatment.DEDUCTIBLE,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
            escalation_policy=escalation.policy,
            day_count_convention=self.config.day_count_convention,
        )

    def build_revenue(
        self,
        generation: GenerationStream,
    ) -> CashFlowStream:
        """Build the revenue stream from generation and market config.

        Returns an empty stream when generation is empty or no market price is set.
        """
        market = self.config.market
        if market is None:
            return CashFlowStream()
        settlements = self._project_generation_settlements(generation.entries)
        price_escalation = self._resolve_price_escalation(settlements, market.price)
        return self._revenue_cashflows_from_generation(
            name="revenue",
            settlements=settlements,
            price=market.price,
            price_escalation=price_escalation,
            label=market.label,
            pro_forma_category=market.pro_forma_category,
            tax_treatment=market.tax_treatment,
        )

    def _generation_revenue_price_escalation(
        self,
        settlements: list[_GenerationSettlement],
    ) -> EscalationPolicy | None:
        """Resolve the shared scalar-price escalation for revenue and outage fallback."""
        escalation = self.config.default_escalation
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
            day_count_convention=self.config.day_count_convention,
        )

    def _resolve_price_escalation(
        self,
        settlements: list[_GenerationSettlement],
        price: float | GenerationPrice,
    ) -> EscalationPolicy | None:
        """Resolve the escalation policy for a scalar or generation price.

        Scalar prices inherit the project default escalation. GenerationPrice
        instances inherit the project default only when ``apply_escalation`` is
        True.
        """
        if isinstance(price, float):
            return self._generation_revenue_price_escalation(settlements)
        if isinstance(price, GenerationPrice) and price.apply_escalation:
            return self._generation_revenue_price_escalation(settlements)
        return None

    def _project_generation_settlements(
        self,
        entries: list[Generation],
        *,
        clip_start: date | None = None,
        clip_end: date | None = None,
    ) -> list[_GenerationSettlement]:
        """Settle generation using the project's financial calendar conventions."""
        return _generation_settlements(
            entries,
            frequency=self.config.timeline.frequency,
            timing=self.config.timeline.timing,
            day_count_convention=self.config.day_count_convention,
            clip_start=clip_start,
            clip_end=clip_end,
        )

    def build_revenue_basis(self, generation: GenerationStream) -> CashFlowStream:
        """Build the unit-price basis for whole-project levelized cost."""
        if not generation.entries or self.config.market is None:
            return CashFlowStream()
        return generation.to_revenue(
            price_per_mwh=1.0,
            label="Revenue",
            frequency=self.config.timeline.frequency,
            timing=self.config.timeline.timing,
            day_count_convention=self.config.day_count_convention,
        )

    def build_generation_linked_policy_streams(
        self,
        generation: GenerationStream,
    ) -> tuple[tuple[str, CashFlowStream], ...]:
        """Build named streams for configured generation-linked policies."""
        if not self.config.generation_linked_policies:
            return ()
        if not generation.entries:
            return ()

        settlements_by_contract, remainder_settlements = settle_generation_contracts(
            generation,
            {
                registration.name: registration.contract
                for registration in self.config.generation_linked_policies
                if isinstance(registration, GenerationRevenueContractConfig)
            },
            frequency=self.config.timeline.frequency,
            timing=self.config.timeline.timing,
            day_count_convention=self.config.day_count_convention,
        )

        streams: list[tuple[str, CashFlowStream]] = []
        for registration in self.config.generation_linked_policies:
            if isinstance(registration, GenerationRevenueContractConfig):
                price_escalation = self._resolve_price_escalation(
                    list(settlements_by_contract[registration.name]),
                    registration.contract.price,
                )
                stream = self._revenue_cashflows_from_generation(
                    name=registration.name,
                    settlements=settlements_by_contract[registration.name],
                    price=registration.contract.price,
                    price_escalation=price_escalation,
                    label=registration.contract.label,
                    pro_forma_category=registration.contract.pro_forma_category,
                    tax_treatment=registration.contract.tax_treatment,
                )
            elif isinstance(registration, GenerationRevenueRemainderConfig):
                price_escalation = self._resolve_price_escalation(
                    list(remainder_settlements),
                    registration.price,
                )
                stream = self._revenue_cashflows_from_generation(
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

    @staticmethod
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
        self,
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
            self._validate_price_schedule_alignment(
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
            escalation_factor = (
                1.0 if price_escalation is None else price_escalation.factor(entry.date)
            )
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

    def build_fixed_opex(self, fixed: FixedOpexConfig) -> CashFlowStream:
        """Build a fixed OPEX cash-flow stream from *fixed* configuration.

        Each period's amount is scaled by the escalation factor and the
        partial-period fraction.
        """
        frequency = (
            fixed.frequency if fixed.frequency is not None else self.config.timeline.frequency
        )
        start = (
            fixed.start
            if fixed.start is not None
            else self.require_timeline_date("operations_start")
        )
        timing = fixed.timing or self.config.timeline.timing
        ops_start = self.config.timeline.operations_start
        ops_end = self.config.timeline.operations_end
        schedule = self.operating_schedule(
            "fixed_opex",
            start=start,
            periods=fixed.periods,
            frequency=frequency,
            timing=timing,
            phase_start=ops_start,
            phase_end=ops_end,
        )
        escalation = self.context.effective_escalation(fixed.escalation)
        escalation_policy = recurring_policy(
            start,
            escalation,
            self.config.day_count_convention,
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

    def build_variable_cost(
        self,
        variable: VariableCostConfig,
        generation: GenerationStream,
    ) -> CashFlowStream:
        """Build a variable cost stream by applying *variable* rate to generation.

        Returns an empty stream when generation is unavailable.
        """
        if not generation.entries:
            return CashFlowStream()
        escalation = self.context.effective_escalation(variable.escalation)
        if escalation.policy is not None:
            return generation.to_cost(
                rate_per_mwh=variable.rate_per_unit,
                label=variable.label,
                escalation_policy=escalation.policy,
                frequency=self.config.timeline.frequency,
                timing=self.config.timeline.timing,
                day_count_convention=self.config.day_count_convention,
            )
        return generation.to_cost(
            rate_per_mwh=variable.rate_per_unit,
            label=variable.label,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
            day_count_convention=self.config.day_count_convention,
            frequency=self.config.timeline.frequency,
            timing=self.config.timeline.timing,
        )

    def build_construction(self) -> ConstructionCashFlows:
        """Build aggregate construction flows and their debt-funding bases.

        A stream override populates only the aggregate because it cannot be
        combined with construction debt. When no spend profile is given, the
        overnight cost is a single pro-rata flow on the COD date. Otherwise the
        construction builder separates scheduled spend from fully financed
        capitalized interest while retaining paid interest only in the aggregate.
        """
        construction = self.config.construction
        if construction is None:
            return ConstructionCashFlows(
                total=CashFlowStream(),
                pro_rata_debt_basis=CashFlowStream(),
                full_debt_basis=CashFlowStream(),
            )
        if isinstance(construction, CashFlowStream):
            if self.config.construction_debt is not None:
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
            else self.require_timeline_date("operations_start")
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
            else self.require_timeline_date("construction_start")
        )
        end = construction.construction_end if construction.construction_end is not None else cod

        # Convert the project configuration to the construction builder's
        # financing configuration.
        financing = self.construction_financing(self.config.construction_debt)

        escalation = self.context.effective_escalation(construction.escalation)
        builder = ConstructionSpendBuilder(
            total_cost=construction.overnight_cost,
            start_date=start,
            end_date=end,
            period=construction.period,
            profile=construction.spend_profile,
            timing=construction.timing or self.config.timeline.timing,
            financing=financing,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
            day_count_convention=self.config.day_count_convention,
        )
        if escalation.policy is not None:
            builder = builder.escalation_policy(escalation.policy)
        return builder.build_components()

    @staticmethod
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

    def build_itc(
        self,
        construction_stream: CashFlowStream,
    ) -> CashFlowStream:
        """Build an ITC cash-flow stream from capital-cost construction flows.

        Returns an empty stream when no ITC rate is configured or construction
        has no capital-cost entries.
        """
        if self.config.itc_rate is None or not construction_stream.entries:
            return CashFlowStream()
        capex_basis = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
        if not capex_basis.entries:
            return CashFlowStream()
        return itc(
            capex_stream=capex_basis,
            rate=self.config.itc_rate,
            placed_in_service=self.require_timeline_date("operations_start"),
        )

    def build_ptc(
        self,
        generation: GenerationStream,
    ) -> CashFlowStream:
        """Build a PTC cash-flow stream from generation and PTC configuration.

        Returns an empty stream when no PTC is configured or generation is empty.
        """
        if self.config.ptc is None or not generation.entries:
            return CashFlowStream()
        ptc_config = self.config.ptc
        escalation = self.context.effective_escalation(ptc_config.escalation)
        if escalation.policy is not None:
            return ptc(
                generation_stream=generation,
                rate_per_mwh=ptc_config.rate_per_unit,
                years=ptc_config.years,
                label=ptc_config.label,
                escalation_policy=escalation.policy,
                day_count_convention=self.config.day_count_convention,
                frequency=self.config.timeline.frequency,
                timing=self.config.timeline.timing,
            )
        return ptc(
            generation_stream=generation,
            rate_per_mwh=ptc_config.rate_per_unit,
            years=ptc_config.years,
            label=ptc_config.label,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
            day_count_convention=self.config.day_count_convention,
            frequency=self.config.timeline.frequency,
            timing=self.config.timeline.timing,
        )

    def remap_event_dates(
        self,
        stream: CashFlowStream,
        frequency: Period,
        phase_start: date | None,
        phase_end: date | None,
        *,
        truncate_after_phase_end: bool = False,
        component_name: str = "cashflow",
    ) -> CashFlowStream:
        """Remap cashflow dates according to the project timing convention.

        Applies the timeline's timing convention to each cashflow in *stream*,
        replacing each date with the computed event date. When
        ``truncate_after_phase_end`` is true, events are first remapped without
        capping to ``phase_end`` and then entries on or after the exclusive phase
        end are dropped with a warning.
        """
        timing = self.config.timeline.timing
        phase_end_inclusive = (
            None
            if truncate_after_phase_end
            else phase_end - relativedelta(days=1)
            if phase_end is not None
            else None
        )
        remapped = stream.apply(
            lambda cf: dc_replace(
                cf,
                date=event_date(cf.date, frequency, timing, phase_start, phase_end_inclusive),
            )
        )
        if truncate_after_phase_end:
            return self.truncate_cashflow_schedule(
                remapped,
                boundary=phase_end,
                component_name=component_name,
            )
        return remapped

    def truncate_cashflow_schedule(
        self,
        stream: CashFlowStream,
        *,
        boundary: date | None,
        component_name: str,
    ) -> CashFlowStream:
        """Drop scheduled cashflows on or after an exclusive analysis boundary."""
        if boundary is None:
            return stream
        dropped_dates = [cf.date for cf in stream.entries if cf.date >= boundary]
        if not dropped_dates:
            return stream
        self.warn_schedule_truncated(
            component_name=component_name,
            dropped_count=len(dropped_dates),
            first_dropped=min(dropped_dates),
            last_dropped=max(dropped_dates),
            boundary=boundary,
        )
        return stream.filter_apply(lambda cf: cf if cf.date < boundary else None)

    @staticmethod
    def warn_schedule_truncated(
        *,
        component_name: str,
        dropped_count: int,
        first_dropped: date,
        last_dropped: date,
        boundary: date,
    ) -> None:
        """Warn that a configured schedule was truncated by ``operations_end``."""
        entry_word = "entry" if dropped_count == 1 else "entries"
        warnings.warn(
            (
                f"{component_name} schedule truncated at operations_end "
                f"{boundary.isoformat()}: dropped {dropped_count} cashflow "
                f"{entry_word} dated from {first_dropped.isoformat()} through "
                f"{last_dropped.isoformat()}. Analyses where operations_end "
                "falls before a configured schedule completes may be misspecified."
            ),
            ScheduleTruncationWarning,
            stacklevel=4,
        )

    def build_depreciation(
        self,
        construction_stream: CashFlowStream,
    ) -> CashFlowStream:
        """Build a depreciation stream from construction capital costs and depreciation config.

        Applies ITC basis adjustment when an ITC rate is configured. Returns an
        empty stream when depreciation is unconfigured or the cost basis is zero.
        """
        if self.config.depreciation is None or not construction_stream.entries:
            return CashFlowStream()
        capex_basis = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
        if not capex_basis.entries:
            return CashFlowStream()
        basis = (
            itc_adjusted_basis(capex_basis, self.config.itc_rate)
            if self.config.itc_rate is not None
            else abs(capex_basis.sum())
        )
        if basis == 0.0:
            return CashFlowStream()
        placed = self.require_timeline_date("operations_start")
        ops_start = self.config.timeline.operations_start
        ops_end = self.config.timeline.operations_end
        match self.config.depreciation:
            case MacrsDepreciationConfig() as config:
                return self.remap_event_dates(
                    macrs_schedule(
                        cost_basis=basis,
                        placed_in_service=placed,
                        property_class=config.property_class,
                        convention=config.convention,
                        label=config.label,
                    ),
                    frequency="year",
                    phase_start=ops_start,
                    phase_end=ops_end,
                    truncate_after_phase_end=True,
                    component_name="depreciation",
                )
            case VdbDepreciationConfig() as config:
                return self.remap_event_dates(
                    vdb_schedule(
                        cost_basis=basis,
                        salvage_value=config.salvage_value,
                        placed_in_service=placed,
                        life=config.life,
                        frequency=config.frequency,
                        factor=config.factor,
                        switch_to_straight_line=config.switch_to_straight_line,
                        convention=config.convention,
                        schedule_dates=config.schedule_dates,
                        valuation_rate=config.valuation_rate,
                        valuation_date=config.valuation_date,
                        terminal_catch_up=config.terminal_catch_up,
                        label=config.label,
                    ),
                    frequency=config.frequency,
                    phase_start=ops_start,
                    phase_end=ops_end,
                    truncate_after_phase_end=True,
                    component_name="depreciation",
                )
            case _:
                raise AssertionError("Unexpected depreciation config")

    def build_debt_proceeds(self, construction: ConstructionCashFlows) -> CashFlowStream:
        """Build construction-debt funding entries at the underlying cost dates.

        Cash construction costs receive cash debt draws for the configured debt
        fraction. Capitalized construction interest receives an equal non-cash
        financing entry because it is added in full to permanent debt principal.
        Explicit debt schedules do not provide draw timing, so no proceeds are
        inferred for that path.
        """
        if self.config.debt_schedule is not None:
            return CashFlowStream()

        debt = self.config.construction_debt
        if debt is None:
            return CashFlowStream()

        cash_debt_proceeds = [
            flow.replace(
                amount=-flow.amount * debt.debt_fraction,
                label="Construction Debt Proceeds",
                pro_forma_category=ProFormaCategory.FINANCING_PROCEEDS,
                tax_treatment=TaxTreatment.NONE,
            )
            for flow in construction.pro_rata_debt_basis.entries
            if not isclose(flow.amount * debt.debt_fraction, 0.0)
        ]
        capitalized_interest_financing = [
            flow.replace(
                amount=-flow.amount,
                label="Capitalized Interest Financing",
                pro_forma_category=ProFormaCategory.FINANCING_PROCEEDS,
                tax_treatment=TaxTreatment.NONE,
            )
            for flow in construction.full_debt_basis.entries
            if not isclose(flow.amount, 0.0)
        ]
        return CashFlowStream([*cash_debt_proceeds, *capitalized_interest_financing]).sort()

    def build_debt(
        self,
        debt_proceeds: CashFlowStream,
    ) -> CashFlowStream:
        """Build the debt service stream from construction debt or schedule config.

        Handles two paths: construction-debt-based amortization (principal
        derived from recorded financing proceeds) and explicit schedule overrides.
        Internally generated payments are allocated across calendar periods;
        explicit schedule dates and amounts are preserved. Returns an empty
        stream when no debt is configured.
        """
        # Explicit schedule override takes precedence
        if self.config.debt_schedule is not None:
            sched = self.config.debt_schedule
            if isinstance(sched, AmortizationSchedule):
                return self.truncate_cashflow_schedule(
                    CashFlowStream.from_streams(
                        sched.interest,
                        sched.principal,
                    ).sort(),
                    boundary=self.config.timeline.operations_end,
                    component_name="debt_service",
                )
            return self.truncate_cashflow_schedule(
                sched,
                boundary=self.config.timeline.operations_end,
                component_name="debt_service",
            )

        # Construction-debt path
        debt = self.config.construction_debt
        if debt is None:
            return CashFlowStream()

        # Derive principal from the recorded construction financing.
        if self.config.construction is None:
            raise ValueError(
                "construction_debt requires a construction schedule to derive "
                "the debt principal — call construction() first"
            )
        principal = debt_proceeds.sum()

        start = (
            debt.amortization_start
            if debt.amortization_start is not None
            else self.require_timeline_date("operations_start")
        )
        schedule = _calendarize_amortization_schedule(
            AmortizationSchedule.build(
                principal=principal,
                annual_rate=debt.amortization_rate,
                term=debt.amortization_term,
                start_date=start,
                frequency=debt.amortization_frequency,
            ),
            frequency=debt.amortization_frequency,
            timing=self.config.timeline.timing,
            day_count_convention=self.config.day_count_convention,
        )
        ops_end = self.config.timeline.operations_end
        return self.truncate_cashflow_schedule(
            CashFlowStream.from_streams(schedule.interest, schedule.principal).sort(),
            boundary=ops_end,
            component_name="debt_service",
        )

    def infer_levelized_cost_escalation_rate(self) -> float | None:
        """Infer a shared annual escalation rate for constant-dollar LCOE when possible."""

        inferred_rates: list[float] = []

        def collect(local: EscalationSettings) -> bool:
            effective = self.context.effective_escalation(local)
            rate = constant_annual_escalation_rate(effective)
            if rate is None:
                return False
            inferred_rates.append(rate)
            return True

        if (
            isinstance(self.config.construction, ConstructionScheduleConfig)
            and self.config.construction.overnight_cost != 0.0
        ):
            if not collect(self.config.construction.escalation):
                return None
        for recurring_cost in self.config.fixed_opex_items.values():
            if recurring_cost.amount != 0.0:
                if not collect(recurring_cost.escalation):
                    return None
        if self.config.ptc is not None and self.config.ptc.rate_per_unit != 0.0:
            if not collect(self.config.ptc.escalation):
                return None

        if not inferred_rates:
            return 0.0

        first_rate = inferred_rates[0]
        if any(
            not isclose(rate, first_rate, rel_tol=0.0, abs_tol=1e-12) for rate in inferred_rates[1:]
        ):
            return None
        return first_rate

    def operating_schedule(
        self,
        section: str,
        *,
        start: date,
        periods: int | float | None,
        frequency: Period,
        timing: TimingConvention = "end",
        phase_start: date | None = None,
        phase_end: date | None = None,
    ) -> tuple[ScheduledPeriod, ...]:
        """Build the sequence of modeled operating periods for a section.

        When *periods* is specified, generates that many period entries and
        allows a final fractional period, truncated to complete days because
        DCAF events are stored as ``datetime.date`` values. Otherwise infers the
        schedule from ``timeline.operations_end``, prorating any trailing
        partial period using :func:`elapsed_periods`.

        *timing*, *phase_start*, and *phase_end* (all exclusive ends) control
        event-date placement. ``phase_end`` is converted to the inclusive
        last-allowable date when forwarded to :func:`event_date`.
        """
        phase_end_inclusive = phase_end - relativedelta(days=1) if phase_end is not None else None

        if periods is not None:
            if periods <= 0:
                raise ValueError(f"{section} periods must be positive")
            windows = period_windows(
                start,
                periods,
                frequency,
                self.config.day_count_convention,
                context=f"{section} periods",
            )
            if phase_end is not None and windows and windows[-1].end > phase_end:
                self.warn_configured_schedule_truncated(
                    section=section,
                    requested_end=windows[-1].end,
                    boundary=phase_end,
                )
            schedule: list[ScheduledPeriod] = []
            for window in windows:
                if phase_end is not None and window.start >= phase_end:
                    break
                effective_window_end = (
                    min(window.end, phase_end) if phase_end is not None else window.end
                )
                fraction = (
                    window.fraction
                    if effective_window_end == window.end
                    else elapsed_periods(
                        window.start,
                        effective_window_end,
                        frequency,
                        self.config.day_count_convention,
                    )
                )
                window_end_inclusive = effective_window_end - relativedelta(days=1)
                effective_phase_end = (
                    min(phase_end_inclusive, window_end_inclusive)
                    if phase_end_inclusive is not None
                    else window_end_inclusive
                )
                schedule.append(
                    ScheduledPeriod(
                        start=window.start,
                        end=effective_window_end,
                        event_date=event_date(
                            window.start,
                            frequency,
                            timing,
                            phase_start,
                            effective_phase_end,
                        ),
                        fraction=fraction,
                    )
                )
            return tuple(schedule)

        exclusive_end = self.require_timeline_date("operations_end")
        if exclusive_end <= start:
            raise ValueError(f"timeline.operations_end must be after the {section} start")

        operations_end_inclusive = exclusive_end - relativedelta(days=1)
        effective_phase_end = (
            phase_end_inclusive if phase_end_inclusive is not None else operations_end_inclusive
        )

        delta = time_delta_per_period(frequency)
        current = start
        schedule = []
        while current < exclusive_end:
            window_end = min(current + delta, exclusive_end)
            schedule.append(
                ScheduledPeriod(
                    start=current,
                    end=window_end,
                    event_date=event_date(
                        current, frequency, timing, phase_start, effective_phase_end
                    ),
                    fraction=elapsed_periods(
                        current,
                        window_end,
                        frequency,
                        self.config.day_count_convention,
                    ),
                )
            )
            current += delta
        return tuple(schedule)

    @staticmethod
    def warn_configured_schedule_truncated(
        *,
        section: str,
        requested_end: date,
        boundary: date,
    ) -> None:
        """Warn that an explicit period count exceeded ``operations_end``."""
        warnings.warn(
            (
                f"{section} schedule requested through {requested_end.isoformat()} "
                f"but operations_end is {boundary.isoformat()}; entries after "
                "operations_end were truncated. Analyses where operations_end "
                "falls before a configured schedule completes may be misspecified."
            ),
            ScheduleTruncationWarning,
            stacklevel=4,
        )

    def require_timeline_date(
        self,
        field_name: Literal["construction_start", "operations_start", "operations_end"],
    ) -> date:
        """Return the named timeline date or raise ``ValueError`` if it is not set."""
        return self.context.require_timeline_date(field_name)


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


def _format_generation_dates(generation_dates: Iterable[date]) -> str:
    dates = sorted(generation_dates)
    if not dates:
        return "none"
    return ", ".join(event_date.isoformat() for event_date in dates)
