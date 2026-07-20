# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Private configuration objects used by the project builder and compiler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TypeAlias

from dcaf.finance.amortization import AmortizationSchedule
from dcaf.finance.construction import SpendProfile
from dcaf.finance.escalation import (
    ConstantRateEscalation,
    EscalationPolicy,
    _coerce_escalation_policy as coerce_escalation_policy,
    _resolve_escalation_policy_override as resolve_escalation_policy_override,
)
from dcaf.project.config import ProjectValuation
from dcaf.project.contracts import GenerationPrice, EnergyContract
from dcaf.project.policies import GenerationLinkedCashFlowPolicy
from dcaf.project.timeline import ProjectTimeline
from dcaf.shared.types import (
    DayCountConvention,
    InterestTreatment,
    MACRSConvention,
    MACRSPropertyClass,
    Period,
    ProFormaCategory,
    SpendScheduleName,
    TaxTreatment,
    TimingConvention,
    VDBConvention,
    normalize_cashflow_classification,
    parse_day_count_convention,
)
from dcaf.shared.validation import validate_finite, validate_non_negative
from dcaf.streams.cashflows import CashFlowStream
from dcaf.streams.generation import GenerationStream


def validate_outage_dates(start: date, end: date) -> None:
    """Validate an inclusive-start, exclusive-end outage interval."""
    if end <= start:
        raise ValueError("outage end must be after outage start")


def validate_capacity_reduction(capacity_reduction: float) -> None:
    """Validate a fractional outage capacity reduction."""
    validate_finite(capacity_reduction, "capacity_reduction")
    if not 0.0 <= capacity_reduction <= 1.0:
        raise ValueError("capacity_reduction must be between 0 and 1")


@dataclass(frozen=True)
class EscalationSettings:
    """Escalation configuration with a simple and advanced mode."""

    escalation: float = 0.0
    escalation_period: Period = "year"
    amount_reference_date: date | None = None
    policy: EscalationPolicy | None = None
    explicit: bool = False

    def __post_init__(self) -> None:
        validate_finite(self.escalation, "escalation")
        object.__setattr__(self, "policy", coerce_escalation_policy(self.policy))
        resolve_escalation_policy_override(
            escalation=self.escalation,
            escalation_period=self.escalation_period,
            amount_reference_date=self.amount_reference_date,
            escalation_policy=self.policy,
            default_escalation_period="year",
        )

    @property
    def is_configured(self) -> bool:
        return (
            self.explicit
            or self.policy is not None
            or self.escalation != 0.0
            or self.escalation_period != "year"
            or self.amount_reference_date is not None
        )

    def to_kwargs(self) -> dict[str, object]:
        if self.policy is not None:
            return {"escalation_policy": self.policy}
        return {
            "escalation": self.escalation,
            "escalation_period": self.escalation_period,
            "amount_reference_date": self.amount_reference_date,
        }


def effective_escalation(
    local: EscalationSettings, default: EscalationSettings
) -> EscalationSettings:
    """Return *local* if it has been configured, otherwise fall back to *default*."""
    return local if local.is_configured else default


def constant_annual_escalation_rate(settings: EscalationSettings) -> float | None:
    """Return the annual rate when *settings* resolves to a constant annual policy."""
    if settings.policy is not None:
        policy = settings.policy
        if isinstance(policy, ConstantRateEscalation) and policy.period == "year":
            return policy.rate
        return None
    if settings.escalation_period == "year":
        return settings.escalation
    return None


def updated_escalation(
    existing: EscalationSettings,
    *,
    escalation: float | None,
    escalation_period: Period | None,
    amount_reference_date: date | None,
    escalation_policy: EscalationPolicy | None,
) -> EscalationSettings:
    """Return updated escalation settings, merging new values over *existing*.

    When all new values are ``None``, returns *existing* unchanged. When an
    *escalation_policy* is provided it takes precedence over simple-rate inputs.
    Otherwise merges individual fields, keeping existing values for any field
    not explicitly provided.
    """
    if (
        escalation is None
        and escalation_period is None
        and amount_reference_date is None
        and escalation_policy is None
    ):
        return existing

    if escalation_policy is not None:
        return EscalationSettings(policy=escalation_policy, explicit=True)

    return EscalationSettings(
        escalation=existing.escalation if escalation is None else escalation,
        escalation_period=existing.escalation_period
        if escalation_period is None
        else escalation_period,
        amount_reference_date=existing.amount_reference_date
        if amount_reference_date is None
        else amount_reference_date,
        policy=None,
        explicit=True,
    )


