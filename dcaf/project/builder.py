# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""High-level project builder APIs for composing DCAF analyses.

This module provides an explicit ``EnergyProject`` builder that wraps the
lower-level DCAF primitives into a configuration-oriented workflow. The builder
stays immutable: each configuration method returns a new project.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import date
from typing import Any, Self

from dcaf.finance.amortization import AmortizationSchedule
from dcaf.finance.construction import SpendProfile
from dcaf.finance.escalation import EscalationPolicy
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
    GenerationOutageConfig,
    MacrsDepreciationConfig,
    ProductionTaxCreditConfig,
    ProjectConfig,
    RevenueConfig,
    VariableCostConfig,
    VdbDepreciationConfig,
    updated_escalation,
)
from dcaf.project._compiler import ProjectCompiler
from dcaf.project.analysis import ProjectAnalysis, ProjectMetrics, ProjectProForma
from dcaf.project.config import ProjectValuation, wacc as compute_wacc
from dcaf.project.contracts import GenerationPrice, EnergyContract
from dcaf.project.policies import (
    GenerationLinkedCashFlowPolicy,
    coerce_generation_linked_policy,
)
from dcaf.streams.cashflows import CashFlowGroup, CashFlowStream
from dcaf.streams.generation import GenerationStream
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
    parse_day_count_convention,
)


