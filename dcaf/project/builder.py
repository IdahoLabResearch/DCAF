"""High-level project builder APIs for composing DCAF analyses.

This module provides an order-independent ``EnergyProject`` builder that wraps
the lower-level DCAF primitives into a more configuration-oriented workflow.
The builder stays immutable: each configuration method returns a new project.

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
from dcaf.project.config import CapitalStructure
from dcaf.project.timeline import ProjectTimeline
from dcaf.finance.amortization import AmortizationSchedule
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
    VDBConvention,
)
from dcaf.shared.formatting import format_label
from dcaf.shared.time import elapsed_periods, hours_per_period, time_delta_per_period

type MarketKey = tuple[str | None, str]
type CashFlowComponentModifier = Callable[
    [CashFlowGroup[str]],
    CashFlowGroup[str] | Mapping[str, CashFlowStream],
]


def _validate_finite(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is not finite (inf or NaN)."""
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


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


@dataclass(frozen=True)
class _ScheduledPeriod:
    """One modeled operating period with an optional partial-period fraction."""

    start: date
    fraction: float = 1.0


@dataclass(frozen=True)
class _GenerationConfig:
    """Configuration for capacity-based generation inputs on a single asset."""

    stream: GenerationStream | None = None
    capacity_mw: float | None = None
    capacity_factor: float | None = None
    start: date | None = None
    periods: int | None = None
    frequency: Period | None = None
    carrier: str = "electricity"
    source: str | None = None
    label: str = "Generation"


@dataclass(frozen=True)
class _RecurringCostConfig:
    """Configuration for a recurring (fixed) operating cost item."""

    amount: float | None = None
    start: date | None = None
    periods: int | None = None
    frequency: Period | None = None
    label: str = "Fixed OPEX"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _VariableCostConfig:
    """Configuration for a per-unit variable cost item."""

    rate_per_unit: float | None = None
    label: str = "Variable Cost"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _ConstructionConfig:
    """Configuration for construction spend schedule inputs on a single asset."""

    stream: CashFlowStream | None = None
    overnight_cost: float | None = None
    spend_profile: SpendProfile | SpendScheduleName = "flat"
    period: Period = "month"
    start: date | None = None
    end: date | None = None
    financing: ConstructionFinancing = field(default_factory=ConstructionFinancing)
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _DebtConfig:
    """Configuration for permanent debt financing on a single asset."""

    schedule: AmortizationSchedule | CashFlowStream | None = None
    annual_rate: float | None = None
    term: int | None = None
    frequency: Period = "year"
    start: date | None = None
    principal: float | None = None


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

    generation: _GenerationConfig = field(default_factory=_GenerationConfig)
    fixed_opex_items: dict[str, _RecurringCostConfig] = field(default_factory=dict)
    variable_cost_items: dict[str, _VariableCostConfig] = field(default_factory=dict)
    construction: _ConstructionConfig = field(default_factory=_ConstructionConfig)
    debt: _DebtConfig = field(default_factory=_DebtConfig)
    depreciation: _DepreciationConfig = None
    itc_rate: float | None = None
    ptc: _PtcConfig | None = None


@dataclass(frozen=True)
class _MarketConfig:
    """Market price and escalation configuration for one energy carrier."""

    sell_price_per_unit: float | None = None
    unit: str | None = None
    label: str = "Market Revenue"
    escalation: _EscalationSettings = field(default_factory=_EscalationSettings)


@dataclass(frozen=True)
class _ProjectConfig:
    """Top-level internal configuration bag for an ``EnergyProject``."""

    name: str = ""
    timeline: ProjectTimeline = field(default_factory=ProjectTimeline)
    assets: dict[str, _AssetConfig] = field(default_factory=dict)
    markets: dict[MarketKey, _MarketConfig] = field(default_factory=dict)
    tax_rate: float | None = None
    capital_structure: CapitalStructure | None = None
    default_escalation: _EscalationSettings = field(default_factory=_EscalationSettings)
    custom_cashflows: dict[str, CashFlowStream] = field(default_factory=dict)
    cashflow_modifiers: tuple[CashFlowComponentModifier, ...] = ()