@dataclass(frozen=True)
class CapacityGenerationConfig:
    """Configuration for capacity-based generation inputs."""

    capacity_mw: float
    capacity_factor: float
    operations_start: date | None = None
    operations_end: date | None = None
    start: date | None = None
    periods: int | float | None = None
    frequency: Period | None = None
    label: str = "Generation"
    timing: TimingConvention | None = None

    def __post_init__(self) -> None:
        validate_non_negative(self.capacity_mw, "capacity_mw")
        if not 0.0 <= self.capacity_factor <= 1.0:
            raise ValueError("capacity_factor must be between 0 and 1")
        if self.periods is not None and self.periods <= 0:
            raise ValueError("generation periods must be positive")
        if (
            self.operations_start is not None
            and self.operations_end is not None
            and self.operations_end <= self.operations_start
        ):
            raise ValueError("operations_end must be after operations_start")


GenerationConfig: TypeAlias = CapacityGenerationConfig | GenerationStream | None


@dataclass(frozen=True)
class GenerationOutageConfig:
    """Configuration for an outage that reduces modeled generation."""

    name: str
    start: date
    end: date
    capacity_mw: float | None = None
    capacity_factor: float | None = None
    capacity_reduction: float = 1.0
    timing: TimingConvention | None = None
    label: str = "Generation Outage"

    def __post_init__(self) -> None:
        validate_outage_dates(self.start, self.end)
        if self.capacity_mw is not None:
            validate_non_negative(self.capacity_mw, "capacity_mw")
        if self.capacity_factor is not None:
            if not 0.0 <= self.capacity_factor <= 1.0:
                raise ValueError("capacity_factor must be between 0 and 1")
        validate_capacity_reduction(self.capacity_reduction)


@dataclass(frozen=True)
class ConstructionOutageConfig:
    """Configuration for construction-outage economics on unmodeled baseline generation."""

    name: str
    start: date
    end: date
    capacity_mw: float
    capacity_factor: float
    capacity_reduction: float = 1.0
    timing: TimingConvention | None = None
    sell_price_per_unit: float | None = None
    fixed_cost: float = 0.0
    cost_per_day: float = 0.0
    lost_revenue_label: str = "Outage Lost Revenue"
    fixed_cost_label: str = "Outage Fixed Cost"
    daily_cost_label: str = "Outage Replacement Power"
    escalation: EscalationSettings = field(default_factory=EscalationSettings)

    def __post_init__(self) -> None:
        validate_outage_dates(self.start, self.end)
        validate_non_negative(self.capacity_mw, "capacity_mw")
        if not 0.0 <= self.capacity_factor <= 1.0:
            raise ValueError("capacity_factor must be between 0 and 1")
        validate_capacity_reduction(self.capacity_reduction)
        if self.sell_price_per_unit is not None:
            validate_finite(self.sell_price_per_unit, "sell_price_per_unit")
        validate_finite(self.fixed_cost, "fixed_cost")
        validate_finite(self.cost_per_day, "cost_per_day")


@dataclass(frozen=True)
class FixedOpexConfig:
    """Configuration for a recurring fixed OPEX item."""

    amount: float
    start: date | None = None
    periods: int | float | None = None
    frequency: Period | None = None
    label: str = "Fixed OPEX"
    escalation: EscalationSettings = field(default_factory=EscalationSettings)
    timing: TimingConvention | None = None

    def __post_init__(self) -> None:
        validate_finite(self.amount, "fixed opex amount")
        if self.periods is not None and self.periods <= 0:
            raise ValueError("fixed_opex periods must be positive")


