"""High-level project builder APIs for composing DCAF analyses.

This module provides an explicit ``EnergyProject`` builder that wraps the
lower-level DCAF primitives into a configuration-oriented workflow. The builder
stays immutable: each configuration method returns a new project.

The implementation is intentionally plural-first. Assets are keyed by name and
market assumptions are keyed by carrier, with optional asset-specific market
overrides. The single-asset case remains ergonomic by using the implicit asset
name ``"default"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace as dc_replace
from datetime import date
from math import isclose, isfinite
from typing import Callable, Literal, Self

from dateutil.relativedelta import relativedelta

from dcaf.project.analysis import ProjectAnalysis, ProjectMetrics, ProjectProForma
from dcaf.project.config import ProjectValuation, wacc as _wacc
from dcaf.project.timeline import ProjectTimeline
from dcaf.finance.amortization import AmortizationSchedule
from dcaf.finance.outage import construction_outage as _construction_outage_helper
from dcaf.streams.cashflows import CashFlow, CashFlowGroup, CashFlowStream
from dcaf.finance.construction import (
    ConstructionFinancing,
    SpendProfile,
    construction_spend_schedule,
)
from dcaf.tax.depreciation import macrs_schedule, vdb_schedule
from dcaf.finance.escalation import (
    ConstantRateEscalation,
    EscalationPolicy,
    _coerce_escalation_policy,
    _resolve_escalation_policy_override,
)
from dcaf.streams.generation import Generation, GenerationStream
from dcaf.tax.incentives import itc, itc_adjusted_basis, ptc
from dcaf.tax.liability import compute_taxable_income, tax_liability
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
)
from dcaf.shared.formatting import format_label
from dcaf.shared.time import elapsed_periods, event_date, hours_per_period, time_delta_per_period

type CashFlowComponentModifier = Callable[
    [CashFlowGroup[str]],
    CashFlowGroup[str] | Mapping[str, CashFlowStream],
]


def _validate_finite(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is not finite (inf or NaN)."""
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


