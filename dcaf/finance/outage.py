# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Outage cashflow and generation helpers.

Provides ``generator_outage()`` for delta-style negative generation and
``construction_outage()`` for the bundled lost-revenue plus replacement-cost
cashflows that arise during nuclear-uprate construction outages.
"""

from datetime import date

from dcaf.finance.escalation import EscalationPolicy
from dcaf.shared.types import (
    DayCountConvention,
    Period,
    ProFormaCategory,
    TaxTreatment,
    TimingConvention,
    normalize_cashflow_classification,
)
from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.streams.generation import GenerationStream, _outage_event_date


def generator_outage(
    *,
    capacity_mw: float,
    capacity_factor: float,
    start: date,
    end: date,
    capacity_reduction: float = 1.0,
    timing: TimingConvention = "end",
    label: str = "Generator Outage",
    day_count_convention: DayCountConvention = "actual/actual",
) -> GenerationStream:
    """
    Create a negative ``GenerationStream`` for an explicit outage interval.

    Thin functional facade over :meth:`GenerationStream.from_outage` for symmetry
    with :func:`construction_outage` and other module-level stream helpers such
    as :func:`dcaf.finance.opex.fixed_opex`. The returned stream contains a
    single negative ``Generation`` entry intended for delta-style modeling.

    Parameters
    ----------
    capacity_mw : float
        Capacity affected by the outage in MW.
    capacity_factor : float
        Counterfactual capacity factor that would have applied during the
        outage interval.
    start : date
        Inclusive outage start date.
    end : date
        Exclusive outage end date. The outage duration is ``end - start``.
    capacity_reduction : float, optional
        Fraction of the affected capacity unavailable during the outage.
        ``1.0`` means fully offline and ``0.5`` means half output lost.
        Default is ``1.0``.
    timing : {"begin", "middle", "end"}, optional
        Date assigned to the negative generation entry. Default is ``"end"``.
    label : str, optional
        Label for the negative generation entry. Default is ``"Generator Outage"``.
    day_count_convention : DayCountConvention, optional
        Day-count convention used to compute elapsed outage hours.

    Returns
    -------
    GenerationStream
        Stream containing one negative generation entry.

    Raises
    ------
    ValueError
        If the date range is empty or invalid, numeric inputs are not finite,
        capacity inputs are negative, or ``capacity_reduction`` is outside
        ``[0, 1]``.

    Examples
    --------
    >>> from datetime import date
    >>> outage = generator_outage(
    ...     capacity_mw=1000.0,
    ...     capacity_factor=0.92,
    ...     start=date(2030, 5, 1),
    ...     end=date(2030, 5, 11),
    ... )
    >>> outage.sum()
    -220800.0
    """
    return GenerationStream.from_outage(
        capacity_mw=capacity_mw,
        capacity_factor=capacity_factor,
        start=start,
        end=end,
        capacity_reduction=capacity_reduction,
        timing=timing,
        label=label,
        day_count_convention=day_count_convention,
    )


def construction_outage(
    *,
    capacity_mw: float,
    capacity_factor: float,
    start: date,
    end: date,
    sell_price_per_unit: float,
    capacity_reduction: float = 1.0,
    fixed_cost: float = 0.0,
    cost_per_day: float = 0.0,
    timing: TimingConvention = "end",
    lost_revenue_label: str = "Outage Lost Revenue",
    fixed_cost_label: str = "Outage Fixed Cost",
    daily_cost_label: str = "Outage Replacement Power",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.OPERATING_COST,
    tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
    escalation: float = 0.0,
    escalation_period: Period = "year",
    amount_reference_date: date | None = None,
    day_count_convention: DayCountConvention = "actual/actual",
    escalation_policy: EscalationPolicy | None = None,
) -> CashFlowStream:
    """
    Build outage-economics cashflows for an unmodeled baseline plant outage.

    Models the typical nuclear-uprate construction-outage scenario in which a
    refueling outage on the existing baseline plant is extended to perform
    uprate work. The lost generation from the baseline plant becomes a lost
    revenue cashflow, and replacement-power and fixed costs are added as
    distinct line items so they appear separately in pro-forma output.

    The returned stream contains up to three cashflows, all carrying the
    same ``pro_forma_category`` and ``tax_treatment``:

    - **Lost revenue** at ``sell_price_per_unit * lost_mwh``, where
      ``lost_mwh`` is computed from capacity, capacity factor, capacity
      reduction, and convention-aware elapsed outage hours. Sign is negative
      (a cost).
    - **Fixed cost** of ``-abs(fixed_cost)`` at the booked outage date,
      omitted when ``fixed_cost == 0``.
    - **Daily cost** of ``-abs(cost_per_day) * (end - start).days`` at the
      booked outage date, omitted when ``cost_per_day == 0``.

    Parameters
    ----------
    capacity_mw : float
        Baseline capacity affected by the outage in MW.
    capacity_factor : float
        Counterfactual capacity factor during the outage.
    start : date
        Inclusive outage start date.
    end : date
        Exclusive outage end date.
    sell_price_per_unit : float
        Price per MWh used to value the lost generation.
    capacity_reduction : float, optional
        Fraction of affected capacity unavailable during the outage. Default
        is ``1.0``.
    fixed_cost : float, optional
        One-time outage cost. Sign is ignored. Default is ``0.0``.
    cost_per_day : float, optional
        Outage cost per calendar day. Sign is ignored. Default is ``0.0``.
    timing : {"begin", "middle", "end"}, optional
        Booking-date convention for all generated cashflows. Default is
        ``"end"``.
    lost_revenue_label : str, optional
        Label for the lost-revenue cashflow. Default is ``"Outage Lost Revenue"``.
    fixed_cost_label : str, optional
        Label for the fixed-cost cashflow. Default is ``"Outage Fixed Cost"``.
    daily_cost_label : str, optional
        Label for the per-day cost cashflow. Default is
        ``"Outage Replacement Power"``.
    pro_forma_category : ProFormaCategory or str or None, optional
        Pro-forma category applied to every cashflow. Default is
        ``"operating_cost"``.
    tax_treatment : TaxTreatment or str, optional
        Tax treatment applied to every cashflow. Default is ``"deductible"``.
    escalation : float, optional
        Compound escalation rate for ``sell_price_per_unit``. Default is
        ``0.0``.
    escalation_period : Period, optional
        Compounding period associated with ``escalation``. Default is ``"year"``.
    amount_reference_date : date, optional
        Date at which ``sell_price_per_unit`` is known. Defaults to the booked
        outage date.
    day_count_convention : DayCountConvention, optional
        Day-count convention used for lost-generation hours and annual price escalation.
    escalation_policy : EscalationPolicy, optional
        Advanced escalation override. When provided it must not be combined
        with ``escalation``, ``escalation_period``, or ``amount_reference_date``.

    Returns
    -------
    CashFlowStream
        Stream of one to three cashflows representing the outage economics.

    Raises
    ------
    ValueError
        If the date range is empty or invalid, capacity inputs are negative,
        ``capacity_reduction`` is outside ``[0, 1]``, or ``sell_price_per_unit``,
        ``fixed_cost``, or ``cost_per_day`` is not finite.

    Examples
    --------
    >>> from datetime import date
    >>> stream = construction_outage(
    ...     capacity_mw=1200.0,
    ...     capacity_factor=0.92,
    ...     start=date(2030, 5, 1),
    ...     end=date(2030, 5, 21),
    ...     sell_price_per_unit=50.0,
    ...     fixed_cost=2_000_000.0,
    ...     cost_per_day=100_000.0,
    ... )
    >>> [(f.label, round(f.amount, 2)) for f in stream.entries]
    ... # doctest: +NORMALIZE_WHITESPACE
    [('Outage Lost Revenue', -26496000.0),
     ('Outage Fixed Cost', -2000000.0),
     ('Outage Replacement Power', -2000000.0)]
    """
    outage_generation = GenerationStream.from_outage(
        capacity_mw=capacity_mw,
        capacity_factor=capacity_factor,
        start=start,
        end=end,
        capacity_reduction=capacity_reduction,
        timing=timing,
        label=lost_revenue_label,
        day_count_convention=day_count_convention,
    )

    if escalation_policy is not None:
        if escalation != 0.0 or amount_reference_date is not None:
            raise ValueError(
                "escalation_policy cannot be combined with escalation or amount_reference_date"
            )
        lost_revenue = outage_generation.to_revenue(
            price_per_mwh=sell_price_per_unit,
            label=lost_revenue_label,
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
            escalation_policy=escalation_policy,
        )
    else:
        lost_revenue = outage_generation.to_revenue(
            price_per_mwh=sell_price_per_unit,
            label=lost_revenue_label,
            pro_forma_category=pro_forma_category,
            tax_treatment=tax_treatment,
            escalation=escalation,
            escalation_period=escalation_period,
            amount_reference_date=amount_reference_date,
            day_count_convention=day_count_convention,
        )

    booking_date = _outage_event_date(start=start, end=end, timing=timing)
    extras = _replacement_cost_flows(
        fixed_cost=fixed_cost,
        cost_per_day=cost_per_day,
        days=(end - start).days,
        booking_date=booking_date,
        fixed_cost_label=fixed_cost_label,
        daily_cost_label=daily_cost_label,
        pro_forma_category=pro_forma_category,
        tax_treatment=tax_treatment,
    )
    if not extras:
        return lost_revenue
    return CashFlowStream.from_streams(lost_revenue, *extras)


def _replacement_cost_flows(
    *,
    fixed_cost: float,
    cost_per_day: float,
    days: int,
    booking_date: date,
    fixed_cost_label: str,
    daily_cost_label: str,
    pro_forma_category: ProFormaCategory | str | None,
    tax_treatment: TaxTreatment | str,
) -> list[CashFlow]:
    """Return distinct fixed and per-day replacement-cost cashflows."""
    resolved_category, resolved_tax = normalize_cashflow_classification(
        pro_forma_category, tax_treatment
    )
    flows: list[CashFlow] = []
    if fixed_cost != 0.0:
        flows.append(
            CashFlow(
                amount=-abs(fixed_cost),
                date=booking_date,
                label=fixed_cost_label,
                is_cash=True,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax,
            )
        )
    if cost_per_day != 0.0:
        flows.append(
            CashFlow(
                amount=-abs(cost_per_day) * days,
                date=booking_date,
                label=daily_cost_label,
                is_cash=True,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax,
            )
        )
    return flows
