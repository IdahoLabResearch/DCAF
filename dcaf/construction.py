"""Construction spend schedule modeling for capital project financial analysis."""

from __future__ import annotations

from dataclasses import dataclass, field, replace as dc_replace
from datetime import date, timedelta
from typing import Self

from dcaf.cashflows import CashFlow, CashFlowStream, CashFlowTags
from dcaf.spend_curves import get_spend_curve
from dcaf.types import (
    InterestTreatment,
    Period,
    SpendSchedule,
    SpendScheduleName,
    _InterestTreatmentEnum,
    _PeriodEnum,
    parse_interest_treatment,
    parse_period,
)
from dcaf.utils import compound_factor, time_delta_per_period, timedelta_fractional_years


def _validate_schedule(schedule: SpendSchedule) -> None:
    """Validate that a spend schedule is well-formed."""
    points = list(schedule)
    if not points:
        raise ValueError("Schedule must not be empty")
    if points[0][0] != 0.0:
        raise ValueError(f"First duration fraction must be 0.0, got {points[0][0]}")
    if points[-1][0] != 1.0:
        raise ValueError(f"Last duration fraction must be 1.0, got {points[-1][0]}")
    if points[-1][1] != 0.0:
        raise ValueError("Last point must have spend_fraction = 0")
    for i in range(1, len(points)):
        if points[i][0] <= points[i - 1][0]:
            raise ValueError(
                "Duration fractions must be monotonically increasing, "
                f"got {points[i - 1][0]} then {points[i][0]}"
            )
    for t, s in points:
        if s < 0:
            raise ValueError(f"All spend fractions must be non-negative, got {s} at t={t}")
    total = sum(s for _, s in points)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Spend fractions must sum to 1.0, got {total}")


def _integrate_curve(schedule: SpendSchedule, t_start: float, t_end: float) -> float:
    """Return the fraction of total spend occurring in ``[t_start, t_end]``."""
    total = 0.0
    for i in range(len(schedule) - 1):
        seg_start, seg_spend = schedule[i]
        seg_end = schedule[i + 1][0]
        seg_len = seg_end - seg_start
        if seg_len <= 0:
            continue
        overlap = max(0.0, min(t_end, seg_end) - max(t_start, seg_start))
        total += seg_spend * overlap / seg_len
    return total


def _resolve_named_schedule(name: SpendScheduleName) -> SpendSchedule:
    """Resolve a named spend schedule to its breakpoint representation."""
    schedule = get_spend_curve(name)
    if schedule is None:
        raise ValueError(f"Unknown curve '{name}'")
    return schedule


@dataclass(frozen=True)
class SpendProfile:
    """Construction spend profile used to allocate total cost over time.

    Parameters
    ----------
    schedule : SpendSchedule
        Piecewise spend schedule expressed as ``(duration_fraction, spend_fraction)``
        breakpoints. The first duration fraction must be ``0.0`` and the last point
        must be ``(1.0, 0.0)``.
    name : str or None, optional
        Human-readable identifier for the profile. Named built-in curves set this
        automatically. Custom schedules typically leave it as ``None``.

    Notes
    -----
    ``SpendProfile`` is the public abstraction for construction timing. It hides the
    choice between named library curves and custom schedules behind a single type.

    Examples
    --------
    Create a named profile:

    >>> from dcaf.construction import SpendProfile
    >>> profile = SpendProfile.curve("flat")
    >>> profile.name
    'flat'

    Create a custom profile:

    >>> custom = SpendProfile.custom(((0.0, 0.6), (0.5, 0.4), (1.0, 0.0)))
    >>> custom.name is None
    True
    """

    schedule: SpendSchedule
    name: str | None = None

    def __post_init__(self) -> None:
        _validate_schedule(self.schedule)

    @classmethod
    def curve(cls, name: SpendScheduleName) -> Self:
        """Return a named spend profile.

        Parameters
        ----------
        name : SpendScheduleName
            Name of the predefined spend curve. Supported values are ``"flat"``,
            ``"bell"``, ``"ramped"``, ``"triangle"``, and ``"linear"``.

        Returns
        -------
        SpendProfile
            Profile wrapping the predefined curve.

        Examples
        --------
        >>> from dcaf.construction import SpendProfile
        >>> SpendProfile.curve("linear").name
        'linear'
        """
        return cls(schedule=_resolve_named_schedule(name), name=name)

    @classmethod
    def custom(cls, schedule: SpendSchedule) -> Self:
        """Return a custom spend profile.

        Parameters
        ----------
        schedule : SpendSchedule
            Custom spend schedule expressed as breakpoint pairs.

        Returns
        -------
        SpendProfile
            Profile wrapping the supplied schedule.

        Examples
        --------
        >>> from dcaf.construction import SpendProfile
        >>> profile = SpendProfile.custom(((0.0, 0.7), (0.5, 0.3), (1.0, 0.0)))
        >>> profile.schedule[0]
        (0.0, 0.7)
        """
        return cls(schedule=schedule)