class EnergyProject:
    """Immutable fluent builder for composing and analyzing energy project cash flows.

    Each configuration method returns a new ``EnergyProject`` instance, leaving
    the original unchanged. The builder supports a single implicit asset named
    ``"default"`` for single-asset projects; multi-asset projects pass explicit
    ``asset`` names to each method.

    Call :meth:`analyze` to compile all configured inputs into a
    :class:`ProjectAnalysis`, or use the convenience methods
    :meth:`cashflows`, :meth:`summary`, and :meth:`pro_forma` to skip the
    intermediate result.

    Examples
    --------
    >>> from datetime import date
    >>> analysis = (
    ...     EnergyProject("My Plant")
    ...     .timeline(
    ...         construction_start=date(2025, 1, 1),
    ...         operations_start=date(2026, 1, 1),
    ...         operating_years=20,
    ...     )
    ...     .generation(capacity_mw=100.0, capacity_factor=0.35)
    ...     .market(sell_price_per_unit=50.0)
    ...     .construction(overnight_cost=200_000_000)
    ...     .tax(rate=0.21)
    ...     .analyze()
    ... )
    >>> metrics = analysis.metrics(discount_rate=0.08)
    """

    def __init__(self, name: str = "") -> None:
        """Initialize a new immutable project builder.

        Parameters
        ----------
        name : str, optional
            Project name carried into the compiled :class:`ProjectAnalysis`.
            Default is an empty string.
        """
        self._config = _ProjectConfig(name=name)

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
    def name(self) -> str:
        """Return the configured project name.

        Returns
        -------
        str
            Project name stored on the builder.
        """
        return self._config.name

    @property
    def timeline_config(self) -> ProjectTimeline:
        """Return the current timeline configuration.

        Returns
        -------
        ProjectTimeline
            Timeline assumptions currently attached to the builder.
        """
        return self._config.timeline

    @property
    def capital_structure_config(self) -> CapitalStructure | None:
        """Return the current capital structure configuration.

        Returns
        -------
        CapitalStructure or None
            Current capital structure, or ``None`` when none has been
            configured.
        """
        return self._config.capital_structure

    def timeline(
        self,
        *,
        construction_start: date | None = None,
        operations_start: date | None = None,
        operations_end: date | None = None,
        frequency: Period | None = None,
        cod: date | None = None,
        operating_years: int | None = None,
    ) -> Self:
        """Configure the project timeline.

        Parameters
        ----------
        construction_start : date, optional
            Date on which construction begins.
        operations_start : date, optional
            Date on which operations begin. This is the preferred replacement
            for the older ``cod`` parameter.
        operations_end : date, optional
            End boundary for operations. When recurring operating inputs do not
            specify explicit period counts, the builder infers the modeled
            schedule from ``operations_start`` up to this date and prorates any
            trailing partial period.
        frequency : Period, optional
            Default operating frequency for recurring items.
        cod : date, optional
            Backward-compatible alias for ``operations_start``.
        operating_years : int, optional
            Backward-compatible helper that derives ``operations_end`` from the
            effective operations start date.

        Returns
        -------
        EnergyProject
            New project with updated timeline assumptions.

        Raises
        ------
        ValueError
            If ``cod`` and ``operations_start`` disagree, ``operating_years``
            is not positive, ``operating_years`` is provided without an
            operations start date, or ``operations_end`` and
            ``operating_years`` describe different operating windows.
        """
        timeline = self._config.timeline
        resolved_operations_start = operations_start
        if cod is not None:
            if resolved_operations_start is not None and cod != resolved_operations_start:
                raise ValueError("cod and operations_start must match when both are provided")
            resolved_operations_start = cod
        effective_operations_start = (
            timeline.operations_start
            if resolved_operations_start is None
            else resolved_operations_start
        )
        resolved_operations_end = operations_end
        if operating_years is not None:
            if operating_years <= 0:
                raise ValueError("operating_years must be positive")
            if effective_operations_start is None:
                raise ValueError(
                    "operations_start or cod is required when operating_years is provided"
                )
            derived_operations_end = (
                effective_operations_start
                + relativedelta(years=operating_years)
                - relativedelta(days=1)
            )
            if (
                resolved_operations_end is not None
                and resolved_operations_end != derived_operations_end
            ):
                raise ValueError(
                    "operations_end and operating_years must describe the same operating window"
                )
            resolved_operations_end = derived_operations_end
        updated = dc_replace(
            timeline,
            construction_start=timeline.construction_start
            if construction_start is None
            else construction_start,
            operations_start=effective_operations_start,
            operations_end=timeline.operations_end
            if resolved_operations_end is None
            else resolved_operations_end,
            frequency=timeline.frequency if frequency is None else frequency,
        )
        return self._copy(config=dc_replace(self._config, timeline=updated))

    def capital_structure(
        self,
        *,
        debt_fraction: float,
        cost_of_debt: float,
        equity_fraction: float,
        cost_of_equity: float,
        tax_rate: float | None = None,
    ) -> Self:
        """Configure the project's capital structure.

        Parameters
        ----------
        debt_fraction : float
            Debt share of total capital.
        cost_of_debt : float
            Cost of debt.
        equity_fraction : float
            Equity share of total capital.
        cost_of_equity : float
            Cost of equity.
        tax_rate : float, optional
            Tax rate used for WACC. If omitted, the project-level tax rate
            (from ``.tax()``) is used at analysis time.

        Returns
        -------
        EnergyProject
            New project with updated capital structure assumptions.

        Raises
        ------
        ValueError
            If any rate or fraction is non-finite, if either capital fraction
            is negative, or if the debt and equity fractions do not sum to
            ``1.0``.
        """
        return self._copy(
            config=dc_replace(
                self._config,
                capital_structure=CapitalStructure(
                    debt_fraction=debt_fraction,
                    cost_of_debt=cost_of_debt,
                    equity_fraction=equity_fraction,
                    cost_of_equity=cost_of_equity,
                    tax_rate=tax_rate,
                ),
            )
        )

    def default_escalation(
        self,
        value: float | EscalationPolicy,
        *,
        escalation_period: Period = "year",
        amount_reference_date: date | None = None,
    ) -> Self:
        """Set the project-wide default escalation applied when no item-level escalation is configured.

        Parameters
        ----------
        value : float or EscalationPolicy
            Annual escalation rate, or a fully configured :class:`EscalationPolicy`.
        escalation_period : Period, optional
            Period over which the rate applies. Default is ``"year"``.
        amount_reference_date : date, optional
            Date at which the base amount is stated. Ignored when *value* is an
            ``EscalationPolicy``.

        Returns
        -------
        EnergyProject
            New project with updated default escalation.
        """
        if isinstance(value, (int, float)):
            settings = _EscalationSettings(
                escalation=float(value),
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                explicit=True,
            )
        else:
            settings = _EscalationSettings(policy=value, explicit=True)
        return self._copy(config=dc_replace(self._config, default_escalation=settings))

    def generation(
        self,
        asset: str = "default",
        *,
        stream: GenerationStream | None = None,
        capacity_mw: float | None = None,
        capacity_factor: float | None = None,
        start: date | None = None,
        periods: int | None = None,
        frequency: Period | None = None,
        carrier: str | None = None,
        source: str | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure capacity-based generation for an asset.

        Either supply a pre-built *stream* or provide *capacity_mw* and
        *capacity_factor* along with scheduling parameters. The two modes
        cannot be combined.

        Parameters
        ----------
        asset : str, optional
            Asset name. Default is ``"default"``.
        stream : GenerationStream, optional
            Pre-built generation stream. Overrides all capacity-based inputs.
        capacity_mw : float, optional
            Nameplate capacity in megawatts.
        capacity_factor : float, optional
            Fraction of full capacity realized on average (0–1).
        start : date, optional
            First period start date. Defaults to ``timeline.operations_start``.
        periods : int, optional
            Number of periods. Inferred from ``timeline.operations_end`` when omitted.
        frequency : Period, optional
            Generation period frequency. Defaults to ``timeline.frequency``.
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

        Raises
        ------
        ValueError
            If *stream* is combined with any capacity-based keyword argument.
        """
        simple_args = (
            capacity_mw,
            capacity_factor,
            start,
            periods,
            frequency,
            carrier,
            source,
            label,
        )
        if stream is not None and any(arg is not None for arg in simple_args):
            raise ValueError(
                "generation stream override cannot be combined with capacity-based inputs"
            )
        asset_config = self._asset_config(asset)
        if stream is not None:
            updated_generation = _GenerationConfig(stream=stream)
        else:
            base = (
                asset_config.generation
                if asset_config.generation.stream is None
                else _GenerationConfig()
            )
            updated_generation = dc_replace(
                base,
                capacity_mw=base.capacity_mw if capacity_mw is None else capacity_mw,
                capacity_factor=base.capacity_factor
                if capacity_factor is None
                else capacity_factor,
                start=base.start if start is None else start,
                periods=base.periods if periods is None else periods,
                frequency=base.frequency if frequency is None else frequency,
                carrier=base.carrier if carrier is None else carrier,
                source=base.source if source is None else source,
                label=base.label if label is None else label,
            )
        return self._with_asset(asset, dc_replace(asset_config, generation=updated_generation))

    def market(
        self,
        carrier: str = "electricity",
        *,
        asset: str | None = None,
        sell_price_per_unit: float | None = None,
        unit: str | None = None,
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure the market price for an energy carrier.

        Parameters
        ----------
        carrier : str, optional
            Energy carrier key (e.g. ``"electricity"``). Default is ``"electricity"``.
        asset : str, optional
            Asset name for asset-specific market overrides. When omitted the
            market applies to all assets with the matching carrier.
        sell_price_per_unit : float, optional
            Price per MWh at the amount reference date.
        unit : str, optional
            Unit label for display purposes.
        escalation : float, optional
            Annual price escalation rate.
        escalation_period : Period, optional
            Period over which the escalation rate applies.
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
            New project with updated market configuration.
        """
        key = (asset, carrier)
        existing = self._config.markets.get(key, _MarketConfig())
        if sell_price_per_unit is not None:
            _validate_finite(sell_price_per_unit, "sell_price_per_unit")
        updated = dc_replace(
            existing,
            sell_price_per_unit=existing.sell_price_per_unit
            if sell_price_per_unit is None
            else sell_price_per_unit,
            unit=existing.unit if unit is None else unit,
            label=existing.label if label is None else label,
            escalation=_updated_escalation(
                existing.escalation,
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        markets = dict(self._config.markets)
        markets[key] = updated
        return self._copy(config=dc_replace(self._config, markets=markets))

    def fixed_opex(
        self,
        asset: str = "default",
        *,
        name: str = "default",
        amount: float | None = None,
        start: date | None = None,
        periods: int | None = None,
        frequency: Period | None = None,
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure a named fixed operating cost item for an asset.

        Parameters
        ----------
        asset : str, optional
            Asset name. Default is ``"default"``.
        name : str, optional
            Item name allowing multiple independent fixed-cost streams per asset.
            Default is ``"default"``.
        amount : float, optional
            Cost amount per period (sign is ignored; applied as an outflow).
        start : date, optional
            First period start date. Defaults to ``timeline.operations_start``.
        periods : int, optional
            Number of periods. Inferred from ``timeline.operations_end`` when omitted.
        frequency : Period, optional
            Cost period frequency. Defaults to ``timeline.frequency``.
        escalation : float, optional
            Annual cost escalation rate.
        escalation_period : Period, optional
            Period over which the escalation rate applies.
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
        if amount is not None:
            _validate_finite(amount, "fixed opex amount")
        asset_config = self._asset_config(asset)
        existing = asset_config.fixed_opex_items.get(name, _RecurringCostConfig())
        updated = dc_replace(
            existing,
            amount=existing.amount if amount is None else amount,
            start=existing.start if start is None else start,
            periods=existing.periods if periods is None else periods,
            frequency=existing.frequency if frequency is None else frequency,
            label=existing.label if label is None else label,
            escalation=_updated_escalation(
                existing.escalation,
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        items = dict(asset_config.fixed_opex_items)
        items[name] = updated
        return self._with_asset(asset, dc_replace(asset_config, fixed_opex_items=items))

    def annual_opex_cost(
        self,
        amount: float,
        *,
        asset: str = "default",
        name: str = "default",
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Add a yearly fixed operating cost; shorthand for :meth:`fixed_opex` with ``frequency="year"``.

        Parameters
        ----------
        amount : float
            Annual cost amount (sign is ignored; applied as an outflow).
        asset : str, optional
            Asset name. Default is ``"default"``.
        name : str, optional
            Item name. Default is ``"default"``.
        escalation : float, optional
            Annual escalation rate.
        escalation_period : Period, optional
            Period over which the escalation rate applies.
        amount_reference_date : date, optional
            Date at which *amount* is stated.
        escalation_policy : EscalationPolicy, optional
            Fully configured escalation policy.
        label : str, optional
            Label template. Use ``{n}`` as a period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with updated annual OPEX configuration.
        """
        return self.fixed_opex(
            asset=asset,
            name=name,
            amount=amount,
            frequency="year",
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            escalation_policy=escalation_policy,
            label=label,
        )

    def variable_cost(
        self,
        rate_per_unit: float,
        *,
        asset: str = "default",
        name: str = "default",
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure a per-MWh variable operating cost for an asset.

        Parameters
        ----------
        rate_per_unit : float
            Cost per MWh (sign is ignored; applied as an outflow).
        asset : str, optional
            Asset name. Default is ``"default"``.
        name : str, optional
            Item name. Default is ``"default"``.
        escalation : float, optional
            Annual escalation rate.
        escalation_period : Period, optional
            Period over which the escalation rate applies.
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
        _validate_finite(rate_per_unit, "variable cost rate_per_unit")
        asset_config = self._asset_config(asset)
        existing = asset_config.variable_cost_items.get(name, _VariableCostConfig())
        updated = dc_replace(
            existing,
            rate_per_unit=rate_per_unit,
            label=existing.label if label is None else label,
            escalation=_updated_escalation(
                existing.escalation,
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        items = dict(asset_config.variable_cost_items)
        items[name] = updated
        return self._with_asset(asset, dc_replace(asset_config, variable_cost_items=items))

    def operations(
        self,
        asset: str = "default",
        *,
        fixed_opex_per_year: float | None = None,
        variable_cost_per_unit: float | None = None,
    ) -> Self:
        """Convenience method for setting fixed and variable operating costs together.

        Parameters
        ----------
        asset : str, optional
            Asset name. Default is ``"default"``.
        fixed_opex_per_year : float, optional
            Annual fixed operating cost amount.
        variable_cost_per_unit : float, optional
            Variable cost per MWh.

        Returns
        -------
        EnergyProject
            New project with updated operations configuration.
        """
        project = self
        if fixed_opex_per_year is not None:
            project = project.annual_opex_cost(fixed_opex_per_year, asset=asset)
        if variable_cost_per_unit is not None:
            project = project.variable_cost(variable_cost_per_unit, asset=asset)
        return project

    def construction(
        self,
        asset: str = "default",
        *,
        stream: CashFlowStream | None = None,
        overnight_cost: float | None = None,
        total_cost: float | None = None,
        spend_profile: SpendProfile | SpendScheduleName | None = None,
        period: Period | None = None,
        start: date | None = None,
        end: date | None = None,
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
    ) -> Self:
        """Configure the construction spend schedule for an asset.

        Either supply a pre-built *stream* or provide *overnight_cost* along
        with scheduling parameters. The two modes cannot be combined.

        Parameters
        ----------
        asset : str, optional
            Asset name. Default is ``"default"``.
        stream : CashFlowStream, optional
            Pre-built construction cash-flow stream.
        overnight_cost : float, optional
            Total overnight capital cost (excluding financing costs).
        total_cost : float, optional
            Alias for *overnight_cost*. Must match if both are provided.
        spend_profile : SpendProfile or SpendScheduleName, optional
            Spend curve shape. Default is ``"flat"``.
        period : Period, optional
            Construction sub-period frequency. Default is ``"month"``.
        start : date, optional
            Construction start date. Defaults to ``timeline.construction_start``.
        end : date, optional
            Construction end date (exclusive). Defaults to ``timeline.operations_start``.
        escalation : float, optional
            Annual cost escalation rate during construction.
        escalation_period : Period, optional
            Period over which the escalation rate applies.
        amount_reference_date : date, optional
            Date at which *overnight_cost* is stated.
        escalation_policy : EscalationPolicy, optional
            Fully configured escalation policy.

        Returns
        -------
        EnergyProject
            New project with updated construction configuration.

        Raises
        ------
        ValueError
            If *stream* is combined with any schedule-based keyword argument,
            or if *overnight_cost* and *total_cost* differ.
        """
        if overnight_cost is not None and total_cost is not None and overnight_cost != total_cost:
            raise ValueError("overnight_cost and total_cost must match when both are provided")
        if overnight_cost is not None:
            _validate_finite(overnight_cost, "overnight_cost")
        if total_cost is not None:
            _validate_finite(total_cost, "total_cost")
        simple_args = (
            overnight_cost,
            total_cost,
            spend_profile,
            period,
            start,
            end,
            escalation,
            escalation_period,
            amount_reference_date,
            escalation_policy,
        )
        if stream is not None and any(arg is not None for arg in simple_args):
            raise ValueError("construction stream override cannot be combined with schedule inputs")
        asset_config = self._asset_config(asset)
        if stream is not None:
            updated = _ConstructionConfig(stream=stream)
        else:
            base = (
                asset_config.construction
                if asset_config.construction.stream is None
                else _ConstructionConfig()
            )
            updated = dc_replace(
                base,
                overnight_cost=base.overnight_cost
                if overnight_cost is None and total_cost is None
                else (overnight_cost if overnight_cost is not None else total_cost),
                spend_profile=base.spend_profile if spend_profile is None else spend_profile,
                period=base.period if period is None else period,
                start=base.start if start is None else start,
                end=base.end if end is None else end,
                escalation=_updated_escalation(
                    base.escalation,
                    escalation=escalation,
                    escalation_period=escalation_period,
                    amount_reference_date=amount_reference_date,
                    escalation_policy=escalation_policy,
                ),
            )
            updated = dc_replace(updated, financing=base.financing)
        return self._with_asset(asset, dc_replace(asset_config, construction=updated))

    def construction_financing(
        self,
        asset: str = "default",
        *,
        debt_fraction: float | None = None,
        interest_rate: float | None = None,
        interest_treatment: InterestTreatment | None = None,
        servicing_period: Period | None = None,
    ) -> Self:
        """Configure construction-period debt financing for an asset.

        Parameters
        ----------
        asset : str, optional
            Asset name. Default is ``"default"``.
        debt_fraction : float, optional
            Fraction of construction cost funded by debt.
        interest_rate : float, optional
            Annual interest rate on construction debt.
        interest_treatment : InterestTreatment, optional
            Whether accrued interest is ``"capitalize"``d into the basis or
            ``"pay"``d in cash.
        servicing_period : Period, optional
            Period frequency for interest accrual.

        Returns
        -------
        EnergyProject
            New project with updated construction financing configuration.

        Raises
        ------
        ValueError
            If a construction stream override is already configured.
        """
        asset_config = self._asset_config(asset)
        if asset_config.construction.stream is not None:
            raise ValueError(
                "construction_financing cannot be configured when a construction "
                "stream override is provided"
            )
        existing = asset_config.construction.financing
        updated_financing = ConstructionFinancing(
            debt_fraction=existing.debt_fraction if debt_fraction is None else debt_fraction,
            interest_rate=existing.interest_rate if interest_rate is None else interest_rate,
            interest_treatment=existing.interest_treatment
            if interest_treatment is None
            else interest_treatment,
            servicing_period=existing.servicing_period
            if servicing_period is None
            else servicing_period,
        )
        updated_construction = dc_replace(asset_config.construction, financing=updated_financing)
        return self._with_asset(asset, dc_replace(asset_config, construction=updated_construction))

    def debt(
        self,
        asset: str = "default",
        *,
        schedule: AmortizationSchedule | CashFlowStream | None = None,
        annual_rate: float | None = None,
        term: int | None = None,
        frequency: Period | None = None,
        start: date | None = None,
        principal: float | None = None,
    ) -> Self:
        """Configure permanent debt service for an asset.

        Either supply a pre-built *schedule* or provide *annual_rate* and *term*.
        The two modes cannot be combined.

        Parameters
        ----------
        asset : str, optional
            Asset name. Default is ``"default"``.
        schedule : AmortizationSchedule or CashFlowStream, optional
            Pre-built debt schedule. Overrides all simple debt inputs.
        annual_rate : float, optional
            Annual interest rate.
        term : int, optional
            Loan term in periods.
        frequency : Period, optional
            Payment frequency. Default is ``"year"``.
        start : date, optional
            First payment date. Defaults to ``timeline.operations_start``.
        principal : float, optional
            Loan principal. Derived from construction costs and capital
            structure when omitted.

        Returns
        -------
        EnergyProject
            New project with updated debt configuration.

        Raises
        ------
        ValueError
            If *schedule* is combined with simple debt inputs, or if
            *annual_rate* or *term* is missing when not using a schedule.
        """
        simple_args = (annual_rate, term, frequency, start, principal)
        if schedule is not None and any(arg is not None for arg in simple_args):
            raise ValueError("debt schedule override cannot be combined with simple debt inputs")
        asset_config = self._asset_config(asset)
        if schedule is not None:
            updated = _DebtConfig(schedule=schedule)
        else:
            base = asset_config.debt if asset_config.debt.schedule is None else _DebtConfig()
            updated = dc_replace(
                base,
                annual_rate=base.annual_rate if annual_rate is None else annual_rate,
                term=base.term if term is None else term,
                frequency=base.frequency if frequency is None else frequency,
                start=base.start if start is None else start,
                principal=base.principal if principal is None else principal,
            )
        return self._with_asset(asset, dc_replace(asset_config, debt=updated))

    def tax(self, *, rate: float) -> Self:
        """Configure the project tax rate used for taxes and default WACC resolution.

        The project tax rate is used for tax liability computation and as the
        default WACC tax component when the capital structure does not specify
        its own ``tax_rate``. Resolution is deferred to ``analyze()`` time so
        call order does not matter.

        Parameters
        ----------
        rate : float
            Project tax rate expressed as a decimal fraction.

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
        return self._copy(config=dc_replace(self._config, tax_rate=rate))

    def depreciation(
        self,
        asset: str = "default",
        *,
        method: Literal["macrs", "vdb"],
        property_class: MACRSPropertyClass | None = None,
        convention: MACRSConvention = "half-year",
        life: int | None = None,
        salvage_value: float = 0.0,
        frequency: Period = "year",
        factor: float = 2.0,
        switch_to_straight_line: bool = True,
        vdb_convention: VDBConvention = "none",
        schedule_dates: tuple[date, ...] | None = None,
        valuation_rate: float | None = None,
        valuation_date: date | None = None,
        terminal_catch_up: bool = False,
        label: str | None = None,
    ) -> Self:
        """Configure depreciation for an asset using MACRS or VDB method.

        Parameters
        ----------
        asset : str, optional
            Asset name. Default is ``"default"``.
        method : {"macrs", "vdb"}
            Depreciation method.
        property_class : MACRSPropertyClass, optional
            Required for ``method="macrs"``. IRS property class (e.g. ``"5-year"``).
        convention : MACRSConvention, optional
            MACRS convention. Default is ``"half-year"``.
        life : int, optional
            Required for ``method="vdb"``. Asset life in periods.
        salvage_value : float, optional
            Salvage value for VDB. Default is ``0.0``.
        frequency : Period, optional
            VDB depreciation period frequency. Default is ``"year"``.
        factor : float, optional
            VDB declining-balance factor. Default is ``2.0`` (200% DB).
        switch_to_straight_line : bool, optional
            Switch to straight-line when it yields a higher deduction.
            Default is ``True``.
        vdb_convention : VDBConvention, optional
            VDB half-period convention. Default is ``"none"``.
        schedule_dates : tuple of date, optional
            Explicit period dates for VDB.
        valuation_rate : float, optional
            Discount rate for VDB present-value election.
        valuation_date : date, optional
            Valuation date for VDB present-value election.
        terminal_catch_up : bool, optional
            Accumulate remaining basis in the final period. Default is ``False``.
        label : str, optional
            Label template for individual depreciation entries.

        Returns
        -------
        EnergyProject
            New project with updated depreciation configuration.

        Raises
        ------
        ValueError
            If *property_class* is missing for MACRS, or *life* is missing for VDB.
        """
        if method == "macrs":
            if property_class is None:
                raise ValueError("property_class is required for MACRS depreciation")
            config: _DepreciationConfig = _MacrsDepreciationConfig(
                property_class=property_class,
                convention=convention,
                label="MACRS Depreciation" if label is None else label,
            )
        else:
            if life is None:
                raise ValueError("life is required for VDB depreciation")
            config = _VdbDepreciationConfig(
                life=life,
                salvage_value=salvage_value,
                frequency=frequency,
                factor=factor,
                switch_to_straight_line=switch_to_straight_line,
                convention=vdb_convention,
                schedule_dates=schedule_dates,
                valuation_rate=valuation_rate,
                valuation_date=valuation_date,
                terminal_catch_up=terminal_catch_up,
                label="VDB Depreciation" if label is None else label,
            )
        asset_config = self._asset_config(asset)
        return self._with_asset(asset, dc_replace(asset_config, depreciation=config))

    def macrs_depreciation(
        self,
        property_class: MACRSPropertyClass,
        *,
        asset: str = "default",
        convention: MACRSConvention = "half-year",
        label: str | None = None,
    ) -> Self:
        """Convenience wrapper for :meth:`depreciation` with ``method="macrs"``.

        Parameters
        ----------
        property_class : MACRSPropertyClass
            IRS property class (e.g. ``"5-year"``).
        asset : str, optional
            Asset name. Default is ``"default"``.
        convention : MACRSConvention, optional
            MACRS convention. Default is ``"half-year"``.
        label : str, optional
            Label template. Use ``{n}`` as a period index placeholder if desired.

        Returns
        -------
        EnergyProject
            New project with MACRS depreciation configured.
        """
        return self.depreciation(
            asset=asset,
            method="macrs",
            property_class=property_class,
            convention=convention,
            label=label,
        )

    def vdb_depreciation(
        self,
        *,
        life: int,
        asset: str = "default",
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
        """Convenience wrapper for :meth:`depreciation` with ``method="vdb"``.

        Parameters
        ----------
        life : int
            Asset life in periods.
        asset : str, optional
            Asset name. Default is ``"default"``.
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
        return self.depreciation(
            asset=asset,
            method="vdb",
            life=life,
            salvage_value=salvage_value,
            frequency=frequency,
            factor=factor,
            switch_to_straight_line=switch_to_straight_line,
            vdb_convention=convention,
            schedule_dates=schedule_dates,
            valuation_rate=valuation_rate,
            valuation_date=valuation_date,
            terminal_catch_up=terminal_catch_up,
            label=label,
        )

    def itc(self, rate: float, *, asset: str = "default") -> Self:
        """Configure an Investment Tax Credit (ITC) for an asset.

        Parameters
        ----------
        rate : float
            ITC rate as a decimal (e.g. ``0.30`` for 30 %).
        asset : str, optional
            Asset name. Default is ``"default"``.

        Returns
        -------
        EnergyProject
            New project with ITC configured.
        """
        _validate_finite(rate, "itc rate")
        asset_config = self._asset_config(asset)
        return self._with_asset(asset, dc_replace(asset_config, itc_rate=rate))

    def ptc(
        self,
        *,
        rate_per_unit: float,
        years: int,
        asset: str = "default",
        escalation: float | None = None,
        escalation_period: Period | None = None,
        amount_reference_date: date | None = None,
        escalation_policy: EscalationPolicy | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure a Production Tax Credit (PTC) for an asset.

        Parameters
        ----------
        rate_per_unit : float
            Credit per MWh of generation.
        years : int
            Number of years the credit applies from operations start.
        asset : str, optional
            Asset name. Default is ``"default"``.
        escalation : float, optional
            Annual escalation rate for the credit.
        escalation_period : Period, optional
            Period over which the escalation rate applies.
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
        if years <= 0:
            raise ValueError("PTC years must be positive")
        _validate_finite(rate_per_unit, "ptc rate_per_unit")
        asset_config = self._asset_config(asset)
        updated = _PtcConfig(
            rate_per_unit=rate_per_unit,
            years=years,
            label="PTC" if label is None else label,
            escalation=_updated_escalation(
                asset_config.ptc.escalation
                if asset_config.ptc is not None
                else _EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        return self._with_asset(asset, dc_replace(asset_config, ptc=updated))

    def add_cashflow_stream(self, name: str, stream: CashFlowStream) -> Self:
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

    def modify_cashflow_components(self, modifier: CashFlowComponentModifier) -> Self:
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
        generation_by_asset: dict[str, GenerationStream] = {}
        component_streams: dict[str, CashFlowStream] = {}

        for asset_name, asset_config in self._config.assets.items():
            generation = self._build_generation(asset_name, asset_config)
            generation_by_asset[asset_name] = generation

            construction_stream = self._build_construction(asset_config)
            if construction_stream.entries:
                component_streams[f"{asset_name}:construction"] = construction_stream

            revenue_stream = self._build_revenue(asset_name, asset_config, generation)
            if revenue_stream.entries:
                component_streams[f"{asset_name}:revenue"] = revenue_stream

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
            tax_liability(taxable_income, tax_rate=self._config.tax_rate)
            if self._config.tax_rate is not None
            else CashFlowStream()
        )
        if taxes.entries:
            component_streams["project:tax_liability"] = taxes

        return ProjectAnalysis(
            name=self._config.name,
            timeline=self._config.timeline,
            capital_structure=self._resolved_capital_structure(),
            generation_by_asset=generation_by_asset,
            cashflow_components=CashFlowGroup(component_streams),
            taxable_income=taxable_income,
            taxes=taxes,
            tax_rate=self._config.tax_rate,
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

    def summary(
        self,
        discount_rate: float | None = None,
        valuation_date: date | None = None,
        convention: DayCountConvention = "actual/365",
    ) -> ProjectMetrics:
        """Compile and return summary project metrics.

        Convenience method equivalent to ``self.analyze().summary(...)``.

        Parameters
        ----------
        discount_rate : float, optional
            Rate used for NPV calculations.
        valuation_date : date, optional
            Reference date for discounting.
        convention : DayCountConvention, optional
            Day count convention. Default is ``"actual/365"``.

        Returns
        -------
        ProjectMetrics
            NPV, XIRR, total cash, generation totals, and LCOE.
        """
        return self.analyze().summary(
            discount_rate=discount_rate,
            valuation_date=valuation_date,
            convention=convention,
        )

    def pro_forma(self, period: Period = "year") -> ProjectProForma:
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

        Returns an empty stream when no capacity inputs are configured.
        Raises ``ValueError`` if only one of ``capacity_mw`` / ``capacity_factor`` is set.
        """
        generation = asset_config.generation
        if generation.stream is not None:
            return generation.stream
        if generation.capacity_mw is None and generation.capacity_factor is None:
            return GenerationStream()
        if generation.capacity_mw is None or generation.capacity_factor is None:
            raise ValueError(
                f"Asset '{asset_name}' generation requires both capacity_mw and capacity_factor"
            )
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
        schedule = self._operating_schedule(
            asset_name,
            "generation",
            start=start,
            periods=generation.periods,
            frequency=frequency,
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
                    date=modeled_period.start,
                    source=source,
                    carrier=generation.carrier,
                    label=label,
                )
            )
        return GenerationStream(entries)

    def _build_revenue(
        self,
        asset_name: str,
        asset_config: _AssetConfig,
        generation: GenerationStream,
    ) -> CashFlowStream:
        """Build the revenue stream for *asset_name* from generation and market config.

        Returns an empty stream when generation is empty or no market price is set.
        Asset-specific market overrides take precedence over carrier-level defaults.
        """
        if not generation.entries:
            return CashFlowStream()
        revenue_streams: list[CashFlowStream] = []
        for carrier, carrier_generation in generation.group_by(carrier=True).items():
            market = self._config.markets.get((asset_name, carrier))
            if market is None:
                market = self._config.markets.get((None, carrier))
            if market is None or market.sell_price_per_unit is None:
                continue
            escalation = _effective_escalation(market.escalation, self._config.default_escalation)
            if escalation.policy is not None:
                revenue_streams.append(
                    carrier_generation.to_revenue(
                        price_per_mwh=market.sell_price_per_unit,
                        label=market.label,
                        escalation_policy=escalation.policy,
                    )
                )
                continue
            revenue_streams.append(
                carrier_generation.to_revenue(
                    price_per_mwh=market.sell_price_per_unit,
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

        Returns an empty stream when no amount is configured. Each period's
        amount is scaled by the escalation factor and the partial-period fraction.
        """
        if fixed.amount is None:
            return CashFlowStream()
        frequency = (
            fixed.frequency if fixed.frequency is not None else self._config.timeline.frequency
        )
        start = (
            fixed.start
            if fixed.start is not None
            else self._require_timeline_date("operations_start")
        )
        schedule = self._operating_schedule(
            asset_name,
            "fixed_opex",
            start=start,
            periods=fixed.periods,
            frequency=frequency,
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
                        * escalation_policy.factor(modeled_period.start)
                        * modeled_period.fraction
                    ),
                    date=modeled_period.start,
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

        Returns an empty stream when no rate or generation is available.
        """
        if variable.rate_per_unit is None or not generation.entries:
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
        Otherwise derives the schedule from overnight cost, dates, and financing.
        """
        construction = asset_config.construction
        if construction.stream is not None:
            if construction.financing != ConstructionFinancing():
                raise ValueError(
                    "construction stream overrides cannot be combined with construction financing"
                )
            return construction.stream
        if construction.overnight_cost is None:
            return CashFlowStream()
        start = (
            construction.start
            if construction.start is not None
            else self._require_timeline_date("construction_start")
        )
        end = (
            construction.end
            if construction.end is not None
            else self._require_timeline_date("operations_start")
        )
        escalation = _effective_escalation(construction.escalation, self._config.default_escalation)
        if escalation.policy is not None:
            return construction_spend_schedule(
                total_cost=construction.overnight_cost,
                start_date=start,
                end_date=end,
                period=construction.period,
                profile=construction.spend_profile,
                financing=construction.financing,
                escalation_policy=escalation.policy,
            )
        return construction_spend_schedule(
            total_cost=construction.overnight_cost,
            start_date=start,
            end_date=end,
            period=construction.period,
            profile=construction.spend_profile,
            financing=construction.financing,
            escalation=escalation.escalation,
            escalation_period=escalation.escalation_period,
            amount_reference_date=escalation.amount_reference_date,
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
        match asset_config.depreciation:
            case _MacrsDepreciationConfig() as config:
                return macrs_schedule(
                    cost_basis=basis,
                    placed_in_service=placed,
                    property_class=config.property_class,
                    convention=config.convention,
                    label=config.label,
                )
            case _VdbDepreciationConfig() as config:
                return vdb_schedule(
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
                )
            case _:
                raise AssertionError("Unexpected depreciation config")

    def _build_debt(
        self,
        asset_config: _AssetConfig,
        construction_stream: CashFlowStream,
    ) -> CashFlowStream:
        debt = asset_config.debt
        if debt.schedule is not None:
            if isinstance(debt.schedule, AmortizationSchedule):
                return CashFlowStream.from_streams(
                    debt.schedule.interest,
                    debt.schedule.principal,
                ).sort()
            return debt.schedule
        if debt.annual_rate is None and debt.term is None and debt.principal is None:
            return CashFlowStream()
        if debt.annual_rate is None or debt.term is None:
            raise ValueError(
                "debt requires both annual_rate and term when not using a schedule override"
            )
        if debt.term <= 0:
            raise ValueError("debt term must be positive")
        principal = debt.principal
        if principal is None:
            principal = self._derive_debt_principal(asset_config, construction_stream)
        start = (
            debt.start
            if debt.start is not None
            else self._require_timeline_date("operations_start")
        )
        schedule = AmortizationSchedule.build(
            principal=principal,
            annual_rate=debt.annual_rate,
            term=debt.term,
            start_date=start,
            frequency=debt.frequency,
        )
        return CashFlowStream.from_streams(schedule.interest, schedule.principal).sort()

    def _derive_debt_principal(
        self,
        asset_config: _AssetConfig,
        construction_stream: CashFlowStream,
    ) -> float:
        """Derive the permanent debt principal from construction costs and financing.

        The formula depends on the construction financing ``interest_treatment``:

        - ``"capitalize"``: Interest accrued during construction is added to the
          cost basis and rolled into the permanent debt principal.
          ``principal = cash_capex * debt_fraction + capitalized_interest``
        - ``"pay"``: Interest was paid in cash during construction and does not
          increase the permanent debt principal.
          ``principal = cash_capex * debt_fraction``
        """
        if not construction_stream.entries:
            raise ValueError("debt principal cannot be derived without a construction schedule")
        debt_fraction = asset_config.construction.financing.debt_fraction
        if debt_fraction == 0.0 and self._config.capital_structure is not None:
            debt_fraction = self._config.capital_structure.debt_fraction
        if debt_fraction == 0.0:
            raise ValueError(
                "debt principal cannot be derived without construction_financing debt_fraction "
                "or project capital_structure debt_fraction"
            )

        capex = construction_stream.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
        cash_basis = abs(capex.cash_only().sum())
        principal = cash_basis * debt_fraction

        if asset_config.construction.financing.interest_treatment == "capitalize":
            capitalized_interest = abs(capex.filter(is_cash=False).sum())
            principal += capitalized_interest

        return principal

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

    def _resolved_capital_structure(self) -> CapitalStructure | None:
        """Return the capital structure with the project tax rate filled in when needed.

        When the capital structure has no explicit ``tax_rate`` and the project has
        one, returns a copy with the project ``tax_rate`` injected so WACC can be
        resolved without requiring a separate call.
        """
        capital_structure = self._config.capital_structure
        if capital_structure is None:
            return None
        if capital_structure.tax_rate is not None or self._config.tax_rate is None:
            return capital_structure
        return dc_replace(capital_structure, tax_rate=self._config.tax_rate)

    def _operating_schedule(
        self,
        asset_name: str,
        section: str,
        *,
        start: date,
        periods: int | None,
        frequency: Period,
    ) -> tuple[_ScheduledPeriod, ...]:
        """Build the sequence of modeled operating periods for an asset and section.

        When *periods* is specified, generates exactly that many full-period
        entries. Otherwise infers the schedule from ``timeline.operations_end``,
        prorating any trailing partial period using :func:`elapsed_periods`.
        """
        if periods is not None:
            if periods <= 0:
                raise ValueError(f"{section} periods must be positive for asset '{asset_name}'")
            delta = time_delta_per_period(frequency)
            current = start
            schedule: list[_ScheduledPeriod] = []
            for _ in range(periods):
                schedule.append(_ScheduledPeriod(start=current))
                current += delta
            return tuple(schedule)

        operations_end_inclusive = self._require_timeline_date("operations_end")
        # Convert inclusive end to exclusive boundary for schedule computation.
        exclusive_end = operations_end_inclusive + relativedelta(days=1)
        if exclusive_end <= start:
            raise ValueError(
                f"timeline.operations_end must not be before the {section} start "
                f"for asset '{asset_name}'"
            )

        delta = time_delta_per_period(frequency)
        current = start
        schedule = []
        while current < exclusive_end:
            period_end = min(current + delta, exclusive_end)
            schedule.append(
                _ScheduledPeriod(
                    start=current,
                    fraction=elapsed_periods(current, period_end, frequency),
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
