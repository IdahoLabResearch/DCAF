# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Private compiler for turning project builder configuration into analysis results."""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field, replace as dc_replace
from datetime import date
from math import isclose
from typing import Literal

from dateutil.relativedelta import relativedelta

from dcaf.finance.amortization import AmortizationSchedule
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
    GenerationLinkedPolicyConfig,
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
from dcaf.project.contracts import GenerationPrice, GenerationSettlementEvent, EnergyContract
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
from dcaf.streams.generation import Generation, GenerationStream
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
                dates = [entry.date for entry in gen.entries]
                operations_start = min(dates)
                # operations_end is exclusive; the latest entry date is
                # within the operating window, so the boundary is the
                # following day.
                operations_end = max(dates) + relativedelta(days=1)

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


@dataclass(frozen=True, slots=True)
class ContractGenerationRequest:
    """Resolved request for one contract against one generation event."""

    name: str
    contract: EnergyContract
    entry_index: int
    requested_mwh: float


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
                self.build_construction_outage(outage_config),
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
            frequency = (
                generation.frequency
                if generation.frequency is not None
                else self.config.timeline.frequency
            )
            start = (
                generation.start
                if generation.start is not None
                else self.require_timeline_date("operations_start")
            )
            timing = generation.timing or self.config.timeline.timing
            ops_start = self.config.timeline.operations_start
            ops_end = self.config.timeline.operations_end
            schedule = self.operating_schedule(
                "generation",
                start=start,
                periods=generation.periods,
                frequency=frequency,
                timing=timing,
                phase_start=ops_start,
                phase_end=ops_end,
            )
            entries: list[Generation] = []
            for modeled_period in schedule:
                hours = elapsed_hours(
                    modeled_period.start,
                    modeled_period.end,
                    self.config.day_count_convention,
                )
                entries.append(
                    Generation(
                        amount_mwh=(generation.capacity_mw * generation.capacity_factor * hours),
                        date=modeled_period.event_date,
                        label=generation.label,
                        period_start=modeled_period.start,
                        period_end=modeled_period.end,
                    )
                )
            base_generation = GenerationStream(entries)

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

            timing = outage.timing
            if capacity_defaults is not None:
                timing = timing or capacity_defaults.timing

            outage_streams.append(
                GenerationStream.from_outage(
                    capacity_mw=capacity_mw,
                    capacity_factor=capacity_factor,
                    start=outage.start,
                    end=outage.end,
                    capacity_reduction=outage.capacity_reduction,
                    timing=timing or self.config.timeline.timing,
                    label=outage.label,
                    day_count_convention=self.config.day_count_convention,
                )
            )
        return GenerationStream.from_streams(*outage_streams)

    def build_construction_outage(
        self,
        outage: ConstructionOutageConfig,
    ) -> CashFlowStream:
        """Build operating-cost cashflows for a construction outage on baseline generation."""
        if outage.sell_price_per_unit is None:
            # TODO: Support schedule and callable prices after defining how an outage's
            # aggregate booking event should be priced.
            market = self.config.market
            if market is None or market.price.mode != "fixed" or market.price.fixed_price is None:
                raise ValueError(
                    f"construction_outage {outage.name!r} requires sell_price_per_unit "
                    "unless generation_revenue is configured with a fixed price; "
                    "scheduled and callable generation_revenue prices are not supported "
                    "for construction outages"
                )
            price_per_mwh = market.price.fixed_price
            escalation = EscalationSettings(explicit=True)
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
        self._validate_price_schedule_alignment(
            name="revenue",
            price=market.price,
            generation=generation,
        )
        if not generation.entries:
            return CashFlowStream()
        return self._revenue_cashflows_from_generation(
            name="revenue",
            generation=generation,
            price=market.price,
            label=market.label,
            pro_forma_category=market.pro_forma_category,
            tax_treatment=market.tax_treatment,
        )

    def build_revenue_basis(self, generation: GenerationStream) -> CashFlowStream:
        """Build the unit-price basis for whole-project levelized cost."""
        if not generation.entries or self.config.market is None:
            return CashFlowStream()
        return generation.to_revenue(price_per_mwh=1.0, label="Revenue")

    def build_generation_linked_policy_streams(
        self,
        generation: GenerationStream,
    ) -> tuple[tuple[str, CashFlowStream], ...]:
        """Build named streams for configured generation-linked policies."""
        if not self.config.generation_linked_policies:
            return ()

        self._validate_generation_linked_schedule_alignment(
            generation,
            self.config.generation_linked_policies,
        )
        if not generation.entries:
            return ()

        contract_requests, requested_by_entry = self._contract_generation_requests(
            generation,
            self.config.generation_linked_policies,
        )

        streams: list[tuple[str, CashFlowStream]] = []
        for registration in self.config.generation_linked_policies:
            if isinstance(registration, GenerationRevenueContractConfig):
                stream = self._contract_revenue_stream(
                    generation=generation,
                    registration=registration,
                    requests=contract_requests,
                )
            elif isinstance(registration, GenerationRevenueRemainderConfig):
                stream = self._remainder_revenue_stream(
                    generation=generation,
                    registration=registration,
                    requested_by_entry=requested_by_entry,
                )
            elif isinstance(registration, CustomGenerationLinkedPolicyConfig):
                stream = registration.policy.cashflows(generation)
            else:
                continue
            streams.append((registration.name, stream))
        return tuple(streams)

    def _validate_generation_linked_schedule_alignment(
        self,
        generation: GenerationStream,
        registrations: tuple[GenerationLinkedPolicyConfig, ...],
    ) -> None:
        """Require custom contract quantities and prices to match compiled generation dates."""
        generation_by_date: dict[date, list[Generation]] = {}
        for entry in generation.entries:
            generation_by_date.setdefault(entry.date, []).append(entry)

        for registration in registrations:
            if isinstance(registration, GenerationRevenueContractConfig):
                self._validate_custom_contract_mwh_schedule_alignment(
                    name=registration.name,
                    contract=registration.contract,
                    generation_by_date=generation_by_date,
                )
                self._validate_price_schedule_alignment(
                    name=registration.name,
                    price=registration.contract.price,
                    generation=generation,
                )
            elif isinstance(registration, GenerationRevenueRemainderConfig):
                self._validate_price_schedule_alignment(
                    name=registration.name,
                    price=registration.price,
                    generation=generation,
                )

    @staticmethod
    def _validate_custom_contract_mwh_schedule_alignment(
        *,
        name: str,
        contract: EnergyContract,
        generation_by_date: dict[date, list[Generation]],
    ) -> None:
        """Validate a built-in contract's explicit MWh schedule against generation.

        This applies only to :class:`EnergyContract` instances using
        ``"custom_mwh_generation_schedule"``. Each requested date must map to exactly one
        compiled generation event, and a non-negative event cannot be asked to provide more MWh
        than it contains.

        This does not validate arbitrary :class:`GenerationLinkedCashFlowPolicy` implementations.
        They return cashflows, not MWh requests, so this compiler does not check that every
        generated MWh is counted exactly once across custom policies, built-in contracts, and
        remainder revenue.
        """
        if contract.quantity_mode != "custom_mwh_generation_schedule":
            return
        if contract.requested_generation is None:
            raise ValueError("custom generation schedule contract is missing requested_generation")

        available_dates = _format_generation_dates(generation_by_date)
        for requested in contract.requested_generation:
            matches = generation_by_date.get(requested.date, [])
            if not matches:
                raise ValueError(
                    f"{name} custom MWh schedule requests "
                    f"{_format_mwh(requested.amount_mwh)} MWh on "
                    f"{requested.date.isoformat()}, but the project generation schedule has no "
                    f"event on that date. Available project generation dates: {available_dates}. "
                    "Each custom MWh schedule date must match exactly one project generation "
                    "event; update requested_generation or the project's generation schedule."
                )
            if len(matches) > 1:
                matching_amounts = ", ".join(
                    f"{_format_mwh(entry.amount_mwh)} MWh" for entry in matches
                )
                raise ValueError(
                    f"{name} custom MWh schedule requests "
                    f"{_format_mwh(requested.amount_mwh)} MWh on "
                    f"{requested.date.isoformat()}, but that date matches {len(matches)} project "
                    f"generation events ({matching_amounts}). Each custom MWh schedule date must "
                    "match exactly one project generation event; consolidate the project generation "
                    "events for that date or revise requested_generation."
                )

            available_mwh = matches[0].amount_mwh
            if available_mwh < 0.0:
                continue
            if _request_exceeds_available(requested.amount_mwh, available_mwh):
                raise ValueError(
                    f"{name} custom MWh schedule requests "
                    f"{_format_mwh(requested.amount_mwh)} MWh on "
                    f"{requested.date.isoformat()}, but the matched project generation event "
                    f"provides only {_format_mwh(available_mwh)} MWh. Reduce the requested amount "
                    "or increase project generation on that date."
                )

    @staticmethod
    def _validate_price_schedule_alignment(
        *,
        name: str,
        price: GenerationPrice,
        generation: GenerationStream,
    ) -> None:
        """Require each exact scheduled-price date to exist in project generation."""
        if price.mode != "schedule":
            return

        generation_dates = {entry.date for entry in generation.entries}
        available_dates = _format_generation_dates(generation_dates)
        for scheduled_date, _price in price.price_schedule:
            if scheduled_date not in generation_dates:
                raise ValueError(
                    f"{name} price schedule contains {scheduled_date.isoformat()}, but the project "
                    "generation schedule has no event on that date. Available project generation "
                    f"dates: {available_dates}. Scheduled price dates must correspond to project "
                    "generation dates; remove the price entry or update the project's generation "
                    "schedule."
                )

    def _contract_generation_requests(
        self,
        generation: GenerationStream,
        registrations: tuple[GenerationLinkedPolicyConfig, ...],
    ) -> tuple[tuple[ContractGenerationRequest, ...], tuple[float, ...]]:
        requests: list[ContractGenerationRequest] = []
        requested_by_entry = [0.0 for _entry in generation.entries]

        for registration in registrations:
            if not isinstance(registration, GenerationRevenueContractConfig):
                continue
            for entry_index, entry in enumerate(generation.entries):
                requested_mwh = registration.contract.requested_mwh_for(entry)
                if isclose(requested_mwh, 0.0, rel_tol=0.0, abs_tol=1e-12):
                    continue
                available_mwh = entry.amount_mwh
                if _request_exceeds_available(requested_mwh, available_mwh):
                    raise ValueError(
                        f"{registration.name} requested {_format_mwh(requested_mwh)} MWh "
                        f"on {entry.date.isoformat()}, but only "
                        f"{_format_mwh(available_mwh)} MWh is available"
                    )
                requests.append(
                    ContractGenerationRequest(
                        name=registration.name,
                        contract=registration.contract,
                        entry_index=entry_index,
                        requested_mwh=requested_mwh,
                    )
                )
                requested_by_entry[entry_index] += requested_mwh

        for entry_index, requested_mwh in enumerate(requested_by_entry):
            available_mwh = generation.entries[entry_index].amount_mwh
            if _request_exceeds_available(requested_mwh, available_mwh):
                raise ValueError(
                    "generation-linked contracts request "
                    f"{_format_mwh(requested_mwh)} MWh on "
                    f"{generation.entries[entry_index].date.isoformat()}, but only "
                    f"{_format_mwh(available_mwh)} MWh is available"
                )

        return tuple(requests), tuple(requested_by_entry)

    def _contract_revenue_stream(
        self,
        *,
        generation: GenerationStream,
        registration: GenerationRevenueContractConfig,
        requests: tuple[ContractGenerationRequest, ...],
    ) -> CashFlowStream:
        entries: list[CashFlow] = []
        category, tax_treatment = normalize_cashflow_classification(
            registration.contract.pro_forma_category,
            registration.contract.tax_treatment,
        )
        for request in requests:
            if request.name != registration.name:
                continue
            entry = generation.entries[request.entry_index]
            event = _settlement_event(
                component_name=registration.name,
                entry=entry,
                requested_mwh=request.requested_mwh,
                delivered_mwh=request.requested_mwh,
            )
            price = registration.contract.price.resolve(event)
            entries.append(
                CashFlow(
                    amount=event.delivered_mwh * price,
                    date=entry.date,
                    label=registration.contract.label,
                    is_cash=True,
                    pro_forma_category=category,
                    tax_treatment=tax_treatment,
                )
            )
        return CashFlowStream(entries)

    def _remainder_revenue_stream(
        self,
        *,
        generation: GenerationStream,
        registration: GenerationRevenueRemainderConfig,
        requested_by_entry: tuple[float, ...],
    ) -> CashFlowStream:
        entries: list[CashFlow] = []
        category, tax_treatment = normalize_cashflow_classification(
            registration.pro_forma_category,
            registration.tax_treatment,
        )
        for entry_index, entry in enumerate(generation.entries):
            delivered_mwh = entry.amount_mwh - requested_by_entry[entry_index]
            if isclose(delivered_mwh, 0.0, rel_tol=0.0, abs_tol=1e-12):
                continue
            event = _settlement_event(
                component_name=registration.name,
                entry=entry,
                requested_mwh=delivered_mwh,
                delivered_mwh=delivered_mwh,
            )
            price = registration.price.resolve(event)
            entries.append(
                CashFlow(
                    amount=event.delivered_mwh * price,
                    date=entry.date,
                    label=registration.label,
                    is_cash=True,
                    pro_forma_category=category,
                    tax_treatment=tax_treatment,
                )
            )
        return CashFlowStream(entries)

    def _revenue_cashflows_from_generation(
        self,
        *,
        name: str,
        generation: GenerationStream,
        price: GenerationPrice,
        label: str,
        pro_forma_category: ProFormaCategory | str | None,
        tax_treatment: TaxTreatment | str,
    ) -> CashFlowStream:
        entries: list[CashFlow] = []
        category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category,
            tax_treatment,
        )
        for entry in generation.entries:
            event = _settlement_event(
                component_name=name,
                entry=entry,
                requested_mwh=entry.amount_mwh,
                delivered_mwh=entry.amount_mwh,
            )
            entries.append(
                CashFlow(
                    amount=event.delivered_mwh * price.resolve(event),
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
            )
        return generation.to_cost(
            rate_per_mwh=variable.rate_per_unit,
            label=variable.label,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
            day_count_convention=self.config.day_count_convention,
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
        Returns an empty stream when no debt is configured.
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
        schedule = AmortizationSchedule.build(
            principal=principal,
            annual_rate=debt.amortization_rate,
            term=debt.amortization_term,
            start_date=start,
            frequency=debt.amortization_frequency,
        )
        ops_start = self.config.timeline.operations_start
        ops_end = self.config.timeline.operations_end
        return self.remap_event_dates(
            CashFlowStream.from_streams(schedule.interest, schedule.principal).sort(),
            frequency=debt.amortization_frequency,
            phase_start=ops_start,
            phase_end=ops_end,
            truncate_after_phase_end=True,
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


def _settlement_event(
    *,
    component_name: str,
    entry: Generation,
    requested_mwh: float,
    delivered_mwh: float,
) -> GenerationSettlementEvent:
    available_mwh = entry.amount_mwh
    shortfall_mwh = requested_mwh - delivered_mwh
    allocated_generation_share = 0.0
    if not isclose(available_mwh, 0.0, rel_tol=0.0, abs_tol=1e-12):
        allocated_generation_share = delivered_mwh / available_mwh
    return GenerationSettlementEvent(
        date=entry.date,
        available_mwh=available_mwh,
        requested_mwh=requested_mwh,
        delivered_mwh=delivered_mwh,
        shortfall_mwh=shortfall_mwh,
        allocated_generation_share=allocated_generation_share,
        component_name=component_name,
    )


def _format_mwh(value: float) -> str:
    return f"{value:.1f}"


def _format_generation_dates(generation_dates: Iterable[date]) -> str:
    dates = sorted(generation_dates)
    if not dates:
        return "none"
    return ", ".join(event_date.isoformat() for event_date in dates)


def _request_exceeds_available(requested_mwh: float, available_mwh: float) -> bool:
    if available_mwh < 0.0:
        return requested_mwh > 0.0 or requested_mwh < available_mwh - 1e-9
    return requested_mwh - available_mwh > 1e-9