type ConstructionProfileInput = SpendProfile | SpendScheduleName


@dataclass(frozen=True)
class ConstructionFinancing:
    """Debt funding and construction-period interest configuration.

    Parameters
    ----------
    debt_fraction : float, optional
        Fraction of each construction draw funded by debt. Must be between ``0.0``
        and ``1.0``. Default is ``0.0``.
    interest_rate : float or None, optional
        Annual construction-period interest rate as a decimal. If provided,
        ``debt_fraction`` must be greater than ``0.0``. Default is ``None``.
    interest_treatment : {"capitalize", "pay"}, optional
        Whether accrued interest is capitalized into project cost or paid in cash
        during construction. Default is ``"capitalize"``.

    Notes
    -----
    Financing is separated from spend timing so callers can reuse the same spend
    profile with multiple funding assumptions.

    Examples
    --------
    Unlevered construction:

    >>> from dcaf.construction import ConstructionFinancing
    >>> ConstructionFinancing()
    ConstructionFinancing(debt_fraction=0.0, interest_rate=None, interest_treatment=<...>)

    Debt-funded construction with paid interest:

    >>> financed = ConstructionFinancing.debt(0.7, interest_rate=0.06, treatment="pay")
    >>> financed.debt_fraction
    0.7
    """

    debt_fraction: float = 0.0
    interest_rate: float | None = None
    interest_treatment: InterestTreatment | _InterestTreatmentEnum = "capitalize"

    def __post_init__(self) -> None:
        if not (0.0 <= self.debt_fraction <= 1.0):
            raise ValueError("debt_fraction must be between 0.0 and 1.0")
        if self.interest_rate is not None and self.debt_fraction == 0.0:
            raise ValueError("Construction interest requires debt_fraction > 0")
        object.__setattr__(
            self,
            "interest_treatment",
            parse_interest_treatment(str(self.interest_treatment)),
        )

    @classmethod
    def debt(
        cls,
        debt_fraction: float,
        *,
        interest_rate: float | None = None,
        treatment: InterestTreatment = "capitalize",
    ) -> Self:
        """Return debt financing settings for construction.

        Parameters
        ----------
        debt_fraction : float
            Fraction of each spend draw funded with debt.
        interest_rate : float or None, optional
            Annual construction-period interest rate. Default is ``None``.
        treatment : {"capitalize", "pay"}, optional
            Interest treatment to apply when ``interest_rate`` is provided.

        Returns
        -------
        ConstructionFinancing
            Validated financing settings.

        Examples
        --------
        >>> from dcaf.construction import ConstructionFinancing
        >>> financing = ConstructionFinancing.debt(0.8, interest_rate=0.05)
        >>> financing.interest_rate
        0.05
        """
        return cls(
            debt_fraction=debt_fraction,
            interest_rate=interest_rate,
            interest_treatment=treatment,
        )