@dataclass(frozen=True)
class VariableCostConfig:
    """Configuration for a per-unit variable cost item."""

    rate_per_unit: float
    label: str = "Variable Cost"
    escalation: EscalationSettings = field(default_factory=EscalationSettings)

    def __post_init__(self) -> None:
        validate_finite(self.rate_per_unit, "variable cost rate_per_unit")
        object.__setattr__(self, "rate_per_unit", abs(self.rate_per_unit))


@dataclass(frozen=True)
class ConstructionScheduleConfig:
    """Configuration for construction spend schedule inputs on a single asset."""

    overnight_cost: float
    cod_date: date | None = None
    spend_profile: SpendProfile | SpendScheduleName | None = None
    construction_start: date | None = None
    construction_end: date | None = None
    period: Period = "month"
    escalation: EscalationSettings = field(default_factory=EscalationSettings)

    def __post_init__(self) -> None:
        validate_finite(self.overnight_cost, "overnight_cost")
        if self.overnight_cost <= 0.0:
            raise ValueError("overnight_cost must be positive")
        if self.spend_profile is not None and self.construction_start is None:
            raise ValueError("construction_start is required when spend_profile is provided")
        if (
            self.spend_profile is not None
            and self.construction_start is not None
            and self.construction_end is not None
            and self.construction_end <= self.construction_start
        ):
            raise ValueError("construction_end must be after construction_start")


ConstructionConfig: TypeAlias = ConstructionScheduleConfig | CashFlowStream | None


@dataclass(frozen=True)
class ConstructionFinancingConfig:
    """Configuration for construction-period financing and operations debt service.

    Captures the full debt lifecycle: what fraction of construction cost is
    debt-funded, how interest accrues during construction, and how the resulting
    principal is repaid during operations.
    """

    debt_fraction: float
    construction_interest_rate: float | None = None
    interest_treatment: InterestTreatment = "capitalize"
    servicing_period: Period | None = None
    amortization_rate: float = 0.0
    amortization_term: int = 0
    amortization_frequency: Period = "year"
    amortization_start: date | None = None

    def __post_init__(self) -> None:
        validate_finite(self.debt_fraction, "debt_fraction")
        if not 0.0 <= self.debt_fraction <= 1.0:
            raise ValueError("debt_fraction must be between 0 and 1")
        if self.construction_interest_rate is not None:
            validate_non_negative(self.construction_interest_rate, "construction_interest_rate")
        validate_non_negative(self.amortization_rate, "amortization_rate")
        if self.amortization_term <= 0:
            raise ValueError("amortization_term must be positive")


@dataclass(frozen=True)
class MacrsDepreciationConfig:
    """MACRS depreciation configuration: property class and convention."""

    property_class: MACRSPropertyClass
    convention: MACRSConvention = "half-year"
    label: str = "MACRS Depreciation"


@dataclass(frozen=True)
class VdbDepreciationConfig:
    """VDB (variable declining balance) depreciation configuration."""

    life: int
    salvage_value: float = 0.0
    frequency: Period = "year"
    factor: float = 2.0
    switch_to_straight_line: bool = True
    convention: VDBConvention = "none"
    schedule_dates: tuple[date, ...] | None = None
    valuation_rate: float | None = None
    valuation_date: date | None = None
    terminal_catch_up: bool = False
    label: str = "VDB Depreciation"

    def __post_init__(self) -> None:
        if self.life <= 0:
            raise ValueError("VDB life must be positive")
        validate_non_negative(self.salvage_value, "VDB salvage_value")
        validate_finite(self.factor, "VDB factor")
        if self.factor <= 0.0:
            raise ValueError("VDB factor must be positive")
        if self.convention == "best-of-half-year-mid-quarter":
            if self.valuation_rate is None or self.valuation_date is None:
                raise ValueError(
                    "valuation_rate and valuation_date are required for "
                    "best-of-half-year-mid-quarter schedules"
                )
        elif self.convention == "none" and (
            self.valuation_rate is not None or self.valuation_date is not None
        ):
            raise ValueError(
                "valuation_rate and valuation_date are only supported for "
                "best-of-half-year-mid-quarter schedules"
            )
        if self.valuation_rate is not None:
            validate_finite(self.valuation_rate, "VDB valuation_rate")


