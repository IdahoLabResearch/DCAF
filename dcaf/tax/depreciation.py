# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
Depreciation schedule utilities for tax modeling.

Provides IRS MACRS rate tables, an Excel-style variable declining balance
calculator, and factory functions for generating depreciation
``CashFlowStream`` objects.
"""

from collections.abc import Iterator, Sequence
from datetime import date
from typing import assert_never

from dcaf.shared.formatting import format_label
from dcaf.shared.time import time_delta_per_period
from dcaf.shared.types import (
    MACRSConvention,
    MACRSPropertyClass,
    Period,
    ProFormaCategory,
    TaxTreatment,
    VDBConvention,
    normalize_cashflow_classification,
)
from dcaf.shared.validation import validate_finite, validate_non_negative
from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.tax._macrs_tables import _MACRS_MID_QUARTER_RATES, _MACRS_RATES


def _validate_vdb_inputs(
    *,
    cost: float,
    salvage: float,
    life: float,
    start_period: float,
    end_period: float,
    factor: float,
) -> None:
    """Validate public VDB inputs using Excel-compatible bounds."""
    numeric_inputs = {
        "life": life,
        "start_period": start_period,
        "end_period": end_period,
        "factor": factor,
    }
    for name, value in numeric_inputs.items():
        validate_finite(value, name)

    validate_non_negative(cost, "cost")
    validate_non_negative(salvage, "salvage")
    if salvage > cost:
        raise ValueError("salvage must not exceed cost")
    if life <= 0:
        raise ValueError("life must be positive")
    validate_non_negative(start_period, "start_period")
    if end_period < start_period:
        raise ValueError("end_period must be greater than or equal to start_period")
    if factor <= 0:
        raise ValueError("factor must be positive")


def _vdb_segments(
    *, cost: float, salvage: float, life: float, factor: float, no_switch: bool
) -> Iterator[tuple[float, float, float]]:
    """Yield ``(segment_start, segment_end, depreciation_rate)`` tuples."""
    remaining_basis = cost
    elapsed = 0.0
    switched_to_straight_line = False

    while elapsed < life and remaining_basis > salvage:
        segment_length = min(1.0, life - elapsed)
        declining_balance_rate = remaining_basis * factor / life
        straight_line_rate = (remaining_basis - salvage) / (life - elapsed)

        if not no_switch and not switched_to_straight_line:
            switched_to_straight_line = straight_line_rate > declining_balance_rate

        rate = (
            declining_balance_rate
            if no_switch or not switched_to_straight_line
            else straight_line_rate
        )
        depreciation = max(0.0, min(rate * segment_length, remaining_basis - salvage))

        if depreciation <= 0.0:
            break

        yield elapsed, elapsed + segment_length, depreciation / segment_length
        remaining_basis -= depreciation
        elapsed += segment_length


def _placed_in_service_quarter(placed_in_service: date) -> int:
    """Return the calendar quarter (1-4) a placed-in-service date falls in.

    Shared by the MACRS and VDB mid-quarter paths so both derive the
    placed-in-service quarter identically. The quarter is read directly from
    the calendar month; the date is a point-in-time event, so no half-open
    interval adjustment applies (e.g. a 3/31 asset is in Q1, a 12/31 asset Q4).
    """
    return (placed_in_service.month - 1) // 3 + 1


def _vdb_convention_shift(convention: VDBConvention, placed_in_service: date) -> float:
    """Return the fractional first-period shift for convention-aware schedules."""
    match convention:
        case "none" | "best-of-half-year-mid-quarter":
            raise ValueError(f"Convention shift is undefined for '{convention}'")
        case "half-year":
            return 0.5
        case "mid-quarter":
            in_service_quarter = _placed_in_service_quarter(placed_in_service)
            return ((in_service_quarter - 0.5) * 2.0) / 8.0
        case _:
            assert_never(convention)


def _validate_schedule_dates(schedule_dates: Sequence[date]) -> tuple[date, ...]:
    """Validate and normalize an explicit date grid for schedule generation."""
    normalized = tuple(schedule_dates)
    for earlier, later in zip(normalized, normalized[1:], strict=False):
        if later <= earlier:
            raise ValueError("schedule_dates must be strictly increasing")
    return normalized


def _build_vdb_candidate_schedule(
    *,
    cost_basis: float,
    salvage_value: float,
    placed_in_service: date,
    life: int,
    dates: Sequence[date],
    factor: float,
    switch_to_straight_line: bool,
    convention: VDBConvention,
    terminal_catch_up: bool,
    label: str,
    pro_forma_category: ProFormaCategory | str | None,
    tax_treatment: TaxTreatment | str,
) -> CashFlowStream:
    """Build a single convention-aware VDB candidate schedule."""
    resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category, tax_treatment
    )
    shift = _vdb_convention_shift(convention, placed_in_service)
    target_total = cost_basis - salvage_value
    accumulated = 0.0
    entries: list[CashFlow] = []
    period_number = 0

    for current_date in dates:
        if current_date <= placed_in_service:
            continue
        period_number += 1

        if period_number == 1:
            start_period = 0.0
            end_period = 1.0 - shift
        elif terminal_catch_up and period_number == life + 1:
            depreciation = max(0.0, target_total - accumulated)
            if depreciation > 0.0:
                entries.append(
                    CashFlow(
                        amount=-depreciation,
                        date=current_date,
                        label=format_label(label, period_number),
                        is_cash=False,
                        pro_forma_category=resolved_category,
                        tax_treatment=resolved_tax_treatment,
                    )
                )
            break
        else:
            start_period = float(period_number - 1) - shift
            end_period = min(float(period_number) - shift, float(life))

        if start_period >= life or end_period <= start_period:
            continue

        depreciation = vdb(
            cost=cost_basis,
            salvage=salvage_value,
            life=float(life),
            start_period=start_period,
            end_period=end_period,
            factor=factor,
            no_switch=not switch_to_straight_line,
        )
        if depreciation <= 0.0:
            continue

        accumulated += depreciation
        entries.append(
            CashFlow(
                amount=-depreciation,
                date=current_date,
                label=format_label(label, period_number),
                is_cash=False,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
        )

    return CashFlowStream(entries)


def _candidate_npv(stream: CashFlowStream, *, valuation_rate: float, valuation_date: date) -> float:
    """Value a non-cash depreciation stream using the standard cashflow NPV helper."""
    return stream.apply(lambda cf: cf.replace(is_cash=True)).npv(
        rate=valuation_rate, valuation_date=valuation_date
    )


def vdb(
    cost: float,
    salvage: float,
    life: float,
    start_period: float,
    end_period: float,
    factor: float = 2.0,
    no_switch: bool = False,
) -> float:
    """
    Compute variable declining balance depreciation over an arbitrary period.

    Mirrors Excel's ``VDB`` function: depreciation is computed using a
    declining-balance factor and, unless ``no_switch`` is true, switches to
    straight-line when that yields a larger deduction.

    Parameters
    ----------
    cost : float
        Original asset basis.
    salvage : float
        Residual value at the end of the asset's life.
    life : float
        Asset life measured in depreciation periods.
    start_period : float
        Start of the depreciation interval, measured in the same period units
        as ``life``.
    end_period : float
        End of the depreciation interval, measured in the same period units as
        ``life``.
    factor : float, optional
        Declining-balance factor. ``2.0`` corresponds to double-declining
        balance.
    no_switch : bool, optional
        When true, remain on declining balance for the entire life instead of
        switching to straight-line.

    Returns
    -------
    float
        Depreciation amount for ``[start_period, end_period)``.

    Raises
    ------
    ValueError
        If the inputs are out of bounds.

    Examples
    --------
    >>> round(vdb(35000, 7500, 36, 10, 20), 2)
    8603.8
    >>> round(vdb(2400, 300, 10, 0, 0.875, factor=1.5), 2)
    315.0
    """
    _validate_vdb_inputs(
        cost=cost,
        salvage=salvage,
        life=life,
        start_period=start_period,
        end_period=end_period,
        factor=factor,
    )

    if cost == 0 or salvage == cost or end_period == start_period:
        return 0.0

    depreciation = 0.0
    for segment_start, segment_end, depreciation_rate in _vdb_segments(
        cost=cost, salvage=salvage, life=life, factor=factor, no_switch=no_switch
    ):
        overlap = max(0.0, min(end_period, segment_end) - max(start_period, segment_start))
        depreciation += depreciation_rate * overlap

    return depreciation


def macrs_schedule(
    cost_basis: float,
    placed_in_service: date,
    property_class: MACRSPropertyClass,
    convention: MACRSConvention = "half-year",
    label: str = "MACRS Depreciation",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.DEPRECIATION,
    tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
) -> CashFlowStream:
    """
    Generate a MACRS depreciation schedule.

    Parameters
    ----------
    cost_basis : float
        Depreciable basis (positive number). Flows will be negative.
    placed_in_service : date
        Date the asset is placed in service; first depreciation is on this date.
    property_class : MACRSPropertyClass
        IRS property class (3, 5, 7, 10, 15, or 20).
    convention : MACRSConvention, optional
        ``"half-year"`` (default) or ``"mid-quarter"``.  Under the
        mid-quarter convention the placed-in-service quarter is derived
        automatically from ``placed_in_service``.
    label : str, optional
        Label template. ``{n}`` is replaced with the 1-based period index.
    pro_forma_category : ProFormaCategory or str or None, optional
        Pro-forma category for each flow. Default is ``"depreciation"``.
    tax_treatment : TaxTreatment or str, optional
        Tax treatment for each flow. Default is ``"deductible"``.

    Returns
    -------
    CashFlowStream

    Examples
    --------
    >>> from datetime import date
    >>> stream = macrs_schedule(1_000_000, date(2030, 1, 1), 5)
    >>> [(cf.date, cf.amount) for cf in stream[:3]]
    [(datetime.date(2030, 1, 1), -200000.0), (datetime.date(2031, 1, 1), -320000.0), (datetime.date(2032, 1, 1), -192000.0)]
    >>> round(sum(cf.amount for cf in stream), 2)
    -1000000.0
    """
    match convention:
        case "half-year":
            rates = _MACRS_RATES[property_class]
        case "mid-quarter":
            quarter = _placed_in_service_quarter(placed_in_service)
            rates = _MACRS_MID_QUARTER_RATES[property_class][quarter]
        case _:
            assert_never(convention)
    resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category, tax_treatment
    )
    entries: list[CashFlow] = []
    for i, rate in enumerate(rates):
        dep_date = date(placed_in_service.year + i, placed_in_service.month, placed_in_service.day)
        flow_label = format_label(label, i + 1)
        entries.append(
            CashFlow(
                amount=-cost_basis * rate,
                date=dep_date,
                label=flow_label,
                is_cash=False,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
        )
    return CashFlowStream(entries)


def vdb_schedule(
    cost_basis: float,
    salvage_value: float,
    placed_in_service: date,
    life: int,
    frequency: Period = "year",
    factor: float = 2.0,
    switch_to_straight_line: bool = True,
    convention: VDBConvention = "none",
    schedule_dates: Sequence[date] | None = None,
    valuation_rate: float | None = None,
    valuation_date: date | None = None,
    terminal_catch_up: bool = False,
    label: str = "VDB Depreciation",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.DEPRECIATION,
    tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
) -> CashFlowStream:
    """
    Generate a variable declining balance depreciation schedule.

    This is a schedule-building facade over :func:`vdb`. ``life`` and
    ``frequency`` together define the depreciation period unit, matching Excel's
    requirement that ``life``, ``start_period``, and ``end_period`` share the
    same unit. For example, a 36-month schedule is represented as
    ``life=36, frequency="month"``.

    Parameters
    ----------
    cost_basis : float
        Original asset basis. Flows will be negative.
    salvage_value : float
        Residual value retained at the end of the asset's life.
    placed_in_service : date
        Date the asset is placed in service; first depreciation is on this date.
    life : int
        Number of depreciation periods.
    frequency : Period, optional
        Depreciation frequency. Default is ``"year"``.
    factor : float, optional
        Declining-balance factor. Default is ``2.0``.
    switch_to_straight_line : bool, optional
        When true (default), switch from declining balance to straight-line
        when straight-line produces a larger deduction.
    convention : VDBConvention, optional
        Schedule-construction convention. ``"none"`` preserves the legacy
        period-by-period schedule. ``"half-year"`` and ``"mid-quarter"``
        apply convention-aware fractional first periods. ``"best-of-half-year-mid-quarter"``
        values both candidates and returns the higher-NPV schedule.
    schedule_dates : Sequence[date] | None, optional
        Explicit dates for convention-aware schedule entries. When provided,
        depreciation flows are placed on these dates instead of
        ``placed_in_service + n * frequency``.
    valuation_rate : float | None, optional
        Annual discount rate used when ``convention`` is
        ``"best-of-half-year-mid-quarter"``.
    valuation_date : date | None, optional
        Valuation date used when ``convention`` is
        ``"best-of-half-year-mid-quarter"``.
    terminal_catch_up : bool, optional
        When true, allow an additional terminal period that books any residual
        depreciation needed to exactly reach ``cost_basis - salvage_value``.
    label : str, optional
        Label template. ``{n}`` is replaced with the 1-based period index.
    pro_forma_category : ProFormaCategory or str or None, optional
        Pro-forma category for each flow. Default is ``"depreciation"``.
    tax_treatment : TaxTreatment or str, optional
        Tax treatment for each flow. Default is ``"deductible"``.

    Returns
    -------
    CashFlowStream
        Non-cash depreciation flows, one per depreciation period.

    Raises
    ------
    ValueError
        If ``life`` is not a positive integer or if the depreciation inputs are
        otherwise invalid.

    Examples
    --------
    >>> from datetime import date
    >>> stream = vdb_schedule(
    ...     cost_basis=35000,
    ...     salvage_value=7500,
    ...     placed_in_service=date(2026, 1, 1),
    ...     life=36,
    ...     frequency="month",
    ... )
    >>> round(sum(-flow.amount for flow in stream.entries[10:20]), 2)
    8603.8
    >>> aligned = vdb_schedule(
    ...     cost_basis=1000,
    ...     salvage_value=0,
    ...     placed_in_service=date(2030, 12, 31),
    ...     life=5,
    ...     convention="mid-quarter",
    ...     schedule_dates=(
    ...         date(2030, 12, 31),
    ...         date(2031, 12, 31),
    ...         date(2032, 12, 31),
    ...         date(2033, 12, 31),
    ...         date(2034, 12, 31),
    ...         date(2035, 12, 31),
    ...         date(2036, 12, 31),
    ...     ),
    ...     terminal_catch_up=True,
    ... )
    >>> aligned.entries[0].date
    datetime.date(2031, 12, 31)
    """
    if isinstance(life, bool) or not isinstance(life, int) or life <= 0:
        raise ValueError("life must be a positive integer")

    _validate_vdb_inputs(
        cost=cost_basis,
        salvage=salvage_value,
        life=float(life),
        start_period=0.0,
        end_period=float(life),
        factor=factor,
    )

    if cost_basis == 0 or salvage_value == cost_basis:
        return CashFlowStream()

    if convention == "best-of-half-year-mid-quarter":
        if valuation_rate is None or valuation_date is None:
            raise ValueError(
                "valuation_rate and valuation_date are required for "
                "best-of-half-year-mid-quarter schedules"
            )
    elif valuation_rate is not None or valuation_date is not None:
        if convention == "none":
            raise ValueError(
                "valuation_rate and valuation_date are only supported for "
                "best-of-half-year-mid-quarter schedules"
            )

    if schedule_dates is not None:
        normalized_schedule_dates = _validate_schedule_dates(schedule_dates)
    else:
        normalized_schedule_dates = None

    if convention != "none":
        if normalized_schedule_dates is None:
            delta = time_delta_per_period(frequency)
            period_count = life + 1 + (1 if terminal_catch_up else 0)
            generated_dates: list[date] = []
            current_date = placed_in_service
            for _ in range(period_count):
                generated_dates.append(current_date)
                current_date += delta
            candidate_dates: Sequence[date] = tuple(generated_dates)
        else:
            candidate_dates = normalized_schedule_dates

        resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
            pro_forma_category, tax_treatment
        )

        if convention == "best-of-half-year-mid-quarter":
            half_year = _build_vdb_candidate_schedule(
                cost_basis=cost_basis,
                salvage_value=salvage_value,
                placed_in_service=placed_in_service,
                life=life,
                dates=candidate_dates,
                factor=factor,
                switch_to_straight_line=switch_to_straight_line,
                convention="half-year",
                terminal_catch_up=terminal_catch_up,
                label=label,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
            mid_quarter = _build_vdb_candidate_schedule(
                cost_basis=cost_basis,
                salvage_value=salvage_value,
                placed_in_service=placed_in_service,
                life=life,
                dates=candidate_dates,
                factor=factor,
                switch_to_straight_line=switch_to_straight_line,
                convention="mid-quarter",
                terminal_catch_up=terminal_catch_up,
                label=label,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
            assert valuation_rate is not None
            assert valuation_date is not None
            if _candidate_npv(
                half_year, valuation_rate=valuation_rate, valuation_date=valuation_date
            ) <= _candidate_npv(
                mid_quarter, valuation_rate=valuation_rate, valuation_date=valuation_date
            ):
                return half_year
            return mid_quarter

        return _build_vdb_candidate_schedule(
            cost_basis=cost_basis,
            salvage_value=salvage_value,
            placed_in_service=placed_in_service,
            life=life,
            dates=candidate_dates,
            factor=factor,
            switch_to_straight_line=switch_to_straight_line,
            convention=convention,
            terminal_catch_up=terminal_catch_up,
            label=label,
            pro_forma_category=resolved_category,
            tax_treatment=resolved_tax_treatment,
        )

    delta = time_delta_per_period(frequency)
    current_date = placed_in_service
    resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category, tax_treatment
    )
    entries: list[CashFlow] = []

    for period_number in range(1, life + 1):
        depreciation = vdb(
            cost=cost_basis,
            salvage=salvage_value,
            life=float(life),
            start_period=float(period_number - 1),
            end_period=float(period_number),
            factor=factor,
            no_switch=not switch_to_straight_line,
        )
        entries.append(
            CashFlow(
                amount=-depreciation,
                date=current_date,
                label=format_label(label, period_number),
                is_cash=False,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
        )
        current_date += delta

    return CashFlowStream(entries)