def _validate_non_negative(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is negative or not finite."""
    _validate_finite(value, name)
    if value < 0.0:
        raise ValueError(f"{name} must be non-negative")


def _validate_outage_dates(start: date, end: date) -> None:
    """Validate an inclusive-start, exclusive-end outage interval."""
    if end <= start:
        raise ValueError("outage end must be after outage start")


def _validate_capacity_reduction(capacity_reduction: float) -> None:
    """Validate a fractional outage capacity reduction."""
    _validate_finite(capacity_reduction, "capacity_reduction")
    if not 0.0 <= capacity_reduction <= 1.0:
        raise ValueError("capacity_reduction must be between 0 and 1")


@dataclass(frozen=True)
class _EscalationSettings:
    """Escalation configuration with a simple and advanced mode."""

    escalation: float = 0.0
    escalation_period: Period = "year"
    amount_reference_date: date | None = None
    policy: EscalationPolicy | None = None
    explicit: bool = False

    def __post_init__(self) -> None:
        _validate_finite(self.escalation, "escalation")
        object.__setattr__(self, "policy", _coerce_escalation_policy(self.policy))
        _resolve_escalation_policy_override(
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


def _effective_escalation(
    local: _EscalationSettings,
    default: _EscalationSettings,
) -> _EscalationSettings:
    """Return *local* if it has been configured, otherwise fall back to *default*."""
    return local if local.is_configured else default


def _constant_annual_escalation_rate(settings: _EscalationSettings) -> float | None:
    """Return the annual rate when *settings* resolves to a constant annual policy."""
    if settings.policy is not None:
        policy = settings.policy
        if isinstance(policy, ConstantRateEscalation) and policy.period == "year":
            return policy.rate
        return None
    if settings.escalation_period == "year":
        return settings.escalation
    return None


@dataclass(frozen=True)
class _ScheduledPeriod:
    """One modeled operating period with an optional partial-period fraction.

    ``event_date`` is the booking date for the period, computed from the timing
    convention and phase boundaries. It defaults to ``start`` when not provided.
    """

    start: date
    event_date: date
    fraction: float = 1.0


@dataclass(frozen=True)
class _CapacityGenerationConfig:
    """Configuration for capacity-based generation inputs on a single asset."""

    capacity_mw: float
    capacity_factor: float
    operations_start: date | None = None
    operations_end: date | None = None
    start: date | None = None
    periods: int | None = None
    frequency: Period | None = None
    carrier: str = "electricity"
    source: str | None = None
    label: str = "Generation"
    timing: TimingConvention | None = None


@dataclass(frozen=True)
class _GenerationStreamConfig:
    """Configuration for an explicit generation stream override.

    Operations dates are inferred from the minimum and maximum dates in the
    provided stream when not explicitly overridden elsewhere.
    """

    stream: GenerationStream


type _GenerationConfig = _CapacityGenerationConfig | _GenerationStreamConfig | None


@dataclass(frozen=True)
class _GenerationOutageConfig:
    """Configuration for an outage that reduces modeled generation."""

    name: str
    start: date
    end: date
    capacity_mw: float | None = None
    capacity_factor: float | None = None
    capacity_reduction: float = 1.0
    timing: TimingConvention | None = None
    source: str | None = None
    carrier: str | None = None
    label: str = "Generation Outage"


@dataclass(frozen=True)
class _ConstructionOutageConfig:
    """Configuration for construction-outage economics on unmodeled baseline generation."""

    name: str
    start: date
    end: date
    capacity_mw: float
    capacity_factor: float
    capacity_reduction: float = 1.0
    timing: TimingConvention | None = None
    carrier: str = "electricity"
    source: str = "construction-outage"
    sell_price_per_unit: float | None = None
    fixed_cost: float = 0.0
    cost_per_day: float = 0.0
    lost_revenue_label: str = "Outage Lost Revenue"
    fixed_cost_label: str = "Outage Fixed Cost"
    daily_cost_label: str = "Outage Replacement Power"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _RecurringCostConfig:
    """Configuration for a recurring (fixed) operating cost item."""

    amount: float
    start: date | None = None
    periods: int | None = None
    frequency: Period | None = None
    label: str = "Fixed OPEX"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)
    timing: TimingConvention | None = None


@dataclass(frozen=True)
class _VariableCostConfig:
    """Configuration for a per-unit variable cost item."""

    rate_per_unit: float
    label: str = "Variable Cost"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _ConstructionScheduleConfig:
    """Configuration for construction spend schedule inputs on a single asset."""

    overnight_cost: float
    cod_date: date | None = None
    spend_profile: SpendProfile | SpendScheduleName | None = None
    construction_start: date | None = None
    construction_end: date | None = None
    period: Period = "month"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _ConstructionStreamConfig:
    """Configuration for an explicit construction cash-flow stream override."""

    stream: CashFlowStream


type _ConstructionConfig = _ConstructionScheduleConfig | _ConstructionStreamConfig | None


@dataclass(frozen=True)
class _ConstructionDebtConfig:
    """Configuration for construction-period debt and its operations-period amortization.

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


@dataclass(frozen=True)
class _DebtScheduleConfig:
    """Configuration for an explicit debt schedule override."""

    schedule: AmortizationSchedule | CashFlowStream


@dataclass(frozen=True)
class _MacrsDepreciationConfig:
    """MACRS depreciation configuration: property class and convention."""

    property_class: MACRSPropertyClass
    convention: MACRSConvention = "half-year"
    label: str = "MACRS Depreciation"


@dataclass(frozen=True)
class _VdbDepreciationConfig:
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


type _DepreciationConfig = _MacrsDepreciationConfig | _VdbDepreciationConfig | None


@dataclass(frozen=True)
class _PtcConfig:
    """Configuration for a production tax credit (PTC) on a single asset."""

    rate_per_unit: float
    years: int
    label: str = "PTC"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _AssetConfig:
    """All configuration for a single named project asset."""

    generation: _GenerationConfig = None
    generation_outages: tuple[_GenerationOutageConfig, ...] = ()
    construction_outages: dict[str, _ConstructionOutageConfig] = field(default_factory=dict)
    fixed_opex_items: dict[str, _RecurringCostConfig] = field(default_factory=dict)
    variable_cost_items: dict[str, _VariableCostConfig] = field(default_factory=dict)
    construction: _ConstructionConfig = None
    construction_debt: _ConstructionDebtConfig | None = None
    debt_schedule: _DebtScheduleConfig | None = None
    depreciation: _DepreciationConfig = None
    itc_rate: float | None = None
    ptc: _PtcConfig | None = None


@dataclass(frozen=True)
class _MarketConfig:
    """Market price and escalation configuration for one energy carrier."""

    sell_price_per_unit: float
    unit: str | None = None
    label: str = "Market Revenue"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _ProjectConfig:
    """Top-level internal configuration bag for an ``EnergyProject``."""

    default_asset: str = "default"
    frequency: Period = "year"
    timing: TimingConvention = "end"
    timeline: ProjectTimeline = field(default_factory=ProjectTimeline)
    assets: dict[str, _AssetConfig] = field(default_factory=dict)
    markets: dict[str, _MarketConfig] = field(default_factory=dict)
    tax_rate: float | None = None
    tax_allow_refund: bool = False
    valuation: ProjectValuation | None = None
    default_escalation: _EscalationSettings = field(default_factory=_EscalationSettings)
    custom_cashflows: dict[str, CashFlowStream] = field(default_factory=dict)
    cashflow_modifiers: tuple[CashFlowComponentModifier, ...] = ()


class EnergyProject:
    """Immutable fluent builder for composing and analyzing energy project cash flows.

    Each configuration method returns a new ``EnergyProject`` instance, leaving
    the original unchanged. All builder methods operate on the single asset
    configured at construction time.

    Call :meth:`analyze` to compile all configured inputs into a
    :class:`ProjectAnalysis`, or use the convenience methods
    :meth:`cashflows`, :meth:`metrics`, and :meth:`pro_forma` to skip the
    intermediate result.

    Examples
    --------
    >>> from datetime import date
    >>> analysis = (
    ...     EnergyProject()
    ...     .discount_rate(rate=0.08)
    ...     .generation(
    ...         capacity_mw=100.0,
    ...         capacity_factor=0.35,
    ...         operations_start=date(2026, 1, 1),
    ...         operations_end=date(2046, 1, 1),
    ...     )
    ...     .revenue_from_generation(sell_price_per_unit=50.0)
    ...     .construction(
    ...         overnight_cost=200_000_000,
    ...         spend_profile="flat",
    ...         construction_start=date(2025, 1, 1),
    ...     )
    ...     .tax(rate=0.21)
    ...     .analyze()
    ... )
    >>> metrics = analysis.metrics()
    """

    def __init__(
        self,
        *,
        asset: str = "default",
        frequency: Period = "year",
        timing: TimingConvention = "end",
    ) -> None:
        """Initialize a new immutable project builder.

        Parameters
        ----------
        asset : str, optional
            Name for the single project asset. Used as a prefix for component
            keys in the compiled :class:`ProjectAnalysis` (e.g.
            ``"<asset>:revenue"``). Default is ``"default"``.
        frequency : Period, optional
            Default operating frequency for recurring items. Default is
            ``"year"``; change only when modeling sub-annual periods.
        timing : TimingConvention, optional
            Default event-date convention. ``"end"`` (default) books events at
            the end of the calendar period, capped by the phase boundary.
            ``"begin"`` books events at the start of the calendar period,
            floored by the phase start.
        """
        self._config = _ProjectConfig(
            default_asset=asset,
            frequency=frequency,
            timing=timing,
        )

    @classmethod
    def from_config(cls, config: _ProjectConfig) -> Self:
        """Construct a project from an existing internal configuration.

        Parameters
        ----------
        config : _ProjectConfig
            Internal configuration snapshot to attach to the returned project.

        Returns
        -------
        EnergyProject
            New project instance using ``config`` as-is.

        Notes
        -----
        This is primarily an internal or advanced escape hatch; most callers
        should prefer the fluent builder methods.
        """
        project = cls.__new__(cls)
        project._config = config
        return project

    def _copy(self, *, config: _ProjectConfig | None = None) -> Self:
        """Return a new project sharing the current config, optionally overriding it."""
        project = self.__class__.__new__(self.__class__)
        project._config = self._config if config is None else config
        return project

    @property
    def valuation_config(self) -> ProjectValuation | None:
        """Return the current valuation configuration.

        Returns
        -------
        ProjectValuation or None
            Current valuation configuration, or ``None`` when none has been
            configured.
        """
        return self._config.valuation

    def discount_rate(
        self,
        *,
        rate: float,
    ) -> Self:
        """Configure the project's default discount rate.

        Parameters
        ----------
        rate : float
            Project-wide default discount rate.

        Returns
        -------
        EnergyProject
            New project with updated valuation assumptions.
        """
        _validate_finite(rate, "discount_rate")
        return self._copy(
            config=dc_replace(
                self._config,
                valuation=ProjectValuation.from_discount_rate(rate),
            )
        )

    def wacc(
        self,
        *,
        debt_fraction: float,
        debt_cost: float,
        equity_cost: float,
        tax_rate: float,
        equity_fraction: float | None = None,
    ) -> Self:
        """Configure the project's default valuation using explicit WACC inputs.

        Parameters
        ----------
        debt_fraction : float
            Debt share of total capital. Must be between 0 and 1.
        debt_cost : float
            Pre-tax cost of debt.
        equity_cost : float
            Cost of equity.
        tax_rate : float
            Marginal tax rate applied to the debt interest tax shield.
        equity_fraction : float, optional
            Equity share of total capital. Defaults to ``1 - debt_fraction``.
            When provided explicitly, must equal ``1 - debt_fraction``.

        Returns
        -------
        EnergyProject
            New project with updated valuation assumptions.

        Raises
        ------
        ValueError
            If any rate or fraction is non-finite, if either capital fraction
            is negative, or if the fractions do not sum to ``1.0``.
        """
        return self._copy(
            config=dc_replace(
                self._config,
                valuation=ProjectValuation.from_discount_rate(
                    _wacc(
                        debt_fraction=debt_fraction,
                        debt_cost=debt_cost,
                        equity_fraction=equity_fraction,
                        equity_cost=equity_cost,
                        tax_rate=tax_rate,
                    )
                ),
            )
        )

    def default_escalation(
        self,
        *,
        rate: float | EscalationPolicy,
        escalation_period: Period = "year",
        amount_reference_date: date | None = None,
    ) -> Self:
        """Set the project-wide default escalation applied when no item-level escalation is configured.

        Parameters
        ----------
        rate : float or EscalationPolicy
            Annual escalation rate as a decimal (e.g. ``0.02`` for 2 %), or a
            fully configured :class:`EscalationPolicy` for non-constant schedules.
        escalation_period : Period, optional
            Period over which the rate applies. Default is ``"year"``.
        amount_reference_date : date, optional
            Reference date at which base amounts are stated. Ignored when *rate*
            is an ``EscalationPolicy``.

        Returns
        -------
        EnergyProject
            New project with updated default escalation.
        """
        if isinstance(rate, (int, float)):
            settings = _EscalationSettings(
                escalation=float(rate),
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                explicit=True,
            )
        else:
            settings = _EscalationSettings(policy=rate, explicit=True)
        return self._copy(config=dc_replace(self._config, default_escalation=settings))

    def generation(
        self,
        *,
        capacity_mw: float,
        capacity_factor: float = 1.0,
        operations_start: date | None = None,
        operations_end: date | None = None,
        start: date | None = None,
        periods: int | None = None,
        frequency: Period | None = None,
        carrier: str = "electricity",
        source: str | None = None,
        label: str | None = None,
        timing: TimingConvention | None = None,
    ) -> Self:
        """Configure capacity-based generation for an asset.

        The ``operations_start`` and ``operations_end`` dates define the
        operating window for the entire project. All recurring operating
        streams (fixed OPEX, debt service, depreciation, tax incentives) use
        these dates as their default boundaries.

        Parameters
        ----------
        capacity_mw : float
            Nameplate capacity in megawatts.
        capacity_factor : float, optional
            Fraction of full capacity realized on average (0–1).
        operations_start : date, optional
            Date on which the asset enters operation. Used as the default start
            for generation, fixed OPEX, debt service, depreciation, and tax
            incentives. Required when ``start`` and ``periods`` are not both
            provided.
        operations_end : date, optional
            Exclusive end boundary for operations (the first day after
            operations cease). When ``periods`` is not provided, the builder
            infers the schedule from ``operations_start`` through (but not
            including) ``operations_end`` and prorates any trailing partial
            period.
        start : date, optional
            First generation period start date. Defaults to ``operations_start``.
        periods : int, optional
            Number of generation periods. Inferred from ``operations_end`` when
            omitted.
        frequency : Period, optional
            Generation period frequency. Defaults to the project-wide frequency
            set at construction time.
        carrier : str, optional
            Energy carrier label (e.g. ``"electricity"``). Default is ``"electricity"``.
        source : str, optional
            Generation source label. Defaults to the asset name.
        label : str, optional
            Label template for individual generation entries. Use ``{n}`` as a
            period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with updated generation configuration.
        """
        asset = self._config.default_asset
        _validate_finite(capacity_mw, "capacity_mw")
        _validate_finite(capacity_factor, "capacity_factor")
        asset_config = self._asset_config(asset)
        updated_generation = _CapacityGenerationConfig(
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            operations_start=operations_start,
            operations_end=operations_end,
            start=start,
            periods=periods,
            frequency=frequency,
            carrier=carrier,
            source=source,
            label="Generation" if label is None else label,
            timing=timing,
        )
        return self._with_asset(asset, dc_replace(asset_config, generation=updated_generation))

    def generation_stream(
        self,
        *,
        stream: GenerationStream,
    ) -> Self:
        """Configure a pre-built generation stream for the asset.

        Operations-period dates are inferred from the minimum and maximum dates
        in the provided stream.

        Parameters
        ----------
        stream : GenerationStream
            Fully specified generation stream.

        Returns
        -------
        EnergyProject
            New project with updated generation configuration.
        """
        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        return self._with_asset(
            asset,
            dc_replace(asset_config, generation=_GenerationStreamConfig(stream=stream)),
        )

    def generation_outage(
        self,
        *,
        start: date,
        end: date,
        name: str = "default",
        capacity_mw: float | None = None,
        capacity_factor: float | None = None,
        capacity_reduction: float = 1.0,
        timing: TimingConvention | None = None,
        source: str | None = None,
        carrier: str | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure an outage that reduces modeled project generation.

        The outage is represented internally as ordinary negative
        ``Generation``. It is therefore included in :attr:`ProjectAnalysis.generation`
        and naturally affects generation-derived revenue, variable costs, PTC,
        total generation, discounted generation, and LCOE.

        Parameters
        ----------
        start : date
            Inclusive outage start date.
        end : date
            Exclusive outage end date.
        name : str, optional
            User-facing outage name for labels and diagnostics.
        capacity_mw : float, optional
            Capacity affected by the outage. Defaults to the configured
            capacity-based generation capacity when available.
        capacity_factor : float, optional
            Counterfactual capacity factor during the outage. Defaults to the
            configured capacity-based generation capacity factor when available.
        capacity_reduction : float, optional
            Fraction of affected capacity unavailable during the outage.
        timing : TimingConvention, optional
            Booking date convention for the negative generation entry. Defaults
            to the outage generation timing or project timing.
        source : str, optional
            Source identifier for the outage generation entry. Defaults to the
            generation source.
        carrier : str, optional
            Energy carrier. Defaults to the generation carrier.
        label : str, optional
            Label for the negative generation entry.

        Returns
        -------
        EnergyProject
            New project with the outage registered.
        """
        _validate_outage_dates(start, end)
        if capacity_mw is not None:
            _validate_non_negative(capacity_mw, "capacity_mw")
        if capacity_factor is not None:
            _validate_non_negative(capacity_factor, "capacity_factor")
        _validate_capacity_reduction(capacity_reduction)

        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        outage = _GenerationOutageConfig(
            name=name,
            start=start,
            end=end,
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            capacity_reduction=capacity_reduction,
            timing=timing,
            source=source,
            carrier=carrier,
            label="Generation Outage" if label is None else label,
        )
        return self._with_asset(
            asset,
            dc_replace(
                asset_config,
                generation_outages=(*asset_config.generation_outages, outage),
            ),
        )

    def construction_outage(
        self,
        *,
        start: date,
        end: date,
        capacity_mw: float,
        capacity_factor: float,
        name: str = "default",
        capacity_reduction: float = 1.0,
        timing: TimingConvention | None = None,
        carrier: str = "electricity",
        source: str = "construction-outage",
        sell_price_per_unit: float | None = None,
        fixed_cost: float = 0.0,
        cost_per_day: float = 0.0,
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        lost_revenue_label: str | None = None,
        fixed_cost_label: str | None = None,
        daily_cost_label: str | None = None,
    ) -> Self:
        """Configure construction-outage economics on unmodeled baseline generation.

        Models the typical nuclear-uprate scenario where a refueling outage on
        the existing baseline plant is extended to perform uprate work. The
        baseline plant is not part of the project's modeled generation, but the
        outage's lost revenue and any replacement-power costs are economic
        impacts of the project. Lost generation is represented internally as
        negative ``Generation`` and converted to operating-cost cashflows; the
        compiled analysis generation stream is not changed.

        Lost revenue, fixed cost, and per-day cost appear as **distinct
        cashflows** in the resulting component, so each shows up as a separate
        line item in pro-forma output.

        Parameters
        ----------
        start, end : date
            Inclusive outage start and exclusive outage end.
        capacity_mw : float
            Baseline capacity affected by the outage.
        capacity_factor : float
            Counterfactual capacity factor during the outage.
        name : str, optional
            Component name suffix used in the compiled analysis.
        capacity_reduction : float, optional
            Fraction of affected capacity unavailable during the outage.
        timing : TimingConvention, optional
            Booking-date convention for generated cashflows.
        carrier : str, optional
            Market carrier used for price lookup when ``sell_price_per_unit`` is omitted.
        source : str, optional
            Source identifier for the internal negative generation.
        sell_price_per_unit : float, optional
            Explicit outage price per MWh. When omitted, the configured market
            price for ``carrier`` is used.
        fixed_cost : float, optional
            Additional one-time outage cost. Sign is ignored.
        cost_per_day : float, optional
            Additional outage cost per calendar day. Sign is ignored.
        lost_revenue_label, fixed_cost_label, daily_cost_label : str, optional
            Per-flow labels for the three cashflow components.

        Advanced
        --------
        escalation_period, amount_reference_date, escalation_policy
            Escalation settings used when ``sell_price_per_unit`` is explicit.
            Otherwise the configured market escalation is used.

        Returns
        -------
        EnergyProject
            New project with the construction outage registered.
        """
        _validate_outage_dates(start, end)
        _validate_non_negative(capacity_mw, "capacity_mw")
        _validate_non_negative(capacity_factor, "capacity_factor")
        _validate_capacity_reduction(capacity_reduction)
        if sell_price_per_unit is not None:
            _validate_finite(sell_price_per_unit, "sell_price_per_unit")
        _validate_finite(fixed_cost, "fixed_cost")
        _validate_finite(cost_per_day, "cost_per_day")

        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        outage = _ConstructionOutageConfig(
            name=name,
            start=start,
            end=end,
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            capacity_reduction=capacity_reduction,
            timing=timing,
            carrier=carrier,
            source=source,
            sell_price_per_unit=sell_price_per_unit,
            fixed_cost=fixed_cost,
            cost_per_day=cost_per_day,
            lost_revenue_label="Outage Lost Revenue"
            if lost_revenue_label is None
            else lost_revenue_label,
            fixed_cost_label="Outage Fixed Cost" if fixed_cost_label is None else fixed_cost_label,
            daily_cost_label="Outage Replacement Power"
            if daily_cost_label is None
            else daily_cost_label,
            escalation=_updated_escalation(
                _EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        outages = dict(asset_config.construction_outages)
        outages[name] = outage
        return self._with_asset(
            asset,
            dc_replace(asset_config, construction_outages=outages),
        )

    def revenue_from_generation(
        self,
        *,
        sell_price_per_unit: float,
        carrier: str = "electricity",
        unit: str | None = None,
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure the revenue price for an energy carrier.

        Parameters
        ----------
        sell_price_per_unit : float
            Price per MWh at the amount reference date.
        carrier : str, optional
            Energy carrier key (e.g. ``"electricity"``). Default is
            ``"electricity"``. Must match the ``carrier`` set on
            :meth:`generation`.
        unit : str, optional
            Unit label for display purposes.
        escalation : float, optional
            Annual price escalation rate. When omitted, falls back to the
            project-wide rate set by :meth:`default_escalation`.

        Advanced
        --------
        escalation_period : Period, optional
            Period over which the escalation rate applies. Default is ``"year"``.
        amount_reference_date : date, optional
            Date at which ``sell_price_per_unit`` is stated.
        escalation_policy : EscalationPolicy, optional
            Fully configured escalation policy. Overrides simple-rate inputs.
        label : str, optional
            Label template for individual revenue cashflows. Use ``{n}`` as a
            period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with updated revenue configuration.
        """
        _validate_finite(sell_price_per_unit, "sell_price_per_unit")
        updated = _MarketConfig(
            sell_price_per_unit=sell_price_per_unit,
            unit=unit,
            label="Revenue" if label is None else label,
            escalation=_updated_escalation(
                _EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        markets = dict(self._config.markets)
        markets[carrier] = updated
        return self._copy(config=dc_replace(self._config, markets=markets))

    def fixed_opex(
        self,
        *,
        name: str = "default",
        amount: float,
        start: date | None = None,
        periods: int | None = None,
        frequency: Period | None = None,
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
        timing: TimingConvention | None = None,
    ) -> Self:
        """Configure a named fixed operating cost item for the asset.

        Parameters
        ----------
        name : str, optional
            Item name allowing multiple independent fixed-cost streams per asset.
            Default is ``"default"``.
        amount : float
            Cost amount per period (sign is ignored; applied as an outflow).
        start : date, optional
            First period start date. Defaults to ``timeline.operations_start``.
        periods : int, optional
            Number of periods. Inferred from ``timeline.operations_end`` when omitted.
        frequency : Period, optional
            Cost period frequency. Defaults to ``timeline.frequency``.
        escalation : float, optional
            Annual cost escalation rate. When omitted, falls back to the
            project-wide rate set by :meth:`default_escalation`.

        Advanced
        --------
        escalation_period : Period, optional
            Period over which the escalation rate applies. Default is ``"year"``.
        amount_reference_date : date, optional
            Date at which *amount* is stated.
        escalation_policy : EscalationPolicy, optional
            Fully configured escalation policy. Overrides simple-rate inputs.
        label : str, optional
            Label template for individual cost cashflows. Use ``{n}`` as a
            period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with updated fixed OPEX configuration.
        """
        asset = self._config.default_asset
        _validate_finite(amount, "fixed opex amount")
        asset_config = self._asset_config(asset)
        updated = _RecurringCostConfig(
            amount=amount,
            start=start,
            periods=periods,
            frequency=frequency,
            label="Fixed OPEX" if label is None else label,
            escalation=_updated_escalation(
                _EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
            timing=timing,
        )
        items = dict(asset_config.fixed_opex_items)
        items[name] = updated
        return self._with_asset(asset, dc_replace(asset_config, fixed_opex_items=items))

    def variable_cost(
        self,
        *,
        rate_per_unit: float,
        name: str = "default",
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure a per-MWh variable operating cost for the asset.

        Parameters
        ----------
        rate_per_unit : float
            Cost per MWh (sign is ignored; applied as an outflow).
        name : str, optional
            Item name. Default is ``"default"``.
        escalation : float, optional
            Annual escalation rate. When omitted, falls back to the project-wide
            rate set by :meth:`default_escalation`.

        Advanced
        --------
        escalation_period : Period, optional
            Period over which the escalation rate applies. Default is ``"year"``.
        amount_reference_date : date, optional
            Date at which *rate_per_unit* is stated.
        escalation_policy : EscalationPolicy, optional
            Fully configured escalation policy.
        label : str, optional
            Label template. Use ``{n}`` as a period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with updated variable cost configuration.
        """
        asset = self._config.default_asset
        _validate_finite(rate_per_unit, "variable cost rate_per_unit")
        asset_config = self._asset_config(asset)
        updated = _VariableCostConfig(
            rate_per_unit=rate_per_unit,
            label="Variable Cost" if label is None else label,
            escalation=_updated_escalation(
                _EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        items = dict(asset_config.variable_cost_items)
        items[name] = updated
        return self._with_asset(asset, dc_replace(asset_config, variable_cost_items=items))

    def construction(
        self,
        *,
        overnight_cost: float,
        cod_date: date | None = None,
        spend_profile: SpendProfile | SpendScheduleName | None = None,
        construction_start: date | None = None,
        construction_end: date | None = None,
        period: Period = "month",
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
    ) -> Self:
        """Configure the construction spend schedule for an asset.

        When ``spend_profile`` is omitted the overnight cost is booked as a
        single cash flow on the commercial operations date (``cod_date``, or
        ``operations_start`` when not provided). When a ``spend_profile`` is
        given, the cost is distributed over the construction period using
        ``construction_start`` and ``construction_end``.

        Parameters
        ----------
        overnight_cost : float
            Total overnight capital cost (excluding financing costs).
        cod_date : date, optional
            Commercial operations date. Defaults to ``operations_start`` from
            the generation configuration.
        spend_profile : SpendProfile or SpendScheduleName, optional
            Spend curve shape. When provided, ``construction_start`` is
            required and the cost is distributed over the construction period.
        construction_start : date, optional
            Construction start date. Required when ``spend_profile`` is provided.
        construction_end : date, optional
            Construction end date (exclusive). Defaults to ``cod_date`` (or
            ``operations_start``) when omitted.
        period : Period, optional
            Construction sub-period frequency. Default is ``"month"``.
        escalation : float, optional
            Annual cost escalation rate during construction. When omitted, falls
            back to the project-wide rate set by :meth:`default_escalation`.

        Advanced
        --------
        escalation_period : Period, optional
            Period over which the escalation rate applies. Default is ``"year"``.
        amount_reference_date : date, optional
            Date at which *overnight_cost* is stated.
        escalation_policy : EscalationPolicy, optional
            Fully configured escalation policy.

        Returns
        -------
        EnergyProject
            New project with updated construction configuration.
        """
        asset = self._config.default_asset
        _validate_finite(overnight_cost, "overnight_cost")
        asset_config = self._asset_config(asset)
        updated = _ConstructionScheduleConfig(
            overnight_cost=overnight_cost,
            cod_date=cod_date,
            spend_profile=spend_profile,
            construction_start=construction_start,
            construction_end=construction_end,
            period=period,
            escalation=_updated_escalation(
                _EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        return self._with_asset(asset, dc_replace(asset_config, construction=updated))

    def construction_stream(
        self,
        *,
        stream: CashFlowStream,
    ) -> Self:
        """Configure a pre-built construction cash-flow stream for the asset.

        Parameters
        ----------
        stream : CashFlowStream
            Fully specified construction cash-flow stream.

        Returns
        -------
        EnergyProject
            New project with updated construction configuration.
        """
        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        return self._with_asset(
            asset,
            dc_replace(asset_config, construction=_ConstructionStreamConfig(stream=stream)),
        )

    def construction_financing(
        self,
        *,
        debt_fraction: float,
        amortization_rate: float,
        amortization_term: int,
        construction_interest_rate: float | None = None,
        interest_treatment: InterestTreatment = "capitalize",
        servicing_period: Period | None = None,
        amortization_frequency: Period = "year",
        amortization_start: date | None = None,
    ) -> Self:
        """Configure construction-period debt and its operations-period amortization.

        Specifies both how construction costs are financed (debt fraction,
        construction-period interest) and how the resulting debt principal is
        repaid during operations (amortization rate, term, frequency). The
        permanent-debt principal is derived automatically from construction
        draws plus any capitalized interest.

        Parameters
        ----------
        debt_fraction : float
            Fraction of construction cost funded by debt (0–1).
        amortization_rate : float
            Annual interest rate on the permanent debt during operations.
        amortization_term : int
            Loan term in periods for the permanent debt.
        construction_interest_rate : float, optional
            Annual interest rate on construction-period debt draws. When
            omitted, no interest accrues during construction.
        interest_treatment : InterestTreatment, optional
            Whether accrued construction interest is ``"capitalize"``d into the
            permanent-debt principal or ``"pay"``d in cash. Default is
            ``"capitalize"``.
        servicing_period : Period, optional
            Period frequency for construction-period interest accrual.
        amortization_frequency : Period, optional
            Payment frequency for permanent debt. Default is ``"year"``.
        amortization_start : date, optional
            First amortization payment date. Defaults to ``operations_start``.

        Returns
        -------
        EnergyProject
            New project with updated construction debt configuration.

        Raises
        ------
        ValueError
            If a construction stream override is already configured.
        """
        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        if isinstance(asset_config.construction, _ConstructionStreamConfig):
            raise ValueError(
                "construction_debt cannot be configured when a construction "
                "stream override is provided"
            )
        _validate_finite(amortization_rate, "amortization_rate")
        updated = _ConstructionDebtConfig(
            debt_fraction=debt_fraction,
            construction_interest_rate=construction_interest_rate,
            interest_treatment=interest_treatment,
            servicing_period=servicing_period,
            amortization_rate=amortization_rate,
            amortization_term=amortization_term,
            amortization_frequency=amortization_frequency,
            amortization_start=amortization_start,
        )
        return self._with_asset(
            asset,
            dc_replace(asset_config, construction_debt=updated),
        )

    def debt_schedule(
        self,
        *,
        schedule: AmortizationSchedule | CashFlowStream,
    ) -> Self:
        """Configure a pre-built debt schedule for the asset.

        Parameters
        ----------
        schedule : AmortizationSchedule or CashFlowStream
            Fully specified debt-service schedule.

        Returns
        -------
        EnergyProject
            New project with updated debt configuration.
        """
        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        return self._with_asset(
            asset, dc_replace(asset_config, debt_schedule=_DebtScheduleConfig(schedule))
        )

    def tax(self, *, rate: float, allow_refund: bool = False) -> Self:
        """Configure the project tax rate used for tax cash flows.

        Parameters
        ----------
        rate : float
            Project tax rate expressed as a decimal fraction.
        allow_refund : bool, optional
            When ``False`` (default), only positive taxable income generates a
            tax liability; losses produce zero tax. When ``True``, negative
            taxable income generates a positive cash flow (tax refund), enabling
            symmetric treatment for delta-to-baseline and levelized cost analyses.

        Returns
        -------
        EnergyProject
            New project with updated tax-rate configuration.

        Raises
        ------
        ValueError
            If ``rate`` is not finite.
        """
        _validate_finite(rate, "tax rate")
        return self._copy(
            config=dc_replace(self._config, tax_rate=rate, tax_allow_refund=allow_refund)
        )

    def depreciation_macrs(
        self,
        *,
        property_class: MACRSPropertyClass,
        convention: MACRSConvention = "half-year",
        label: str | None = None,
    ) -> Self:
        """Configure MACRS depreciation for the asset.

        Parameters
        ----------
        property_class : MACRSPropertyClass
            IRS property class (e.g. ``"5-year"``).
        convention : MACRSConvention, optional
            MACRS convention. Default is ``"half-year"``.
        label : str, optional
            Label template. Use ``{n}`` as a period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with MACRS depreciation configured.
        """
        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        return self._with_asset(
            asset,
            dc_replace(
                asset_config,
                depreciation=_MacrsDepreciationConfig(
                    property_class=property_class,
                    convention=convention,
                    label="MACRS Depreciation" if label is None else label,
                ),
            ),
        )

    def depreciation_vdb(
        self,
        *,
        life: int,
        salvage_value: float = 0.0,
        frequency: Period = "year",
        factor: float = 2.0,
        switch_to_straight_line: bool = True,
        convention: VDBConvention = "none",
        schedule_dates: tuple[date, ...] | None = None,
        valuation_rate: float | None = None,
        valuation_date: date | None = None,
        terminal_catch_up: bool = False,
        label: str | None = None,
    ) -> Self:
        """Configure VDB depreciation for the asset.

        Parameters
        ----------
        life : int
            Asset life in periods.
        salvage_value : float, optional
            Salvage value. Default is ``0.0``.
        frequency : Period, optional
            Depreciation period frequency. Default is ``"year"``.
        factor : float, optional
            Declining-balance factor. Default is ``2.0``.
        switch_to_straight_line : bool, optional
            Switch to straight-line when optimal. Default is ``True``.
        convention : VDBConvention, optional
            Half-period convention. Default is ``"none"``.
        schedule_dates : tuple of date, optional
            Explicit period dates.
        valuation_rate : float, optional
            Discount rate for present-value election.
        valuation_date : date, optional
            Valuation date for present-value election.
        terminal_catch_up : bool, optional
            Accumulate remaining basis in the final period. Default is ``False``.
        label : str, optional
            Label template. Use ``{n}`` as a period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with VDB depreciation configured.
        """
        asset = self._config.default_asset
        asset_config = self._asset_config(asset)
        return self._with_asset(
            asset,
            dc_replace(
                asset_config,
                depreciation=_VdbDepreciationConfig(
                    life=life,
                    salvage_value=salvage_value,
                    frequency=frequency,
                    factor=factor,
                    switch_to_straight_line=switch_to_straight_line,
                    convention=convention,
                    schedule_dates=schedule_dates,
                    valuation_rate=valuation_rate,
                    valuation_date=valuation_date,
                    terminal_catch_up=terminal_catch_up,
                    label="VDB Depreciation" if label is None else label,
                ),
            ),
        )

    def investment_tax_credit(self, *, rate: float) -> Self:
        """Configure an Investment Tax Credit (ITC) for the asset.

        Parameters
        ----------
        rate : float
            ITC rate as a decimal (e.g. ``0.30`` for 30 %).

        Returns
        -------
        EnergyProject
            New project with ITC configured.
        """
        asset = self._config.default_asset
        _validate_finite(rate, "itc rate")
        asset_config = self._asset_config(asset)
        return self._with_asset(asset, dc_replace(asset_config, itc_rate=rate))

    def production_tax_credit(
        self,
        *,
        rate_per_unit: float,
        years: int = 10,
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure a Production Tax Credit (PTC) for the asset.

        Parameters
        ----------
        rate_per_unit : float
            Credit per MWh of generation.
        years : int, optional
            Number of years the credit applies from operations start.
        escalation : float, optional
            Annual escalation rate for the credit. When omitted, falls back to
            the project-wide rate set by :meth:`default_escalation`.

        Advanced
        --------
        escalation_period : Period, optional
            Period over which the escalation rate applies. Default is ``"year"``.
        amount_reference_date : date, optional
            Date at which *rate_per_unit* is stated.
        escalation_policy : EscalationPolicy, optional
            Fully configured escalation policy.
        label : str, optional
            Label template. Use ``{n}`` as a period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with PTC configured.

        Raises
        ------
        ValueError
            If *years* is not positive.
        """
        asset = self._config.default_asset
        if years <= 0:
            raise ValueError("PTC years must be positive")
        _validate_finite(rate_per_unit, "ptc rate_per_unit")
        asset_config = self._asset_config(asset)
        updated = _PtcConfig(
            rate_per_unit=rate_per_unit,
            years=years,
            label="PTC" if label is None else label,
            escalation=_updated_escalation(
                _EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        return self._with_asset(asset, dc_replace(asset_config, ptc=updated))

    def add_cashflow_stream(self, *, name: str, stream: CashFlowStream) -> Self:
        """Add a custom named cash-flow component to the project.

        Parameters
        ----------
        name : str
            Component name to use in the compiled analysis and pro forma.
        stream : CashFlowStream
            Cash-flow stream to inject as an additional component.

        Returns
        -------
        EnergyProject
            New project with the custom component registered.
        """
        custom = dict(self._config.custom_cashflows)
        custom[name] = stream
        return self._copy(config=dc_replace(self._config, custom_cashflows=custom))

    def modify_cashflow_components(self, *, modifier: CashFlowComponentModifier) -> Self:
        """Register a component-level cashflow modifier.

        The modifier receives the compiled pre-tax cashflow component map and may
        add, remove, or rewrite named components. The modified components are
        then used to recompute taxable income, tax liability, metrics, and the
        project pro forma.

        Parameters
        ----------
        modifier : CashFlowComponentModifier
            Callable that receives the compiled component group and returns
            either a replacement :class:`CashFlowGroup` or a mapping of names
            to :class:`CashFlowStream` objects.

        Returns
        -------
        EnergyProject
            New project with ``modifier`` appended to the modifier pipeline.

        Raises
        ------
        TypeError
            Raised later during :meth:`analyze` if the modifier returns any
            non-:class:`CashFlowStream` values.
        """
        modifiers = (*self._config.cashflow_modifiers, modifier)
        return self._copy(config=dc_replace(self._config, cashflow_modifiers=modifiers))

    def analyze(self) -> ProjectAnalysis:
        """Compile all configuration into a :class:`ProjectAnalysis`.

        Builds generation, revenue, fixed and variable OPEX, construction,
        ITC, PTC, depreciation, and debt service streams for every configured
        asset. Applies any registered cashflow modifiers, then computes taxable
        income and tax liability.

        Returns
        -------
        ProjectAnalysis
            Compiled project analysis containing all cash-flow components,
            generation streams, and tax calculations.

        Raises
        ------
        ValueError
            If required timeline dates are missing, asset generation
            configuration is incomplete, or debt configuration is invalid.
        """
        # Assemble the internal timeline from generation/construction configs.
        # This populates operations_start, operations_end, construction_start
        # so downstream _build_* methods can access them via _require_timeline_date.
        resolved = self._resolve_timeline()
        original_config = self._config
        self._config = dc_replace(self._config, timeline=resolved)

        try:
            return self._analyze_impl()
        finally:
            self._config = original_config

    def _analyze_impl(self) -> ProjectAnalysis:
        """Internal analysis implementation called after timeline resolution."""
        generation = GenerationStream()
        component_streams: dict[str, CashFlowStream] = {}
        levelized_revenue_basis: dict[str, CashFlowStream] = {}

        for asset_name, asset_config in self._config.assets.items():
            generation = self._build_generation(asset_name, asset_config)

            construction_stream = self._build_construction(asset_config)
            if construction_stream.entries:
                component_streams[f"{asset_name}:construction"] = construction_stream

            revenue_stream = self._build_revenue(generation)
            if revenue_stream.entries:
                component_streams[f"{asset_name}:revenue"] = revenue_stream
            revenue_basis_stream = self._build_revenue(generation, price_per_mwh=1.0)
            if revenue_basis_stream.entries:
                levelized_revenue_basis[f"{asset_name}:revenue"] = revenue_basis_stream

            for cost_name, cost_config in asset_config.fixed_opex_items.items():
                stream = self._build_fixed_opex(asset_name, cost_config)
                if stream.entries:
                    key = (
                        f"{asset_name}:fixed_opex"
                        if cost_name == "default"
                        else f"{asset_name}:fixed_opex:{cost_name}"
                    )
                    component_streams[key] = stream

            for vc_name, vc_config in asset_config.variable_cost_items.items():
                stream = self._build_variable_cost(vc_config, generation)
                if stream.entries:
                    key = (
                        f"{asset_name}:variable_cost"
                        if vc_name == "default"
                        else f"{asset_name}:variable_cost:{vc_name}"
                    )
                    component_streams[key] = stream

            for outage_name, outage_config in asset_config.construction_outages.items():
                stream = self._build_construction_outage(outage_config)
                if stream.entries:
                    key = (
                        f"{asset_name}:construction_outage"
                        if outage_name == "default"
                        else f"{asset_name}:construction_outage:{outage_name}"
                    )
                    component_streams[key] = stream

            itc_stream = self._build_itc(asset_config, construction_stream)
            if itc_stream.entries:
                component_streams[f"{asset_name}:itc"] = itc_stream

            ptc_stream = self._build_ptc(asset_config, generation)
            if ptc_stream.entries:
                component_streams[f"{asset_name}:ptc"] = ptc_stream

            depreciation_stream = self._build_depreciation(asset_config, construction_stream)
            if depreciation_stream.entries:
                component_streams[f"{asset_name}:depreciation"] = depreciation_stream

            debt_stream = self._build_debt(asset_config, construction_stream)
            if debt_stream.entries:
                component_streams[f"{asset_name}:debt_service"] = debt_stream

        for name, stream in self._config.custom_cashflows.items():
            component_streams[name] = stream

        component_streams = self._apply_cashflow_modifiers(component_streams)

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
                tax_rate=self._config.tax_rate,
                allow_refund=self._config.tax_allow_refund,
            )
            if self._config.tax_rate is not None
            else CashFlowStream()
        )
        if taxes.entries:
            component_streams["project:tax_liability"] = taxes

        return ProjectAnalysis(
            timeline=self._config.timeline,
            valuation=self._config.valuation,
            generation=generation,
            cashflow_components=CashFlowGroup(component_streams),
            taxable_income=taxable_income,
            taxes=taxes,
            tax_rate=self._config.tax_rate,
            levelized_revenue_basis=CashFlowGroup(levelized_revenue_basis),
            levelized_cost_escalation_rate=self._infer_levelized_cost_escalation_rate(),
        )

    def cashflows(self) -> CashFlowStream:
        """Return all project cash flows as a single sorted stream.

        Convenience method equivalent to ``self.analyze().cashflows``.

        Returns
        -------
        CashFlowStream
            All cash flows sorted by date.
        """
        return self.analyze().cashflows

    def components(self) -> CashFlowGroup[str]:
        """Return all named cash-flow components.

        Convenience method equivalent to ``self.analyze().cashflow_components``.

        Returns
        -------
        CashFlowGroup[str]
            Named components including generation, revenue, costs, and taxes.
        """
        return self.analyze().cashflow_components

    def metrics(
        self,
        discount_rate: float | None = None,
        valuation_date: date | None = None,
        convention: DayCountConvention = "actual/365",
        levelized_cost_escalation_rate: float | None = None,
        levelized_cost_escalation_policy: EscalationPolicy | None = None,
    ) -> ProjectMetrics:
        """Compile and return project metrics.

        Convenience method equivalent to ``self.analyze().metrics(...)``.

        Parameters
        ----------
        discount_rate : float, optional
            Rate used for NPV calculations.
        valuation_date : date, optional
            Reference date for discounting.
        convention : DayCountConvention, optional
            Day count convention. Default is ``"actual/365"``.
        levelized_cost_escalation_rate : float, optional
            Annual escalation rate assumed for the levelized price stream.
            When omitted, DCAF uses an inferred project-level rate when one
            can be resolved.
        levelized_cost_escalation_policy : EscalationPolicy, optional
            Explicit escalation policy for the levelized price stream.

        Returns
        -------
        ProjectMetrics
            NPV, XIRR, total cash, generation totals, and LCOE.
        """
        return self.analyze().metrics(
            discount_rate=discount_rate,
            valuation_date=valuation_date,
            convention=convention,
            levelized_cost_escalation_rate=levelized_cost_escalation_rate,
            levelized_cost_escalation_policy=levelized_cost_escalation_policy,
        )

    def pro_forma(self, *, period: Period = "year") -> ProjectProForma:
        """Compile and return a period-aggregated pro-forma table.

        Convenience method equivalent to ``self.analyze().pro_forma(period)``.

        Parameters
        ----------
        period : Period, optional
            Aggregation frequency. Default is ``"year"``.

        Returns
        -------
        ProjectProForma
            Pro-forma with component rows and summary rows.
        """
        return self.analyze().pro_forma(period=period)

    def _resolve_timeline(self) -> ProjectTimeline:
        """Assemble an internal timeline from generation and construction configs.

        Operations dates come from the generation config (capacity-based or
        stream-based). Construction start comes from the construction config
        when a spend profile is specified. Frequency and timing come from the
        project-level constructor defaults.
        """
        operations_start: date | None = None
        operations_end: date | None = None
        construction_start: date | None = None

        for asset_config in self._config.assets.values():
            gen = asset_config.generation
            if isinstance(gen, _CapacityGenerationConfig):
                if gen.operations_start is not None:
                    operations_start = gen.operations_start
                if gen.operations_end is not None:
                    operations_end = gen.operations_end
            elif isinstance(gen, _GenerationStreamConfig):
                if gen.stream.entries:
                    dates = [entry.date for entry in gen.stream.entries]
                    operations_start = min(dates)
                    # operations_end is exclusive; the latest entry date is
                    # within the operating window, so the boundary is the
                    # following day.
                    operations_end = max(dates) + relativedelta(days=1)

            con = asset_config.construction
            if isinstance(con, _ConstructionScheduleConfig):
                if con.construction_start is not None:
                    construction_start = con.construction_start

        return ProjectTimeline(
            construction_start=construction_start,
            operations_start=operations_start,
            operations_end=operations_end,
            frequency=self._config.frequency,
            timing=self._config.timing,
        )

    def _asset_config(self, asset: str) -> _AssetConfig:
        """Return the current configuration for *asset*, or a default if unset."""
        return self._config.assets.get(asset, _AssetConfig())

    def _with_asset(self, asset: str, config: _AssetConfig) -> Self:
        """Return a new project with *asset*'s configuration replaced by *config*."""
        assets = dict(self._config.assets)
        assets[asset] = config
        return self._copy(config=dc_replace(self._config, assets=assets))

    def _build_generation(self, asset_name: str, asset_config: _AssetConfig) -> GenerationStream:
        """Build the generation stream for *asset_name* from its configuration.

        Returns an empty stream when generation is unconfigured.
        """
        generation = asset_config.generation
        if generation is None:
            if asset_config.generation_outages:
                raise ValueError(
                    f"generation_outage requires generation to be configured for asset "
                    f"{asset_name!r}"
                )
            return GenerationStream()
        if isinstance(generation, _GenerationStreamConfig):
            base_generation = generation.stream
        else:
            frequency = (
                generation.frequency
                if generation.frequency is not None
                else self._config.timeline.frequency
            )
            start = (
                generation.start
                if generation.start is not None
                else self._require_timeline_date("operations_start")
            )
            timing = generation.timing or self._config.timeline.timing
            ops_start = self._config.timeline.operations_start
            ops_end = self._config.timeline.operations_end
            schedule = self._operating_schedule(
                asset_name,
                "generation",
                start=start,
                periods=generation.periods,
                frequency=frequency,
                timing=timing,
                phase_start=ops_start,
                phase_end=ops_end,
            )
            entries: list[Generation] = []
            hours = hours_per_period(frequency)
            source = asset_name if generation.source is None else generation.source
            for index, modeled_period in enumerate(schedule, start=1):
                label = format_label(generation.label, index)
                entries.append(
                    Generation(
                        amount_mwh=(
                            generation.capacity_mw
                            * generation.capacity_factor
                            * hours
                            * modeled_period.fraction
                        ),
                        date=modeled_period.event_date,
                        source=source,
                        carrier=generation.carrier,
                        label=label,
                    )
                )
            base_generation = GenerationStream(entries)

        outage_generation = self._build_generation_outages(asset_name, asset_config)
        if not outage_generation.entries:
            return base_generation
        return GenerationStream.from_streams(base_generation, outage_generation).sort()

    def _build_generation_outages(
        self,
        asset_name: str,
        asset_config: _AssetConfig,
    ) -> GenerationStream:
        """Build negative generation entries for configured modeled outages."""
        if not asset_config.generation_outages:
            return GenerationStream()

        generation = asset_config.generation
        capacity_defaults: _CapacityGenerationConfig | None = (
            generation if isinstance(generation, _CapacityGenerationConfig) else None
        )
        ops_start = self._config.timeline.operations_start
        ops_end = self._config.timeline.operations_end

        outage_streams: list[GenerationStream] = []
        for outage in asset_config.generation_outages:
            if ops_start is not None and outage.start < ops_start:
                raise ValueError(
                    f"generation_outage {outage.name!r} for asset {asset_name!r} "
                    "starts before operations_start"
                )
            if ops_end is not None and outage.end > ops_end:
                raise ValueError(
                    f"generation_outage {outage.name!r} for asset {asset_name!r} "
                    "ends after operations_end"
                )

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

            source = outage.source
            carrier = outage.carrier
            timing = outage.timing
            if capacity_defaults is not None:
                source = source if source is not None else capacity_defaults.source or asset_name
                carrier = carrier if carrier is not None else capacity_defaults.carrier
                timing = timing or capacity_defaults.timing

            outage_streams.append(
                GenerationStream.from_outage(
                    capacity_mw=capacity_mw,
                    capacity_factor=capacity_factor,
                    start=outage.start,
                    end=outage.end,
                    capacity_reduction=outage.capacity_reduction,
                    timing=timing or self._config.timeline.timing,
                    source="" if source is None else source,
                    carrier="electricity" if carrier is None else carrier,
                    label=outage.label,
                )
            )
        return GenerationStream.from_streams(*outage_streams)

    def _build_construction_outage(
        self,
        outage: _ConstructionOutageConfig,
    ) -> CashFlowStream:
        """Build operating-cost cashflows for a construction outage on baseline generation."""
        if outage.sell_price_per_unit is None:
            market = self._config.markets.get(outage.carrier)
            if market is None:
                raise ValueError(
                    f"construction_outage {outage.name!r} requires sell_price_per_unit "
                    f"or a configured market for carrier {outage.carrier!r}"
                )
            price_per_mwh = market.sell_price_per_unit
            escalation = _effective_escalation(market.escalation, self._config.default_escalation)
        else:
            price_per_mwh = outage.sell_price_per_unit
            escalation = _effective_escalation(outage.escalation, self._config.default_escalation)

        return _construction_outage_helper(
            capacity_mw=outage.capacity_mw,
            capacity_factor=outage.capacity_factor,
            start=outage.start,
            end=outage.end,
            sell_price_per_unit=price_per_mwh,
            capacity_reduction=outage.capacity_reduction,
            fixed_cost=outage.fixed_cost,
            cost_per_day=outage.cost_per_day,
            timing=outage.timing or self._config.timeline.timing,
            source=outage.source,
            carrier=outage.carrier,
            lost_revenue_label=outage.lost_revenue_label,
            fixed_cost_label=outage.fixed_cost_label,
            daily_cost_label=outage.daily_cost_label,
            pro_forma_category=ProFormaCategory.OPERATING_COST,
            tax_treatment=TaxTreatment.DEDUCTIBLE,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
            escalation_policy=escalation.policy,
        )

    def _build_revenue(
        self,
        generation: GenerationStream,
        *,
        price_per_mwh: float | None = None,
    ) -> CashFlowStream:
        """Build the revenue stream for *asset_name* from generation and market config.

        Returns an empty stream when generation is empty or no market price is set.
        """
        if not generation.entries:
            return CashFlowStream()
        revenue_streams: list[CashFlowStream] = []
        for carrier, carrier_generation in generation.group_by(carrier=True).items():
            market = self._config.markets.get(carrier)
            if market is None:
                continue
            resolved_price_per_mwh = (
                market.sell_price_per_unit if price_per_mwh is None else price_per_mwh
            )
            escalation = _effective_escalation(market.escalation, self._config.default_escalation)
            if escalation.policy is not None:
                revenue_streams.append(
                    carrier_generation.to_revenue(
                        price_per_mwh=resolved_price_per_mwh,
                        label=market.label,
                        escalation_policy=escalation.policy,
                    )
                )
                continue
            revenue_streams.append(
                carrier_generation.to_revenue(
                    price_per_mwh=resolved_price_per_mwh,
                    label=market.label,
                    escalation=escalation.escalation,
                    escalation_period=escalation.escalation_period,
                    amount_reference_date=escalation.amount_reference_date,
                )
            )
        if not revenue_streams:
            return CashFlowStream()
        return CashFlowStream.from_streams(*revenue_streams).sort()

    def _build_fixed_opex(self, asset_name: str, fixed: _RecurringCostConfig) -> CashFlowStream:
        """Build a fixed OPEX cash-flow stream from *fixed* configuration.

        Each period's amount is scaled by the escalation factor and the
        partial-period fraction.
        """
        frequency = (
            fixed.frequency if fixed.frequency is not None else self._config.timeline.frequency
        )
        start = (
            fixed.start
            if fixed.start is not None
            else self._require_timeline_date("operations_start")
        )
        timing = fixed.timing or self._config.timeline.timing
        ops_start = self._config.timeline.operations_start
        ops_end = self._config.timeline.operations_end
        schedule = self._operating_schedule(
            asset_name,
            "fixed_opex",
            start=start,
            periods=fixed.periods,
            frequency=frequency,
            timing=timing,
            phase_start=ops_start,
            phase_end=ops_end,
        )
        escalation = _effective_escalation(fixed.escalation, self._config.default_escalation)
        escalation_policy = _recurring_policy(start, escalation)
        entries: list[CashFlow] = []
        for index, modeled_period in enumerate(schedule, start=1):
            label = format_label(fixed.label, index)
            entries.append(
                CashFlow(
                    amount=(
                        -abs(fixed.amount)
                        * escalation_policy.factor(modeled_period.event_date)
                        * modeled_period.fraction
                    ),
                    date=modeled_period.event_date,
                    label=label,
                    is_cash=True,
                    pro_forma_category=ProFormaCategory.OPERATING_COST,
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                )
            )
        return CashFlowStream(entries)

    def _build_variable_cost(
        self,
        variable: _VariableCostConfig,
        generation: GenerationStream,
    ) -> CashFlowStream:
        """Build a variable cost stream by applying *variable* rate to generation.

        Returns an empty stream when generation is unavailable.
        """
        if not generation.entries:
            return CashFlowStream()
        escalation = _effective_escalation(variable.escalation, self._config.default_escalation)
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
        )

    def _build_construction(self, asset_config: _AssetConfig) -> CashFlowStream:
        """Build the construction spend stream from asset construction configuration.

        Returns the pre-built stream directly when a stream override is provided.
        When no spend profile is given, books the overnight cost as a single
        cash flow on the COD date. Otherwise distributes the cost over the
        construction period using the spend schedule.
        """
        construction = asset_config.construction
        if construction is None:
            return CashFlowStream()
        if isinstance(construction, _ConstructionStreamConfig):
            if asset_config.construction_debt is not None:
                raise ValueError(
                    "construction stream overrides cannot be combined with construction debt"
                )
            return construction.stream

        # Resolve COD date: explicit > operations_start
        cod = (
            construction.cod_date
            if construction.cod_date is not None
            else self._require_timeline_date("operations_start")
        )

        # Overnight-only path: no spend profile, book as single cash flow at COD
        if construction.spend_profile is None:
            return CashFlowStream(
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

        # Spend-profile path: distribute cost over construction period
        start = (
            construction.construction_start
            if construction.construction_start is not None
            else self._require_timeline_date("construction_start")
        )
        end = construction.construction_end if construction.construction_end is not None else cod

        # Convert _ConstructionDebtConfig to ConstructionFinancing for the
        # lower-level construction_spend_schedule function.
        financing = self._construction_financing(asset_config.construction_debt)

        escalation = _effective_escalation(construction.escalation, self._config.default_escalation)
        if escalation.policy is not None:
            return construction_spend_schedule(
                total_cost=construction.overnight_cost,
                start_date=start,
                end_date=end,
                period=construction.period,
                profile=construction.spend_profile,
                financing=financing,
                escalation_policy=escalation.policy,
            )
        return construction_spend_schedule(
            total_cost=construction.overnight_cost,
            start_date=start,
            end_date=end,
            period=construction.period,
            profile=construction.spend_profile,
            financing=financing,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
        )

    @staticmethod
    def _construction_financing(
        debt_config: _ConstructionDebtConfig | None,
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

    def _build_itc(
        self,
        asset_config: _AssetConfig,
        construction_stream: CashFlowStream,
    ) -> CashFlowStream:
        """Build an ITC cash-flow stream from capital-cost construction flows.

        Returns an empty stream when no ITC rate is configured or construction
        has no capital-cost entries.
        """
        if asset_config.itc_rate is None or not construction_stream.entries:
            return CashFlowStream()
        capex_basis = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
        if not capex_basis.entries:
            return CashFlowStream()
        return itc(
            capex_stream=capex_basis,
            rate=asset_config.itc_rate,
            placed_in_service=self._require_timeline_date("operations_start"),
        )

    def _build_ptc(
        self,
        asset_config: _AssetConfig,
        generation: GenerationStream,
    ) -> CashFlowStream:
        """Build a PTC cash-flow stream from generation and PTC configuration.

        Returns an empty stream when no PTC is configured or generation is empty.
        """
        if asset_config.ptc is None or not generation.entries:
            return CashFlowStream()
        escalation = _effective_escalation(
            asset_config.ptc.escalation, self._config.default_escalation
        )
        if escalation.policy is not None:
            return ptc(
                generation_stream=generation,
                rate_per_mwh=asset_config.ptc.rate_per_unit,
                years=asset_config.ptc.years,
                label=asset_config.ptc.label,
                escalation_policy=escalation.policy,
            )
        return ptc(
            generation_stream=generation,
            rate_per_mwh=asset_config.ptc.rate_per_unit,
            years=asset_config.ptc.years,
            label=asset_config.ptc.label,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
        )

    def _remap_event_dates(
        self,
        stream: CashFlowStream,
        frequency: Period,
        phase_start: date | None,
        phase_end: date | None,
    ) -> CashFlowStream:
        """Remap cashflow dates according to the project timing convention.

        Applies the timeline's timing convention to each cashflow in *stream*,
        replacing each date with the computed event date. ``phase_end`` is the
        exclusive end of the phase and is converted to the inclusive last
        allowable date for :func:`event_date`.
        """
        timing = self._config.timeline.timing
        phase_end_inclusive = phase_end - relativedelta(days=1) if phase_end is not None else None
        return stream.apply(
            lambda cf: dc_replace(
                cf,
                date=event_date(cf.date, frequency, timing, phase_start, phase_end_inclusive),
            )
        )

    def _build_depreciation(
        self,
        asset_config: _AssetConfig,
        construction_stream: CashFlowStream,
    ) -> CashFlowStream:
        """Build a depreciation stream from construction capital costs and depreciation config.

        Applies ITC basis adjustment when an ITC rate is configured. Returns an
        empty stream when depreciation is unconfigured or the cost basis is zero.
        """
        if asset_config.depreciation is None or not construction_stream.entries:
            return CashFlowStream()
        capex_basis = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
        if not capex_basis.entries:
            return CashFlowStream()
        basis = (
            itc_adjusted_basis(capex_basis, asset_config.itc_rate)
            if asset_config.itc_rate is not None
            else abs(capex_basis.sum())
        )
        if basis == 0.0:
            return CashFlowStream()
        placed = self._require_timeline_date("operations_start")
        ops_start = self._config.timeline.operations_start
        ops_end = self._config.timeline.operations_end
        match asset_config.depreciation:
            case _MacrsDepreciationConfig() as config:
                return self._remap_event_dates(
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
                )
            case _VdbDepreciationConfig() as config:
                return self._remap_event_dates(
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
                )
            case _:
                raise AssertionError("Unexpected depreciation config")

    def _build_debt(
        self,
        asset_config: _AssetConfig,
        construction_stream: CashFlowStream,
    ) -> CashFlowStream:
        """Build the debt service stream from construction debt or schedule config.

        Handles two paths: construction-debt-based amortization (principal
        derived from construction draws) and explicit schedule overrides.
        Returns an empty stream when no debt is configured.
        """
        # Explicit schedule override takes precedence
        if asset_config.debt_schedule is not None:
            sched = asset_config.debt_schedule
            if isinstance(sched.schedule, AmortizationSchedule):
                return CashFlowStream.from_streams(
                    sched.schedule.interest,
                    sched.schedule.principal,
                ).sort()
            return sched.schedule

        # Construction-debt path
        debt = asset_config.construction_debt
        if debt is None:
            return CashFlowStream()
        if debt.amortization_term <= 0:
            raise ValueError("amortization_term must be positive")

        # Derive principal from construction draws
        if not construction_stream.entries:
            raise ValueError(
                "construction_debt requires a construction schedule to derive "
                "the debt principal — call construction() first"
            )
        capex = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
        debt_draws = abs(capex.cash_only().sum()) * debt.debt_fraction
        capitalized_interest = abs(capex.filter(is_cash=False).sum())
        principal = debt_draws + capitalized_interest

        start = (
            debt.amortization_start
            if debt.amortization_start is not None
            else self._require_timeline_date("operations_start")
        )
        schedule = AmortizationSchedule.build(
            principal=principal,
            annual_rate=debt.amortization_rate,
            term=debt.amortization_term,
            start_date=start,
            frequency=debt.amortization_frequency,
        )
        ops_start = self._config.timeline.operations_start
        ops_end = self._config.timeline.operations_end
        return self._remap_event_dates(
            CashFlowStream.from_streams(schedule.interest, schedule.principal).sort(),
            frequency=debt.amortization_frequency,
            phase_start=ops_start,
            phase_end=ops_end,
        )

    def _apply_cashflow_modifiers(
        self,
        component_streams: dict[str, CashFlowStream],
    ) -> dict[str, CashFlowStream]:
        """Apply all registered cashflow modifiers sequentially to *component_streams*.

        Each modifier receives a ``CashFlowGroup`` and must return either a
        ``CashFlowGroup`` or a ``Mapping[str, CashFlowStream]``.
        """
        updated = dict(component_streams)
        for modifier in self._config.cashflow_modifiers:
            modified = modifier(CashFlowGroup(updated))
            updated = (
                dict(modified.items()) if isinstance(modified, CashFlowGroup) else dict(modified)
            )
            for name, stream in updated.items():
                if not isinstance(stream, CashFlowStream):
                    raise TypeError(
                        "cashflow modifiers must return CashFlowStream values for every component"
                    )
        return updated

    def _infer_levelized_cost_escalation_rate(self) -> float | None:
        """Infer a shared annual escalation rate for constant-dollar LCOE when possible."""

        inferred_rates: list[float] = []

        def collect(local: _EscalationSettings) -> bool:
            effective = _effective_escalation(local, self._config.default_escalation)
            rate = _constant_annual_escalation_rate(effective)
            if rate is None:
                return False
            inferred_rates.append(rate)
            return True

        for asset_config in self._config.assets.values():
            if (
                isinstance(asset_config.construction, _ConstructionScheduleConfig)
                and asset_config.construction.overnight_cost != 0.0
            ):
                if not collect(asset_config.construction.escalation):
                    return None
            for recurring_cost in asset_config.fixed_opex_items.values():
                if recurring_cost.amount != 0.0:
                    if not collect(recurring_cost.escalation):
                        return None
            if asset_config.ptc is not None and asset_config.ptc.rate_per_unit != 0.0:
                if not collect(asset_config.ptc.escalation):
                    return None

        for market in self._config.markets.values():
            if market.sell_price_per_unit != 0.0:
                if not collect(market.escalation):
                    return None

        if not inferred_rates:
            return 0.0

        first_rate = inferred_rates[0]
        if any(
            not isclose(rate, first_rate, rel_tol=0.0, abs_tol=1e-12) for rate in inferred_rates[1:]
        ):
            return None
        return first_rate

    def _operating_schedule(
        self,
        asset_name: str,
        section: str,
        *,
        start: date,
        periods: int | None,
        frequency: Period,
        timing: TimingConvention = "end",
        phase_start: date | None = None,
        phase_end: date | None = None,
    ) -> tuple[_ScheduledPeriod, ...]:
        """Build the sequence of modeled operating periods for an asset and section.

        When *periods* is specified, generates exactly that many full-period
        entries. Otherwise infers the schedule from ``timeline.operations_end``,
        prorating any trailing partial period using :func:`elapsed_periods`.

        *timing*, *phase_start*, and *phase_end* (all exclusive ends) control
        event-date placement. ``phase_end`` is converted to the inclusive
        last-allowable date when forwarded to :func:`event_date`.
        """
        phase_end_inclusive = phase_end - relativedelta(days=1) if phase_end is not None else None

        if periods is not None:
            if periods <= 0:
                raise ValueError(f"{section} periods must be positive for asset '{asset_name}'")
            delta = time_delta_per_period(frequency)
            current = start
            schedule: list[_ScheduledPeriod] = []
            for _ in range(periods):
                schedule.append(
                    _ScheduledPeriod(
                        start=current,
                        event_date=event_date(
                            current, frequency, timing, phase_start, phase_end_inclusive
                        ),
                    )
                )
                current += delta
            return tuple(schedule)

        exclusive_end = self._require_timeline_date("operations_end")
        if exclusive_end <= start:
            raise ValueError(
                f"timeline.operations_end must be after the {section} start "
                f"for asset '{asset_name}'"
            )

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
                _ScheduledPeriod(
                    start=current,
                    event_date=event_date(
                        current, frequency, timing, phase_start, effective_phase_end
                    ),
                    fraction=elapsed_periods(current, window_end, frequency),
                )
            )
            current += delta
        return tuple(schedule)

    def _require_timeline_date(
        self,
        field_name: Literal["construction_start", "operations_start", "operations_end"],
    ) -> date:
        """Return the named timeline date or raise ``ValueError`` if it is not set."""
        value = getattr(self._config.timeline, field_name)
        if value is None:
            raise ValueError(f"timeline.{field_name} is required for this project configuration")
        return value


def _updated_escalation(
    existing: _EscalationSettings,
    *,
    escalation: float | None,
    escalation_period: Period | None,
    amount_reference_date: date | None,
    escalation_policy: EscalationPolicy | None,
) -> _EscalationSettings:
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
        return _EscalationSettings(policy=escalation_policy, explicit=True)

    return _EscalationSettings(
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


def _recurring_policy(start: date, escalation: _EscalationSettings) -> EscalationPolicy:
    """Resolve recurring-cost escalation settings into a concrete policy."""
    if escalation.policy is not None:
        return escalation.policy
    return ConstantRateEscalation(
        reference_date=start
        if escalation.amount_reference_date is None
        else escalation.amount_reference_date,
        rate=escalation.escalation,
        period=escalation.escalation_period,
    )
