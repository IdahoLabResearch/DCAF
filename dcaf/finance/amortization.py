# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Debt amortization schedules for financial modeling.

Provides a builder-pattern API for generating amortization ``CashFlowStream``
objects with separate interest and principal components.

Classes
-------
AmortizationSchedule
    Decomposed debt service schedule with total, interest, and principal streams.
AmortizationBuilder
    Fluent builder for configuring and generating amortization schedules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Self, assert_never, overload

from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.shared.types import (
    Period,
    ProFormaCategory,
    TaxTreatment,
    _PeriodEnum,
    normalize_cashflow_classification,
    parse_period,
)
from dcaf.shared.formatting import format_label
from dcaf.shared.time import time_delta_per_period


@dataclass(frozen=True)
class _PeriodConfig:
    periodic_rate: float
    pays_principal: bool


def _payment_periods_per_year(frequency: Period | _PeriodEnum) -> int:
    """Return the number of payment periods per year for a given frequency.

    Parameters
    ----------
    frequency : Period
        Payment frequency (``"day"``, ``"month"``, ``"quarter"``, or ``"year"``).

    Returns
    -------
    int
        Number of payment periods per year.
    """
    normalized_frequency = parse_period(str(frequency))
    match normalized_frequency:
        case _PeriodEnum.DAY:
            return 365
        case _PeriodEnum.MONTH:
            return 12
        case _PeriodEnum.QUARTER:
            return 4
        case _PeriodEnum.YEAR:
            return 1
        case _:
            assert_never(normalized_frequency)


@dataclass
class AmortizationSchedule:
    """Decomposed debt service schedule.

    Attributes
    ----------
    total : CashFlowStream
        Combined debt service (interest + principal) cashflows.
    interest : CashFlowStream
        Interest-only cashflows.
    principal : CashFlowStream
        Principal-only cashflows.
    """

    total: CashFlowStream
    interest: CashFlowStream
    principal: CashFlowStream

    @classmethod
    def builder(
        cls,
        principal: float,
        annual_rate: float,
        term: int,
        start_date: date,
        frequency: Period = "month",
        label: str = "Debt Service",
        interest_label: str = "Interest",
        principal_label: str = "Principal",
        interest_pro_forma_category: ProFormaCategory | str | None = (
            ProFormaCategory.FINANCING_INTEREST
        ),
        interest_tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
        principal_pro_forma_category: ProFormaCategory | str | None = (
            ProFormaCategory.FINANCING_PRINCIPAL
        ),
        principal_tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
    ) -> AmortizationBuilder:
        """Return an ``AmortizationBuilder`` for fluent schedule configuration.

        Parameters
        ----------
        principal : float
            Loan principal amount.
        annual_rate : float
            Annual interest rate as a decimal (e.g., ``0.05`` for 5%).
        term : int
            Number of payment periods.
        start_date : date
            Date of the first payment.
        frequency : Period, optional
            Payment frequency. Default is ``"month"``.
        label : str, optional
            Template for total payment labels. ``{n}`` is replaced with the period number.
        interest_label : str, optional
            Template for interest payment labels. ``{n}`` is replaced with the period number.
        principal_label : str, optional
            Template for principal payment labels. ``{n}`` is replaced with the period number.
        interest_pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category applied to interest cashflows.
        interest_tax_treatment : TaxTreatment or str, optional
            Tax treatment applied to interest cashflows.
        principal_pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category applied to principal cashflows.
        principal_tax_treatment : TaxTreatment or str, optional
            Tax treatment applied to principal cashflows.

        Returns
        -------
        AmortizationBuilder
            Builder instance ready for further configuration or ``build()``.
        """
        return AmortizationBuilder(
            principal=principal,
            annual_rate=annual_rate,
            term=term,
            start_date=start_date,
            frequency=frequency,
            label=label,
            interest_label=interest_label,
            principal_label=principal_label,
            interest_pro_forma_category=interest_pro_forma_category,
            interest_tax_treatment=interest_tax_treatment,
            principal_pro_forma_category=principal_pro_forma_category,
            principal_tax_treatment=principal_tax_treatment,
        )

    @classmethod
    def build(
        cls,
        principal: float,
        annual_rate: float,
        term: int,
        start_date: date,
        frequency: Period = "month",
        label: str = "Debt Service",
        interest_label: str = "Interest",
        principal_label: str = "Principal",
        interest_pro_forma_category: ProFormaCategory | str | None = (
            ProFormaCategory.FINANCING_INTEREST
        ),
        interest_tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
        principal_pro_forma_category: ProFormaCategory | str | None = (
            ProFormaCategory.FINANCING_PRINCIPAL
        ),
        principal_tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
    ) -> AmortizationSchedule:
        """Build and return an ``AmortizationSchedule`` directly (no rules).

        Equivalent to calling ``builder(...).build()``. Use when no interest-only
        periods, interest-free windows, or rate changes are needed.

        Parameters
        ----------
        principal : float
            Loan principal amount.
        annual_rate : float
            Annual interest rate as a decimal (e.g., ``0.05`` for 5%).
        term : int
            Number of payment periods.
        start_date : date
            Date of the first payment.
        frequency : Period, optional
            Payment frequency. Default is ``"month"``.
        label : str, optional
            Template for total payment labels. ``{n}`` is replaced with the period number.
        interest_label : str, optional
            Template for interest payment labels. ``{n}`` is replaced with the period number.
        principal_label : str, optional
            Template for principal payment labels. ``{n}`` is replaced with the period number.
        interest_pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category applied to interest cashflows.
        interest_tax_treatment : TaxTreatment or str, optional
            Tax treatment applied to interest cashflows.
        principal_pro_forma_category : ProFormaCategory or str or None, optional
            Pro-forma category applied to principal cashflows.
        principal_tax_treatment : TaxTreatment or str, optional
            Tax treatment applied to principal cashflows.

        Returns
        -------
        AmortizationSchedule
            The fully computed schedule.
        """
        return cls.builder(
            principal=principal,
            annual_rate=annual_rate,
            term=term,
            start_date=start_date,
            frequency=frequency,
            label=label,
            interest_label=interest_label,
            principal_label=principal_label,
            interest_pro_forma_category=interest_pro_forma_category,
            interest_tax_treatment=interest_tax_treatment,
            principal_pro_forma_category=principal_pro_forma_category,
            principal_tax_treatment=principal_tax_treatment,
        ).build()