@dataclass(frozen=True)
class ConstructionSpendConfig:
    """Validated construction spend configuration.

    Parameters
    ----------
    total_cost : float
        Base project cost before construction-period escalation.
    start_date : date
        Construction start date.
    end_date : date
        Construction completion date. Must be after ``start_date``.
    period : Period, optional
        Cashflow granularity. Default is ``"month"``.
    profile : SpendProfile or SpendScheduleName, optional
        Spend timing profile applied across the construction duration. Passing a
        string uses a named built-in curve. Default is ``"flat"``.
    financing : ConstructionFinancing or None, optional
        Debt and construction-interest assumptions. Passing ``None`` uses
        unlevered construction with no interest.
    escalation_rate : float, optional
        Annual escalation rate compounded to the midpoint of each period.
        Default is ``0.0``.

    Notes
    -----
    ``ConstructionSpendConfig`` is the canonical validated representation used by
    both the direct function API and the builder API.

    Examples
    --------
    >>> from datetime import date
    >>> from dcaf.construction import ConstructionSpendConfig, SpendProfile
    >>> config = ConstructionSpendConfig(
    ...     total_cost=1_000_000,
    ...     start_date=date(2025, 1, 1),
    ...     end_date=date(2026, 1, 1),
    ...     profile=SpendProfile.curve("linear"),
    ... )
    >>> config.period.value
    'month'
    """

    total_cost: float
    start_date: date
    end_date: date
    period: Period | _PeriodEnum = "month"
    profile: SpendProfile = field(default_factory=lambda: SpendProfile.curve("flat"))
    financing: ConstructionFinancing = field(default_factory=ConstructionFinancing)
    escalation_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.total_cost <= 0:
            raise ValueError("total_cost must be positive")
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        # NOTE: the object.__setattr__ calls get around the immutability of the frozen dataclass
        # This looks ugly but it's the best solution for doing post-init validation and
        # normalization but keeping the resulting object immutable.
        object.__setattr__(self, "period", parse_period(str(self.period)))
        object.__setattr__(self, "profile", _normalize_profile(self.profile))
        object.__setattr__(self, "financing", _normalize_financing(self.financing))


@dataclass(frozen=True)
class _ScheduledSpend:
    """Internal representation of a single scheduled construction spend period."""

    start_date: date
    end_date: date
    booking_date: date
    spend_amount: float


def _normalize_profile(profile: ConstructionProfileInput) -> SpendProfile:
    """Convert profile input into a validated ``SpendProfile`` instance."""
    if isinstance(profile, SpendProfile):
        return profile
    return SpendProfile.curve(profile)


def _normalize_financing(financing: ConstructionFinancing | None) -> ConstructionFinancing:
    """Convert financing input into a validated ``ConstructionFinancing`` instance."""
    if financing is None:
        return ConstructionFinancing()
    if isinstance(financing, ConstructionFinancing):
        return financing
    raise TypeError("financing must be a ConstructionFinancing instance or None")


def _iter_period_boundaries(
    start_date: date,
    end_date: date,
    period: _PeriodEnum,
) -> list[tuple[date, date]]:
    """Generate inclusive-exclusive period boundaries over a construction window."""
    delta = time_delta_per_period(period.value)
    boundaries: list[tuple[date, date]] = []
    period_index = 0

    while True:
        current = start_date + delta * period_index
        if current >= end_date:
            return boundaries

        next_date = start_date + delta * (period_index + 1)
        period_end = next_date if next_date <= end_date else end_date
        boundaries.append((current, period_end))
        period_index += 1