class EnergyProject:
    """Immutable fluent builder for composing and analyzing energy project cash flows.

    Each configuration method returns a new ``EnergyProject`` instance, leaving
    the original unchanged.

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
    ...     .generation_revenue(price=50.0)
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
        frequency: Period = "year",
        timing: TimingConvention = "end",
        day_count_convention: DayCountConvention = "actual/actual",
    ) -> None:
        """Initialize a new immutable project builder.

        Parameters
        ----------
        frequency : Period, optional
            Default operating frequency for recurring items. Default is
            ``"year"``; change only when modeling sub-annual periods.
        timing : TimingConvention, optional
            Default event-date convention. ``"end"`` (default) books events at
            the end of the calendar period, capped by the phase boundary.
            ``"begin"`` books events at the start of the calendar period,
            floored by the phase start.
        day_count_convention : DayCountConvention, optional
            Project-wide day-count convention. Default ``"actual/actual"``
            uses real calendar days and calendar-year denominators.
        """
        self._config = ProjectConfig(
            frequency=frequency,
            timing=timing,
            day_count_convention=day_count_convention,
        )

    def _with(self, **config_changes: Any) -> Self:
        """Return a new project with changes applied to the current config."""
        project = self.__class__.__new__(self.__class__)
        project._config = dc_replace(self._config, **config_changes)
        return project

    def _has_generation_revenue_policies(self) -> bool:
        """Return whether contract or remainder revenue policies are configured."""
        return any(
            isinstance(
                registration,
                (GenerationRevenueContractConfig, GenerationRevenueRemainderConfig),
            )
            for registration in self._config.generation_linked_policies
        )

    def _require_no_generation_revenue(self, method_name: str) -> None:
        """Reject contract/remainder revenue when whole-project revenue is configured."""
        if self._config.market is not None:
            raise ValueError(f"{method_name} cannot be combined with generation_revenue")

    def _require_available_generation_linked_name(self, name: str) -> None:
        """Reject duplicate generation-linked component names."""
        if any(
            registration.name == name for registration in self._config.generation_linked_policies
        ):
            raise ValueError(f"generation-linked policy name {name!r} is already configured")

    def day_count_convention(self, convention: DayCountConvention) -> Self:
        """Set the project-wide day-count convention."""
        return self._with(
            day_count_convention=parse_day_count_convention(str(convention)).value,
        )

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
        return self._with(valuation=ProjectValuation.from_discount_rate(rate))

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
        return self._with(
            valuation=ProjectValuation.from_discount_rate(
                compute_wacc(
                    debt_fraction=debt_fraction,
                    debt_cost=debt_cost,
                    equity_fraction=equity_fraction,
                    equity_cost=equity_cost,
                    tax_rate=tax_rate,
                )
            ),
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
            settings = EscalationSettings(
                escalation=float(rate),
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                explicit=True,
            )
        else:
            settings = EscalationSettings(policy=rate, explicit=True)
        return self._with(default_escalation=settings)

    def generation(
        self,
        *,
        capacity_mw: float,
        capacity_factor: float = 1.0,
        operations_start: date | None = None,
        operations_end: date | None = None,
        start: date | None = None,
        periods: int | float | None = None,
        label: str | None = None,
    ) -> Self:
        """Configure capacity-based generation for the project.

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
            Date on which the project enters operation. Used as the default start
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
        periods : int or float, optional
            Number of generation periods. Fractional periods include the final
            complete days that fit in the requested period count. If the
            requested end falls within a day, the incomplete day is omitted and
            a warning is raised. Inferred from ``operations_end`` when omitted.
        label : str, optional
            Label applied to every generation entry. Default is ``"Generation"``.

        Returns
        -------
        EnergyProject
            New project with updated generation configuration.
        """
        updated_generation = CapacityGenerationConfig(
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            operations_start=operations_start,
            operations_end=operations_end,
            start=start,
            periods=periods,
            label="Generation" if label is None else label,
        )
        return self._with(generation=updated_generation)

    def generation_stream(
        self,
        *,
        stream: GenerationStream,
    ) -> Self:
        """Configure a pre-built generation stream for the project.

        Operations-period dates are inferred from the minimum physical period
        start and maximum physical period end in the provided stream. Legacy
        point-dated generation is normalized to a one-day physical period by
        :class:`Generation` before project setup uses it.

        Parameters
        ----------
        stream : GenerationStream
            Fully specified generation stream.

        Returns
        -------
        EnergyProject
            New project with updated generation configuration.
        """
        return self._with(generation=stream)

    def generation_outage(
        self,
        *,
        start: date,
        end: date,
        name: str = "default",
        capacity_mw: float | None = None,
        capacity_factor: float | None = None,
        capacity_reduction: float = 1.0,
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
        label : str, optional
            Label for the negative generation entry.

        Returns
        -------
        EnergyProject
            New project with the outage registered.
        """
        outage = GenerationOutageConfig(
            name=name,
            start=start,
            end=end,
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            capacity_reduction=capacity_reduction,
            label="Generation Outage" if label is None else label,
        )
        return self._with(generation_outages=(*self._config.generation_outages, outage))

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
        cashflow series** in the resulting component, so each shows up as a
        separate line item in pro-forma output. Every series is split at the
        project's calendar frequency. Cashflow dates use the explicit outage
        ``timing`` when supplied, otherwise the project timing convention.

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
            Booking-date convention for generated cashflows. When omitted, the
            project timing is used. This explicit outage rule is not replaced
            by the project default.
        sell_price_per_unit : float, optional
            Explicit outage price per MWh. When omitted, a scalar ``price``
            configured with :meth:`generation_revenue` is used with the same
            project-default escalation, or a fixed ``price_policy`` is used
            without escalation. Scheduled and callable generation-revenue
            policies require an explicit outage price.
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

        Returns
        -------
        EnergyProject
            New project with the construction outage registered.
        """
        outage = ConstructionOutageConfig(
            name=name,
            start=start,
            end=end,
            capacity_mw=capacity_mw,
            capacity_factor=capacity_factor,
            capacity_reduction=capacity_reduction,
            timing=timing,
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
            escalation=updated_escalation(
                EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        outages = dict(self._config.construction_outages)
        outages[name] = outage
        return self._with(construction_outages=outages)

    def generation_revenue(
        self,
        *,
        price: float | None = None,
        price_policy: GenerationPrice | None = None,
        label: str | None = None,
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE,
        tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE,
    ) -> Self:
        """Configure the revenue price for project generation.

        Parameters
        ----------
        price : float, optional
            Base per-MWh settlement price for each generation event. The price
            inherits the project-wide rate configured by
            :meth:`default_escalation`. When that default has no
            ``amount_reference_date``, the earliest generation event is the
            reference date. Mutually exclusive with ``price_policy``.
        price_policy : GenerationPrice, optional
            Complete settlement-price policy that does not inherit the project
            default escalation. A scheduled policy must contain an entry whose
            date exactly matches every generation event; schedule entries are
            not carried forward. Mutually exclusive with ``price``.
        label : str, optional
            Label applied to every generated revenue cashflow. Default is
            ``"Revenue"``.
        pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category for the revenue flows. Default is revenue.
        tax_treatment : TaxTreatment or str, optional
            Tax treatment for the revenue flows. Default is taxable.

        Returns
        -------
        EnergyProject
            New project with updated revenue configuration.

        Raises
        ------
        TypeError
            If ``price`` is not a scalar or ``price_policy`` is not a
            ``GenerationPrice``.
        ValueError
            If neither or both price arguments are provided, ``price`` is not
            finite, generation revenue is already configured, or whole-project
            revenue is combined with contract or remainder revenue.
        """
        if (price is None) == (price_policy is None):
            raise ValueError("provide exactly one of price or price_policy")
        if price is not None and (not isinstance(price, int | float) or isinstance(price, bool)):
            raise TypeError("generation_revenue price must be a finite scalar")
        if price_policy is not None and not isinstance(price_policy, GenerationPrice):
            raise TypeError("generation_revenue price_policy must be a GenerationPrice")
        if self._config.market is not None:
            raise ValueError("generation_revenue may only be called once")
        if self._has_generation_revenue_policies():
            raise ValueError(
                "generation_revenue cannot be combined with "
                "generation_revenue_contract or generation_revenue_remainder"
            )
        selected_price: float | GenerationPrice
        if price is not None:
            selected_price = float(price)
        else:
            assert price_policy is not None
            selected_price = price_policy
        updated = RevenueConfig(
            price=selected_price,
            label="Revenue" if label is None else label,
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
        )
        return self._with(market=updated)

    def generation_revenue_contract(
        self,
        *,
        name: str,
        contract: EnergyContract,
    ) -> Self:
        """Register a named generation-linked contract revenue policy.

        Parameters
        ----------
        name : str
            Component name used in the compiled analysis.
        contract : EnergyContract
            Contract terms used to allocate generation and settle revenue.

        Returns
        -------
        EnergyProject
            New project with the contract revenue policy registered.
        """
        self._require_no_generation_revenue("generation_revenue_contract")
        self._require_available_generation_linked_name(name)
        registration = GenerationRevenueContractConfig(name=name, contract=contract)
        return self._with(
            generation_linked_policies=(
                *self._config.generation_linked_policies,
                registration,
            )
        )

    def generation_revenue_remainder(
        self,
        *,
        name: str,
        price: GenerationPrice,
        label: str | None = None,
        pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.REVENUE,
        tax_treatment: TaxTreatment | str = TaxTreatment.TAXABLE,
    ) -> Self:
        """Register a named revenue policy for generation not allocated to contracts.

        Parameters
        ----------
        name : str
            Component name used in the compiled analysis.
        price : GenerationPrice
            Per-MWh settlement price for unallocated generation. A scheduled
            price must contain an entry whose date exactly matches every
            unallocated generation event; schedule entries are not carried
            forward.
        label : str, optional
            Label applied to generated revenue cashflows. Default is
            ``"Remainder Revenue"``.
        pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category for generated cashflows. Default is revenue.
        tax_treatment : TaxTreatment or str, optional
            Tax treatment for generated cashflows. Default is taxable.

        Returns
        -------
        EnergyProject
            New project with the remainder revenue policy registered.
        """
        self._require_no_generation_revenue("generation_revenue_remainder")
        self._require_available_generation_linked_name(name)
        if any(
            isinstance(registration, GenerationRevenueRemainderConfig)
            for registration in self._config.generation_linked_policies
        ):
            raise ValueError("only one generation_revenue_remainder may be configured")
        registration = GenerationRevenueRemainderConfig(
            name=name,
            price=price,
            label="Remainder Revenue" if label is None else label,
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
        )
        return self._with(
            generation_linked_policies=(
                *self._config.generation_linked_policies,
                registration,
            )
        )

    def generation_linked_policy(
        self,
        *,
        name: str,
        policy: GenerationLinkedCashFlowPolicy,
    ) -> Self:
        """Register a custom generation-linked cashflow policy.

        This is an advanced escape hatch for policies that derive cashflows from the compiled
        generation stream. DCAF validates only that the policy provides ``cashflows(generation)``.
        It does not check that every generated MWh is counted exactly once across custom policies,
        built-in contracts, and remainder revenue. A custom policy can omit generation or count
        the same generation again. Use the built-in generation-revenue methods when that check is
        required.

        Parameters
        ----------
        name : str
            Component name used in the compiled analysis.
        policy : GenerationLinkedCashFlowPolicy
            Object providing ``cashflows(generation)``.

        Returns
        -------
        EnergyProject
            New project with the custom generation-linked policy registered.

        Raises
        ------
        TypeError
            If ``policy`` does not provide ``cashflows(generation)``.
        """
        self._require_available_generation_linked_name(name)
        registration = CustomGenerationLinkedPolicyConfig(
            name=name,
            policy=coerce_generation_linked_policy(policy),
        )
        return self._with(
            generation_linked_policies=(
                *self._config.generation_linked_policies,
                registration,
            )
        )

    def fixed_opex(
        self,
        *,
        name: str = "default",
        amount: float,
        start: date | None = None,
        periods: int | float | None = None,
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
        periods : int or float, optional
            Number of periods. Fractional periods include the final complete
            days that fit in the requested period count. If the requested end
            falls within a day, the incomplete day is omitted and a warning is
            raised. Inferred from ``timeline.operations_end`` when omitted.
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
            Label applied to every fixed-cost cashflow. Default is ``"Fixed OPEX"``.
        timing : {"begin", "middle", "end"}, optional
            Booking-date convention for fixed-OPEX cashflows. When omitted,
            defaults to the project-level timing convention.

        Returns
        -------
        EnergyProject
            New project with updated fixed OPEX configuration.
        """
        updated = FixedOpexConfig(
            amount=amount,
            start=start,
            periods=periods,
            frequency=frequency,
            label="Fixed OPEX" if label is None else label,
            escalation=updated_escalation(
                EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
            timing=timing,
        )
        items = dict(self._config.fixed_opex_items)
        items[name] = updated
        return self._with(fixed_opex_items=items)

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
            Label applied to every variable-cost cashflow. Default is
            ``"Variable Cost"``.

        Returns
        -------
        EnergyProject
            New project with updated variable cost configuration.
        """
        updated = VariableCostConfig(
            rate_per_unit=rate_per_unit,
            label="Variable Cost" if label is None else label,
            escalation=updated_escalation(
                EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        items = dict(self._config.variable_cost_items)
        items[name] = updated
        return self._with(variable_cost_items=items)

    def construction(
        self,
        *,
        overnight_cost: float,
        cod_date: date | None = None,
        spend_profile: SpendProfile | SpendScheduleName | None = None,
        construction_start: date | None = None,
        construction_end: date | None = None,
        period: Period = "month",
        timing: TimingConvention | None = None,
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
            ``operations_start``) when omitted. With end timing, the final
            spend is booked on the preceding, last included construction day.
        period : Period, optional
            Calendar period used to aggregate construction spend. Default is
            ``"month"``.
        timing : {"begin", "middle", "end"}, optional
            Booking convention for distributed construction spend. Defaults to
            the project timing convention. This does not affect a construction
            cost booked as a single cashflow at COD when ``spend_profile`` is
            omitted.
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
        updated = ConstructionScheduleConfig(
            overnight_cost=overnight_cost,
            cod_date=cod_date,
            spend_profile=spend_profile,
            construction_start=construction_start,
            construction_end=construction_end,
            period=period,
            timing=timing,
            escalation=updated_escalation(
                EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        return self._with(construction=updated)

    def construction_stream(
        self,
        *,
        stream: CashFlowStream,
    ) -> Self:
        """Configure a pre-built construction cash-flow stream for the project.

        Parameters
        ----------
        stream : CashFlowStream
            Fully specified construction cash-flow stream.

        Returns
        -------
        EnergyProject
            New project with updated construction configuration.
        """
        return self._with(construction=stream)

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
        if isinstance(self._config.construction, CashFlowStream):
            raise ValueError(
                "construction_debt cannot be configured when a construction "
                "stream override is provided"
            )
        updated = ConstructionFinancingConfig(
            debt_fraction=debt_fraction,
            construction_interest_rate=construction_interest_rate,
            interest_treatment=interest_treatment,
            servicing_period=servicing_period,
            amortization_rate=amortization_rate,
            amortization_term=amortization_term,
            amortization_frequency=amortization_frequency,
            amortization_start=amortization_start,
        )
        return self._with(construction_debt=updated)

    def debt_schedule(
        self,
        *,
        schedule: AmortizationSchedule | CashFlowStream,
    ) -> Self:
        """Configure a pre-built debt schedule for the project.

        Parameters
        ----------
        schedule : AmortizationSchedule or CashFlowStream
            Fully specified debt-service schedule. The schedule represents debt
            issued outside the project cash-flow model, or an explicitly supplied
            opening debt balance. DCAF does not infer financing proceeds for this
            path because the schedule does not contain draw timing.

        Returns
        -------
        EnergyProject
            New project with updated debt configuration.
        """
        return self._with(debt_schedule=schedule)

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
        return self._with(tax_rate=rate, tax_allow_refund=allow_refund)

    def depreciation_macrs(
        self,
        *,
        property_class: MACRSPropertyClass,
        convention: MACRSConvention = "half-year",
        label: str | None = None,
    ) -> Self:
        """Configure MACRS depreciation for the project.

        Parameters
        ----------
        property_class : MACRSPropertyClass
            IRS property class (e.g. ``"5-year"``).
        convention : MACRSConvention, optional
            MACRS convention. Default is ``"half-year"``.
        label : str, optional
            Label applied to every MACRS depreciation flow. Default is
            ``"MACRS Depreciation"``.

        Returns
        -------
        EnergyProject
            New project with MACRS depreciation configured.
        """
        return self._with(
            depreciation=MacrsDepreciationConfig(
                property_class=property_class,
                convention=convention,
                label="MACRS Depreciation" if label is None else label,
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
            Label applied to every VDB depreciation flow. Default is
            ``"VDB Depreciation"``.

        Returns
        -------
        EnergyProject
            New project with VDB depreciation configured.
        """
        return self._with(
            depreciation=VdbDepreciationConfig(
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
        )

    def investment_tax_credit(self, *, rate: float) -> Self:
        """Configure an Investment Tax Credit (ITC) for the project.

        Parameters
        ----------
        rate : float
            ITC rate as a decimal (e.g. ``0.30`` for 30 %).

        Returns
        -------
        EnergyProject
            New project with ITC configured.
        """
        return self._with(itc_rate=rate)

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
        """Configure a Production Tax Credit (PTC) for the project.

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
            Label applied to every PTC cashflow. Default is ``"PTC"``.

        Returns
        -------
        EnergyProject
            New project with PTC configured.

        Raises
        ------
        ValueError
            If *years* is not positive.
        """
        updated = ProductionTaxCreditConfig(
            rate_per_unit=rate_per_unit,
            years=years,
            label="PTC" if label is None else label,
            escalation=updated_escalation(
                EscalationSettings(),
                escalation=escalation,
                escalation_period=escalation_period,
                amount_reference_date=amount_reference_date,
                escalation_policy=escalation_policy,
            ),
        )
        return self._with(ptc=updated)

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
        return self._with(custom_cashflows=custom)

    def analyze(self) -> ProjectAnalysis:
        """Compile all configuration into a :class:`ProjectAnalysis`.

        Builds generation, revenue, fixed and variable OPEX, construction,
        ITC, PTC, depreciation, and debt service streams. Then computes taxable
        income and tax liability from streams marked taxable or deductible.

        Returns
        -------
        ProjectAnalysis
            Compiled project analysis containing all cash-flow components,
            generation streams, and tax calculations.

        Raises
        ------
        ValueError
            If required timeline dates are missing, the generation
            configuration is incomplete, or debt configuration is invalid.
        """
        return ProjectCompiler.from_config(self._config).compile()

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
        convention: DayCountConvention | None = None,
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
            Day count convention. Defaults to the project-wide convention.
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