def amortize(
    principal: float,
    annual_rate: float,
    term: int,
    start_date: date,
    frequency: Period = "month",
    label: str = "Debt Service",
    interest_label: str = "Interest",
    principal_label: str = "Principal",
    interest_pro_forma_category: ProFormaCategory | str | None = (
        ProFormaCategory.FINANCING_INTEREST
    ),
    interest_tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
    principal_pro_forma_category: ProFormaCategory | str | None = (
        ProFormaCategory.FINANCING_PRINCIPAL
    ),
    principal_tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
) -> AmortizationSchedule:
    """Build a standard fixed-payment amortization schedule.

    Equivalent to ``AmortizationSchedule.build()``. Prefer
    ``AmortizationSchedule.builder()`` when interest-only periods, interest-free
    windows, or mid-schedule rate changes are required.

    Parameters
    ----------
    principal : float
        Loan principal amount.
    annual_rate : float
        Annual interest rate as a decimal (e.g., ``0.05`` for 5%).
    term : int
        Number of payment periods.
    start_date : date
        Date of the first payment.
    frequency : Period, optional
        Payment frequency. Default is ``"month"``.
    label : str, optional
        Template for total payment labels. ``{n}`` is replaced with the period number.
    interest_label : str, optional
        Template for interest payment labels. ``{n}`` is replaced with the period number.
    principal_label : str, optional
        Template for principal payment labels. ``{n}`` is replaced with the period number.
    interest_pro_forma_category : ProFormaCategory or str or None, optional
        Pro-forma category applied to interest cashflows.
        Default is ``ProFormaCategory.FINANCING_INTEREST``.
    interest_tax_treatment : TaxTreatment or str, optional
        Tax treatment applied to interest cashflows. Default is ``TaxTreatment.DEDUCTIBLE``.
    principal_pro_forma_category : ProFormaCategory or str or None, optional
        Pro-forma category applied to principal cashflows.
        Default is ``ProFormaCategory.FINANCING_PRINCIPAL``.
    principal_tax_treatment : TaxTreatment or str, optional
        Tax treatment applied to principal cashflows. Default is ``TaxTreatment.NONE``.

    Returns
    -------
    AmortizationSchedule
        Decomposed debt service schedule with ``total``, ``interest``, and
        ``principal`` ``CashFlowStream`` objects.

    Examples
    --------
    A 30-year mortgage at 5% annual rate with monthly payments:

    >>> from datetime import date
    >>> from dcaf.finance.amortization import amortize
    >>> schedule = amortize(100_000.0, 0.05, 360, date(2026, 1, 1))
    >>> round(abs(schedule.total.entries[0].amount), 2)
    536.82
    """
    return AmortizationSchedule.build(
        principal=principal,
        annual_rate=annual_rate,
        term=term,
        start_date=start_date,
        frequency=frequency,
        label=label,
        interest_label=interest_label,
        principal_label=principal_label,
        interest_pro_forma_category=interest_pro_forma_category,
        interest_tax_treatment=interest_tax_treatment,
        principal_pro_forma_category=principal_pro_forma_category,
        principal_tax_treatment=principal_tax_treatment,
    )