def _scheduled_spend_amount(
    config: ConstructionSpendConfig,
    current: date,
    period_end: date,
) -> float:
    """Compute escalated spend allocated to a single period."""
    total_days = (config.end_date - config.start_date).days
    t_start = (current - config.start_date).days / total_days
    t_end = (period_end - config.start_date).days / total_days
    spend_fraction = _integrate_curve(config.profile.schedule, t_start, t_end)

    mid_days = (
        (current - config.start_date).days + (period_end - config.start_date).days
    ) // 2
    mid_date = config.start_date + timedelta(days=mid_days)
    years_elapsed = timedelta_fractional_years(config.start_date, mid_date)
    escalation_factor = compound_factor(config.escalation_rate, years_elapsed)
    return config.total_cost * spend_fraction * escalation_factor


def _scheduled_spends(config: ConstructionSpendConfig) -> list[_ScheduledSpend]:
    """Expand a validated config into per-period scheduled spend entries."""
    periods = _iter_period_boundaries(config.start_date, config.end_date, config.period)
    spends: list[_ScheduledSpend] = []

    for current, period_end in periods:
        spends.append(
            _ScheduledSpend(
                start_date=current,
                end_date=period_end,
                booking_date=period_end,
                spend_amount=_scheduled_spend_amount(config, current, period_end),
            )
        )

    return spends


def _construction_spend_cashflow(spend: _ScheduledSpend) -> CashFlow:
    """Create the construction spend cashflow for one scheduled period."""
    return CashFlow(
        amount=-spend.spend_amount,
        date=spend.booking_date,
        label="Construction Spend",
        is_cash=True,
        tags=frozenset({CashFlowTags.CAPEX, CashFlowTags.EXPENSE}),
    )


def _construction_interest_cashflow(
    interest: float,
    booking_date: date,
    treatment: _InterestTreatmentEnum,
) -> CashFlow | None:
    """Create the construction-interest cashflow for one scheduled period."""
    if interest <= 0.0:
        return None
    if treatment is _InterestTreatmentEnum.CAPITALIZE:
        return CashFlow(
            amount=-interest,
            date=booking_date,
            label="Capitalized Interest",
            is_cash=False,
            tags=frozenset({CashFlowTags.CAPEX}),
        )
    return CashFlow(
        amount=-interest,
        date=booking_date,
        label="Interest Payment",
        is_cash=True,
        tags=frozenset({CashFlowTags.EXPENSE}),
    )


def _build_cashflows(config: ConstructionSpendConfig) -> list[CashFlow]:
    """Build raw construction spend and interest cashflows from a config."""
    flows: list[CashFlow] = []
    debt_balance = 0.0

    for spend in _scheduled_spends(config):
        flows.append(_construction_spend_cashflow(spend))

        if config.financing.debt_fraction == 0.0:
            continue

        draw_amount = spend.spend_amount * config.financing.debt_fraction
        interest = 0.0

        if config.financing.interest_rate is not None:
            period_years = timedelta_fractional_years(spend.start_date, spend.end_date)
            interest = debt_balance * config.financing.interest_rate * period_years
            interest_flow = _construction_interest_cashflow(
                interest,
                spend.booking_date,
                config.financing.interest_treatment,
            )
            if interest_flow is not None:
                flows.append(interest_flow)

        debt_balance += draw_amount
        if (
            config.financing.interest_rate is not None
            and config.financing.interest_treatment is _InterestTreatmentEnum.CAPITALIZE
        ):
            debt_balance += interest

    return flows