DepreciationConfig: TypeAlias = MacrsDepreciationConfig | VdbDepreciationConfig | None


@dataclass(frozen=True)
class ProductionTaxCreditConfig:
    """Configuration for a production tax credit (PTC) on a single asset."""

    rate_per_unit: float
    years: int
    label: str = "PTC"
    escalation: EscalationSettings = field(default_factory=EscalationSettings)

    def __post_init__(self) -> None:
        if self.years <= 0:
            raise ValueError("PTC years must be positive")
        validate_non_negative(self.rate_per_unit, "ptc rate_per_unit")


@dataclass(frozen=True)
class RevenueConfig:
    """Whole-project generation revenue configuration."""

    price: GenerationPrice
    label: str = "Revenue"
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE
    tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE

    def __post_init__(self) -> None:
        if not isinstance(self.price, GenerationPrice):
            raise TypeError("generation_revenue price must be a GenerationPrice")
        category, treatment = normalize_cashflow_classification(
            self.pro_forma_category,
            self.tax_treatment,
        )
        object.__setattr__(self, "pro_forma_category", category)
        object.__setattr__(self, "tax_treatment", treatment)


@dataclass(frozen=True)
class GenerationRevenueContractConfig:
    """Named generation-linked contract revenue registration."""

    name: str
    contract: EnergyContract


@dataclass(frozen=True)
class GenerationRevenueRemainderConfig:
    """Named revenue registration for generation not allocated to contracts."""

    name: str
    price: GenerationPrice
    label: str = "Remainder Revenue"
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE
    tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE

    def __post_init__(self) -> None:
        if not isinstance(self.price, GenerationPrice):
            raise TypeError("generation_revenue_remainder price must be a GenerationPrice")
        category, treatment = normalize_cashflow_classification(
            self.pro_forma_category,
            self.tax_treatment,
        )
        object.__setattr__(self, "pro_forma_category", category)
        object.__setattr__(self, "tax_treatment", treatment)


@dataclass(frozen=True)
class CustomGenerationLinkedPolicyConfig:
    """Named custom generation-linked policy registration."""

    name: str
    policy: GenerationLinkedCashFlowPolicy


GenerationLinkedPolicyConfig: TypeAlias = (
    GenerationRevenueContractConfig
    | GenerationRevenueRemainderConfig
    | CustomGenerationLinkedPolicyConfig
)


@dataclass(frozen=True)
class ProjectConfig:
    """Top-level internal configuration bag for an ``EnergyProject``."""

    frequency: Period = "year"
    timing: TimingConvention = "end"
    day_count_convention: DayCountConvention = "actual/actual"
    timeline: ProjectTimeline = field(default_factory=ProjectTimeline)
    generation: GenerationConfig = None
    generation_outages: tuple[GenerationOutageConfig, ...] = ()
    construction_outages: dict[str, ConstructionOutageConfig] = field(default_factory=dict)
    fixed_opex_items: dict[str, FixedOpexConfig] = field(default_factory=dict)
    variable_cost_items: dict[str, VariableCostConfig] = field(default_factory=dict)
    construction: ConstructionConfig = None
    construction_debt: ConstructionFinancingConfig | None = None
    debt_schedule: AmortizationSchedule | CashFlowStream | None = None
    depreciation: DepreciationConfig = None
    itc_rate: float | None = None
    ptc: ProductionTaxCreditConfig | None = None
    market: RevenueConfig | None = None
    generation_linked_policies: tuple[GenerationLinkedPolicyConfig, ...] = ()
    tax_rate: float | None = None
    tax_allow_refund: bool = False
    valuation: ProjectValuation | None = None
    default_escalation: EscalationSettings = field(default_factory=EscalationSettings)
    custom_cashflows: dict[str, CashFlowStream] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "day_count_convention",
            parse_day_count_convention(str(self.day_count_convention)).value,
        )
        if self.itc_rate is not None:
            validate_non_negative(self.itc_rate, "itc rate")
        if self.tax_rate is not None:
            validate_non_negative(self.tax_rate, "tax rate")