class AmortizationBuilder:
    """Fluent builder for configuring and generating amortization schedules.

    Use ``AmortizationSchedule.builder()`` to obtain an instance. Chain rule
    methods (``interest_only``, ``interest_free``, ``rate_change``) then call
    ``build()`` to produce the final ``AmortizationSchedule``.
    """

    def __init__(
        self,
        principal: float,
        annual_rate: float,
        term: int,
        start_date: date,
        frequency: Period = "month",
        label: str = "Debt Service",
        interest_label: str = "Interest",
        principal_label: str = "Principal",
        interest_pro_forma_category: ProFormaCategory | str | None = (
            ProFormaCategory.FINANCING_INTEREST
        ),
        interest_tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
        principal_pro_forma_category: ProFormaCategory | str | None = (
            ProFormaCategory.FINANCING_PRINCIPAL
        ),
        principal_tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
    ) -> None:
        if term <= 0:
            raise ValueError("term must be positive")
        self._principal = principal
        self._annual_rate = annual_rate
        self._term = term
        self._start_date = start_date
        self._frequency: _PeriodEnum = parse_period(str(frequency))
        self._label = label
        self._interest_label = interest_label
        self._principal_label = principal_label
        (
            self._interest_pro_forma_category,
            self._interest_tax_treatment,
        ) = normalize_cashflow_classification(
            interest_pro_forma_category,
            interest_tax_treatment,
        )
        (
            self._principal_pro_forma_category,
            self._principal_tax_treatment,
        ) = normalize_cashflow_classification(
            principal_pro_forma_category,
            principal_tax_treatment,
        )
        self._rules: list[tuple[str, set[int] | tuple[int, float]]] = []

    @staticmethod
    def _normalize_periods(periods: int | range | Sequence[int]) -> set[int]:
        """Convert a period specification to a set of zero-based period indices.

        Parameters
        ----------
        periods : int or range or Sequence[int]
            If ``int``, interpreted as a count: returns ``{0, 1, ..., periods-1}``.
            If ``range`` or sequence, converted directly to a set.

        Returns
        -------
        set[int]
            Zero-based period indices.
        """
        if isinstance(periods, int):
            return set(range(periods))
        return set(periods)

    def interest_only(self, periods: int | range | Sequence[int]) -> Self:
        """Designate periods as interest-only (no principal repayment).

        Parameters
        ----------
        periods : int or range or Sequence[int]
            Periods to mark as interest-only. If ``int``, all periods
            ``0`` through ``periods-1`` are affected.

        Returns
        -------
        Self
            The builder instance for method chaining.
        """
        self._rules.append(("interest_only", self._normalize_periods(periods)))
        return self

    @overload
    def interest_free(self, *, from_period: int = ..., to_period: int = ...) -> Self: ...
    @overload
    def interest_free(self, *, from_date: date = ..., to_date: date = ...) -> Self: ...
    def interest_free(
        self,
        *,
        from_period: int | None = None,
        to_period: int | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> Self:
        """Designate a range of periods as interest-free (zero interest rate).

        Specify either period indices or dates, not both. Period bounds are
        inclusive on both ends; date bounds are half-open ``[from_date,
        to_date)``. Omitting an endpoint defaults to the beginning or end of
        the schedule.

        Parameters
        ----------
        from_period : int, optional
            First zero-indexed period to be interest-free. Defaults to ``0``.
        to_period : int, optional
            Last zero-indexed period to be interest-free (inclusive).
            Defaults to the final period.
        from_date : date, optional
            Start date of the interest-free window (inclusive).
        to_date : date, optional
            Exclusive end boundary; payment dates on or after this date are
            excluded.

        Returns
        -------
        Self
            The builder instance for method chaining.

        Raises
        ------
        ValueError
            If both period and date arguments are provided, or if neither is provided.
        """
        has_period = from_period is not None or to_period is not None
        has_date = from_date is not None or to_date is not None
        if has_period and has_date:
            msg = "Cannot mix period and date arguments"
            raise ValueError(msg)
        if not has_period and not has_date:
            msg = "Must specify from_period/to_period or from_date/to_date"
            raise ValueError(msg)

        if has_date:
            indices = self._resolve_date_range(from_date, to_date)
        else:
            start = from_period if from_period is not None else 0
            end = to_period if to_period is not None else self._term - 1
            indices = set(range(start, end + 1))

        self._rules.append(("interest_free", indices))
        return self

    def _resolve_date_range(self, from_date: date | None, to_date: date | None) -> set[int]:
        """Convert a half-open date range to a set of zero-based period indices.

        Parameters
        ----------
        from_date : date or None
            Inclusive start date. ``None`` means no lower bound.
        to_date : date or None
            Exclusive end date. ``None`` means no upper bound.

        Returns
        -------
        set[int]
            Zero-based indices of periods whose payment dates fall within
            ``[from_date, to_date)``.
        """
        delta = time_delta_per_period(self._frequency.value)
        indices: list[int] = []
        for i in range(self._term):
            payment_date = self._start_date + delta * i
            if from_date is not None and payment_date < from_date:
                continue
            if to_date is not None and payment_date >= to_date:
                continue
            indices.append(i)
        return set(indices)

    def rate_change(self, from_period: int, annual_rate: float) -> Self:
        """Change the annual interest rate starting at a given period.

        Parameters
        ----------
        from_period : int
            Zero-based period index at which the new rate takes effect.
        annual_rate : float
            New annual interest rate as a decimal (e.g., ``0.06`` for 6%).

        Returns
        -------
        Self
            The builder instance for method chaining.
        """
        ppy = _payment_periods_per_year(self._frequency)
        new_periodic_rate = annual_rate / ppy
        self._rules.append(("rate_change", (from_period, new_periodic_rate)))
        return self

    def _apply_rules(self, period_index: int, config: _PeriodConfig) -> _PeriodConfig:
        """Apply all stored rules to produce the effective config for a period.

        Parameters
        ----------
        period_index : int
            Zero-based index of the period being computed.
        config : _PeriodConfig
            Default config (base periodic rate, ``pays_principal=True``).

        Returns
        -------
        _PeriodConfig
            Effective configuration after applying all rules in order.
        """
        rate = config.periodic_rate
        pays = config.pays_principal
        for kind, data in self._rules:
            if kind == "interest_only":
                if period_index in data:
                    pays = False
            elif kind == "interest_free":
                if period_index in data:
                    rate = 0.0
            elif kind == "rate_change":
                from_period, new_rate = data
                if period_index >= from_period:
                    rate = new_rate
        return _PeriodConfig(periodic_rate=rate, pays_principal=pays)

    def _count_remaining_amortizing(self, current: int, default_config: _PeriodConfig) -> int:
        """Count amortizing periods from ``current`` (inclusive) through the end.

        Parameters
        ----------
        current : int
            Zero-based index of the first period to consider.
        default_config : _PeriodConfig
            Base config passed to ``_apply_rules`` for each period.

        Returns
        -------
        int
            Number of periods with ``pays_principal=True`` from ``current`` onward.
        """
        count = 0
        for i in range(current, self._term):
            config = self._apply_rules(i, default_config)
            if config.pays_principal:
                count += 1
        return count

    def _compute_amounts(
        self,
        balance: float,
        config: _PeriodConfig,
        period_index: int,
        default_config: _PeriodConfig,
    ) -> tuple[float, float, float]:
        """Compute interest, principal, and total payment amounts for one period.

        Parameters
        ----------
        balance : float
            Outstanding principal balance at the start of the period.
        config : _PeriodConfig
            Effective configuration for this period (after rules are applied).
        period_index : int
            Zero-based index of the current period.
        default_config : _PeriodConfig
            Base config used to count remaining amortizing periods.

        Returns
        -------
        tuple[float, float, float]
            ``(interest_amount, principal_amount, total_amount)``
        """
        interest_amount = balance * config.periodic_rate

        if config.pays_principal:
            remaining = self._count_remaining_amortizing(period_index, default_config)
            if config.periodic_rate == 0.0:
                total_amount = balance / remaining
            else:
                r_n = (1.0 + config.periodic_rate) ** remaining
                total_amount = balance * config.periodic_rate * r_n / (r_n - 1.0)
            principal_amount = total_amount - interest_amount
        else:
            principal_amount = 0.0
            total_amount = interest_amount

        return interest_amount, principal_amount, total_amount

    def _make_cashflows(
        self,
        n: int,
        payment_date: date,
        interest_amount: float,
        principal_amount: float,
        total_amount: float,
    ) -> tuple[CashFlow, CashFlow, CashFlow]:
        """Construct the three ``CashFlow`` objects for one period.

        Parameters
        ----------
        n : int
            One-based period number used in label formatting.
        payment_date : date
            Date of the payment.
        interest_amount : float
            Interest component of the payment.
        principal_amount : float
            Principal component of the payment.
        total_amount : float
            Combined debt service payment (interest + principal).

        Returns
        -------
        tuple[CashFlow, CashFlow, CashFlow]
            ``(total_flow, interest_flow, principal_flow)``
        """

        total_flow = CashFlow(
            amount=-total_amount,
            date=payment_date,
            label=format_label(self._label, n),
            is_cash=True,
            pro_forma_category=None,
            tax_treatment=TaxTreatment.NONE,
        )
        interest_flow = CashFlow(
            amount=-interest_amount,
            date=payment_date,
            label=format_label(self._interest_label, n),
            is_cash=True,
            pro_forma_category=self._interest_pro_forma_category,
            tax_treatment=self._interest_tax_treatment,
        )
        principal_flow = CashFlow(
            amount=-principal_amount,
            date=payment_date,
            label=format_label(self._principal_label, n),
            is_cash=True,
            pro_forma_category=self._principal_pro_forma_category,
            tax_treatment=self._principal_tax_treatment,
        )
        return total_flow, interest_flow, principal_flow

    def build(self) -> AmortizationSchedule:
        """Execute the schedule generation loop and return the schedule.

        Returns
        -------
        AmortizationSchedule
            Decomposed debt service schedule with ``total``, ``interest``,
            and ``principal`` ``CashFlowStream`` objects.
        """
        ppy = _payment_periods_per_year(self._frequency)
        default_config = _PeriodConfig(periodic_rate=self._annual_rate / ppy, pays_principal=True)
        delta = time_delta_per_period(self._frequency.value)

        balance = self._principal
        total_flows: list[CashFlow] = []
        interest_flows: list[CashFlow] = []
        principal_flows: list[CashFlow] = []

        for i in range(self._term):
            config = self._apply_rules(i, default_config)
            interest, principal, total = self._compute_amounts(balance, config, i, default_config)
            balance -= principal
            t, intr, princ = self._make_cashflows(
                i + 1, self._start_date + delta * i, interest, principal, total
            )
            total_flows.append(t)
            interest_flows.append(intr)
            principal_flows.append(princ)

        return AmortizationSchedule(
            total=CashFlowStream(total_flows),
            interest=CashFlowStream(interest_flows),
            principal=CashFlowStream(principal_flows),
        )