class ConstructionSpendBuilder:
    """Immutable fluent builder for construction spend cashflow streams.

    Parameters
    ----------
    total_cost : float
        Base project cost before escalation.
    start_date : date
        Construction start date.
    end_date : date
        Construction completion date.
    period : Period, optional
        Cashflow granularity. Default is ``"month"``.
    profile : SpendProfile or SpendScheduleName, optional
        Spend timing profile. Passing a string uses a named built-in curve.
        Default is ``"flat"``.
    financing : ConstructionFinancing or None, optional
        Debt and construction-interest assumptions. Default is unlevered
        construction.
    escalation_rate : float, optional
        Annual escalation rate. Default is ``0.0``.

    Notes
    -----
    The builder is immutable. Each configuration method returns a new builder,
    which makes it safe to derive multiple scenarios from a shared base setup.

    Examples
    --------
    >>> from datetime import date
    >>> from dcaf.construction import ConstructionSpendBuilder
    >>> stream = (
    ...     ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
    ...     .curve("linear")
    ...     .financing(0.7, interest_rate=0.06, treatment="capitalize")
    ...     .escalation(0.03)
    ...     .build()
    ... )
    >>> len(stream.flows) > 0
    True
    """

    def __init__(
        self,
        total_cost: float,
        start_date: date,
        end_date: date,
        period: Period = "month",
        *,
        profile: ConstructionProfileInput = "flat",
        financing: ConstructionFinancing | None = None,
        escalation_rate: float = 0.0,
    ) -> None:
        self._config = ConstructionSpendConfig(
            total_cost=total_cost,
            start_date=start_date,
            end_date=end_date,
            period=period,
            profile=profile,
            financing=financing,
            escalation_rate=escalation_rate,
        )

    @classmethod
    def from_config(cls, config: ConstructionSpendConfig) -> Self:
        """Create a builder from an existing validated config.

        Parameters
        ----------
        config : ConstructionSpendConfig
            Validated construction spend configuration to wrap.

        Returns
        -------
        ConstructionSpendBuilder
            Builder initialized from ``config``.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder, ConstructionSpendConfig
        >>> config = ConstructionSpendConfig(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        >>> builder = ConstructionSpendBuilder.from_config(config)
        >>> builder.config.total_cost
        1000000
        """
        builder = cls.__new__(cls)
        builder._config = config
        return builder

    @property
    def config(self) -> ConstructionSpendConfig:
        """Return the builder's validated configuration.

        Returns
        -------
        ConstructionSpendConfig
            Immutable validated configuration backing the builder.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder
        >>> builder = ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        >>> builder.config.profile.name
        'flat'
        """
        return self._config

    def profile(self, profile: ConstructionProfileInput) -> Self:
        """Set the spend profile.

        Parameters
        ----------
        profile : SpendProfile or SpendScheduleName
            Either a validated ``SpendProfile`` instance or the name of a built-in
            curve.

        Returns
        -------
        ConstructionSpendBuilder
            New builder with the updated profile.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder, SpendProfile
        >>> builder = ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        >>> updated = builder.profile(SpendProfile.curve("bell"))
        >>> updated.config.profile.name
        'bell'
        """
        return self.from_config(dc_replace(self._config, profile=_normalize_profile(profile)))

    def curve(self, name: SpendScheduleName) -> Self:
        """Set a named spend profile.

        Parameters
        ----------
        name : SpendScheduleName
            Name of the built-in spend curve to apply.

        Returns
        -------
        ConstructionSpendBuilder
            New builder with the named profile applied.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder
        >>> builder = ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        >>> builder.curve("linear").config.profile.name
        'linear'
        """
        return self.profile(name)

    def schedule(self, schedule: SpendSchedule) -> Self:
        """Set a custom spend profile.

        Parameters
        ----------
        schedule : SpendSchedule
            Custom schedule expressed as breakpoint pairs.

        Returns
        -------
        ConstructionSpendBuilder
            New builder with the custom profile applied.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder
        >>> builder = ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        >>> updated = builder.schedule(((0.0, 0.6), (0.5, 0.4), (1.0, 0.0)))
        >>> updated.config.profile.name is None
        True
        """
        return self.profile(SpendProfile.custom(schedule))

    def financing(
        self,
        debt_fraction: float,
        *,
        interest_rate: float | None = None,
        treatment: InterestTreatment = "capitalize",
    ) -> Self:
        """Set debt funding and construction-period interest behavior.

        Parameters
        ----------
        debt_fraction : float
            Fraction of each construction draw funded by debt.
        interest_rate : float or None, optional
            Annual construction-period interest rate. Default is ``None``.
        treatment : {"capitalize", "pay"}, optional
            Interest treatment to apply when ``interest_rate`` is supplied.

        Returns
        -------
        ConstructionSpendBuilder
            New builder with updated financing assumptions.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder
        >>> builder = ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        >>> updated = builder.financing(0.75, interest_rate=0.06, treatment="pay")
        >>> updated.config.financing.debt_fraction
        0.75
        """
        return self.from_config(
            dc_replace(
                self._config,
                financing=ConstructionFinancing.debt(
                    debt_fraction,
                    interest_rate=interest_rate,
                    treatment=treatment,
                ),
            )
        )

    def escalation(self, rate: float) -> Self:
        """Set the annual cost escalation rate.

        Parameters
        ----------
        rate : float
            Annual escalation rate applied to each period midpoint.

        Returns
        -------
        ConstructionSpendBuilder
            New builder with updated escalation assumptions.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder
        >>> builder = ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        >>> builder.escalation(0.03).config.escalation_rate
        0.03
        """
        return self.from_config(dc_replace(self._config, escalation_rate=rate))

    def build(self) -> CashFlowStream:
        """Build and return the construction spend ``CashFlowStream``.

        Returns
        -------
        CashFlowStream
            Construction spend stream containing spend cashflows and, when
            configured, construction-interest flows.

        Examples
        --------
        >>> from datetime import date
        >>> from dcaf.construction import ConstructionSpendBuilder
        >>> stream = (
        ...     ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2025, 7, 1))
        ...     .curve("linear")
        ...     .build()
        ... )
        >>> stream.flows[0].label
        'Construction Spend'
        """
        return CashFlowStream(_build_cashflows(self._config))


def construction_spend_schedule(
    total_cost: float,
    start_date: date,
    end_date: date,
    period: Period = "month",
    *,
    profile: ConstructionProfileInput = "flat",
    financing: ConstructionFinancing | None = None,
    escalation_rate: float = 0.0,
) -> CashFlowStream:
    """Build a construction spend schedule directly.

    Parameters
    ----------
    total_cost : float
        Base project cost before escalation.
    start_date : date
        Construction start date.
    end_date : date
        Construction completion date.
    period : Period, optional
        Cashflow granularity. Default is ``"month"``.
    profile : SpendProfile or SpendScheduleName, optional
        Spend timing profile. The default is ``"flat"``, which keeps the implicit
        profile visible in the function signature.
    financing : ConstructionFinancing or None, optional
        Debt and construction-interest assumptions. Default is unlevered
        construction.
    escalation_rate : float, optional
        Annual escalation rate applied to each period midpoint. Default is ``0.0``.

    Returns
    -------
    CashFlowStream
        Construction spend cashflows for the requested configuration.

    Notes
    -----
    This is the simplest public entry point. Use it when one call fully describes
    the scenario. For scenario templates or incremental configuration, use
    ``ConstructionSpendBuilder``.

    Examples
    --------
    Basic usage with the implicit ``"flat"`` profile:

    >>> from datetime import date
    >>> from dcaf.construction import construction_spend_schedule
    >>> stream = construction_spend_schedule(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
    >>> len(stream.flows) > 0
    True

    Usage with explicit financing and a named profile:

    >>> from dcaf.construction import ConstructionFinancing
    >>> financed = construction_spend_schedule(
    ...     1_000_000,
    ...     date(2025, 1, 1),
    ...     date(2026, 1, 1),
    ...     profile="linear",
    ...     financing=ConstructionFinancing.debt(0.7, interest_rate=0.06),
    ... )
    >>> any(flow.label == "Capitalized Interest" for flow in financed.flows)
    True
    """
    return ConstructionSpendBuilder(
        total_cost=total_cost,
        start_date=start_date,
        end_date=end_date,
        period=period,
        profile=profile,
        financing=financing,
        escalation_rate=escalation_rate,
    ).build()
