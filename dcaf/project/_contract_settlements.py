# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Private generation-contract allocation and validation engine."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import date
from math import fsum, isclose
from typing import assert_never

from dcaf.project.contracts import EnergyContract, GenerationSettlementEvent
from dcaf.shared.time import (
    PeriodWindow,
    elapsed_hours,
    period_start,
    period_window_event_date,
    time_delta_per_period,
)
from dcaf.shared.types import DayCountConvention, Period, TimingConvention
from dcaf.streams.generation import (
    GenerationStream,
    _GenerationSettlement,
    _calendar_period_windows,
    _generation_settlements,
    _prorated_generation_amount,
)


def settle_generation_contracts(
    generation: GenerationStream,
    contracts: dict[str, EnergyContract],
    *,
    frequency: Period,
    timing: TimingConvention,
    day_count_convention: DayCountConvention,
) -> tuple[
    dict[str, tuple[_GenerationSettlement, ...]],
    tuple[_GenerationSettlement, ...],
]:
    """Allocate generation to named contracts and return unused generation."""
    raw_by_contract = {
        name: _contract_settlements(
            generation,
            name,
            contract,
            frequency,
            timing,
            day_count_convention,
        )
        for name, contract in contracts.items()
    }
    requests = [
        settlement for settlements in raw_by_contract.values() for settlement in settlements
    ]
    remainder = _remainder_settlements(
        generation,
        requests,
        frequency,
        timing,
        day_count_convention,
    )
    by_contract = {
        name: tuple(
            _aggregate_settlements(
                settlements,
                generation,
                frequency,
                timing,
                day_count_convention,
            )
        )
        for name, settlements in raw_by_contract.items()
    }
    return by_contract, tuple(remainder)


def settlement_event(
    component_name: str,
    settlement: _GenerationSettlement,
) -> GenerationSettlementEvent:
    """Return pricing context for a fully delivered generation settlement."""
    delivered_mwh = settlement.amount_mwh
    available_mwh = settlement.available_mwh
    allocated_share = (
        0.0
        if isclose(available_mwh, 0.0, rel_tol=0.0, abs_tol=1e-12)
        else delivered_mwh / available_mwh
    )
    return GenerationSettlementEvent(
        date=settlement.date,
        period_start=settlement.period_start,
        period_end=settlement.period_end,
        available_mwh=available_mwh,
        requested_mwh=delivered_mwh,
        delivered_mwh=delivered_mwh,
        shortfall_mwh=0.0,
        allocated_generation_share=allocated_share,
        component_name=component_name,
    )


def _contract_settlements(
    generation: GenerationStream,
    name: str,
    contract: EnergyContract,
    frequency: Period,
    timing: TimingConvention,
    day_count_convention: DayCountConvention,
) -> list[_GenerationSettlement]:
    """Return requested settlements for one built-in contract."""
    match contract.quantity_mode:
        case "fixed_mwh_per_generation_event":
            settlements = _fixed_contract_settlements(
                generation,
                contract,
                frequency,
                timing,
                day_count_convention,
            )
        case "fraction_of_generation":
            assert contract.generation_share is not None
            settlements = []
            for source_index, entry in enumerate(generation.entries):
                if entry.amount_mwh < 0.0:
                    continue
                for available in _generation_settlements(
                    [entry],
                    frequency=frequency,
                    timing=timing,
                    day_count_convention=day_count_convention,
                    clip_start=contract.start,
                    clip_end=contract.end,
                ):
                    settlements.append(
                        dc_replace(
                            available,
                            source_index=source_index,
                            amount_mwh=available.amount_mwh * contract.generation_share,
                        )
                    )
        case "custom_mwh_generation_schedule":
            assert contract.requested_generation is not None
            settlements = _custom_contract_settlements(
                generation,
                name,
                contract,
                frequency,
                timing,
                day_count_convention,
            )
        case _:
            assert_never(contract.quantity_mode)
    return settlements


def _custom_contract_settlements(
    generation: GenerationStream,
    name: str,
    contract: EnergyContract,
    frequency: Period,
    timing: TimingConvention,
    day_count_convention: DayCountConvention,
) -> list[_GenerationSettlement]:
    """Allocate custom requested periods across overlapping generation sources."""
    assert contract.requested_generation is not None
    settlements: list[_GenerationSettlement] = []
    for requested in contract.requested_generation:
        requested_settlements = _generation_settlements(
            [requested],
            frequency=frequency,
            timing=timing,
            day_count_convention=day_count_convention,
            clip_start=contract.start,
            clip_end=contract.end,
        )
        for requested_settlement in requested_settlements:
            allocated = _allocate_contract_quantity(
                generation,
                requested_settlement.amount_mwh,
                requested_settlement.period_start,
                requested_settlement.period_end,
                frequency,
                timing,
                day_count_convention,
            )
            has_source = any(
                entry.period_end > requested_settlement.period_start
                and entry.period_start < requested_settlement.period_end
                for entry in generation.entries
            )
            if requested_settlement.amount_mwh > 0.0 and not allocated and not has_source:
                raise ValueError(
                    f"{name} custom MWh period "
                    f"[{requested_settlement.period_start}, {requested_settlement.period_end}) "
                    "overlaps no positive project generation sources; found 0"
                )
            settlements.extend(allocated)
    return settlements


def _fixed_contract_settlements(
    generation: GenerationStream,
    contract: EnergyContract,
    frequency: Period,
    timing: TimingConvention,
    day_count_convention: DayCountConvention,
) -> list[_GenerationSettlement]:
    """Allocate each fixed calendar quantity once across available sources."""
    assert contract.amount_mwh is not None
    assert contract.start is not None
    assert contract.end is not None
    assert contract.quantity_frequency is not None

    settlements: list[_GenerationSettlement] = []
    for quantity_start, quantity_end in _calendar_period_windows(
        contract.start,
        contract.end,
        contract.quantity_frequency,
    ):
        calendar_start = period_start(quantity_start, contract.quantity_frequency)
        calendar_end = calendar_start + time_delta_per_period(contract.quantity_frequency)
        if quantity_start == calendar_start and quantity_end == calendar_end:
            period_quantity = contract.amount_mwh
        else:
            period_quantity = (
                contract.amount_mwh
                * elapsed_hours(
                    quantity_start,
                    quantity_end,
                    day_count_convention,
                )
                / elapsed_hours(calendar_start, calendar_end, day_count_convention)
            )

        settlements.extend(
            _allocate_contract_quantity(
                generation,
                period_quantity,
                quantity_start,
                quantity_end,
                frequency,
                timing,
                day_count_convention,
            )
        )
    return settlements


def _allocate_contract_quantity(
    generation: GenerationStream,
    amount_mwh: float,
    start: date,
    end: date,
    frequency: Period,
    timing: TimingConvention,
    day_count_convention: DayCountConvention,
) -> list[_GenerationSettlement]:
    """Allocate one requested quantity pro rata over positive overlapping generation."""
    available = [
        settlement
        for settlement in _generation_settlements(
            generation.entries,
            frequency=frequency,
            timing=timing,
            day_count_convention=day_count_convention,
            clip_start=start,
            clip_end=end,
        )
        if settlement.amount_mwh > 0.0
    ]
    if not available:
        return []

    total_available = fsum(settlement.amount_mwh for settlement in available)
    allocated: list[float] = []
    settlements: list[_GenerationSettlement] = []
    for index, available_settlement in enumerate(available):
        quantity = (
            amount_mwh - fsum(allocated)
            if index == len(available) - 1
            else amount_mwh * available_settlement.amount_mwh / total_available
        )
        allocated.append(quantity)
        settlements.append(
            dc_replace(
                available_settlement,
                amount_mwh=quantity,
                available_mwh=available_settlement.amount_mwh,
            )
        )
    return settlements


def _aggregate_settlements(
    settlements: list[_GenerationSettlement],
    generation: GenerationStream,
    frequency: Period,
    timing: TimingConvention,
    day_count_convention: DayCountConvention,
) -> list[_GenerationSettlement]:
    """Combine validated requests by source and financial calendar period."""
    grouped: dict[tuple[int, date], list[_GenerationSettlement]] = {}
    for settlement in settlements:
        calendar_start = period_start(settlement.period_start, frequency)
        grouped.setdefault((settlement.source_index, calendar_start), []).append(settlement)

    aggregated: list[_GenerationSettlement] = []
    for (source_index, _calendar_start), group in grouped.items():
        effective_start = min(entry.period_start for entry in group)
        effective_end = max(entry.period_end for entry in group)
        window = PeriodWindow(start=effective_start, end=effective_end)
        aggregated.append(
            _GenerationSettlement(
                source_index=source_index,
                amount_mwh=fsum(entry.amount_mwh for entry in group),
                date=period_window_event_date(window, timing),
                period_start=effective_start,
                period_end=effective_end,
                label=group[0].label,
                available_mwh=_prorated_generation_amount(
                    generation.entries[source_index],
                    start=effective_start,
                    end=effective_end,
                    day_count_convention=day_count_convention,
                ),
            )
        )
    return sorted(aggregated, key=lambda entry: (entry.date, entry.source_index))


def _remainder_settlements(
    generation: GenerationStream,
    requests: list[_GenerationSettlement],
    frequency: Period,
    timing: TimingConvention,
    day_count_convention: DayCountConvention,
) -> list[_GenerationSettlement]:
    """Return unused and out-of-contract generation as period settlements."""
    remainder: list[_GenerationSettlement] = []
    for source_index, source in enumerate(generation.entries):
        source_requests = [request for request in requests if request.source_index == source_index]
        boundaries = {source.period_start, source.period_end}
        for window_start, window_end in _calendar_period_windows(
            source.period_start,
            source.period_end,
            frequency,
        ):
            boundaries.update((window_start, window_end))
        for request in source_requests:
            boundaries.update((request.period_start, request.period_end))
        ordered = sorted(boundaries)
        intervals = list(zip(ordered, ordered[1:]))
        available = _allocate_interval_amounts(
            source.amount_mwh,
            source.period_start,
            source.period_end,
            intervals,
            day_count_convention,
        )
        requested = [0.0 for _interval in intervals]
        for request in source_requests:
            request_amounts = _allocate_interval_amounts(
                request.amount_mwh,
                request.period_start,
                request.period_end,
                intervals,
                day_count_convention,
            )
            requested = [
                current + addition
                for current, addition in zip(requested, request_amounts, strict=True)
            ]

        for (interval_start, interval_end), available_mwh, requested_mwh in zip(
            intervals,
            available,
            requested,
            strict=True,
        ):
            if _request_exceeds_available(requested_mwh, available_mwh):
                raise ValueError(
                    "generation-linked contracts request "
                    f"{_format_mwh(requested_mwh)} MWh in "
                    f"[{interval_start}, {interval_end}), but only "
                    f"{_format_mwh(available_mwh)} MWh is available"
                )
            amount_mwh = available_mwh - requested_mwh
            if isclose(amount_mwh, 0.0, rel_tol=0.0, abs_tol=1e-12):
                continue
            window = PeriodWindow(start=interval_start, end=interval_end)
            remainder.append(
                _GenerationSettlement(
                    source_index=source_index,
                    amount_mwh=amount_mwh,
                    date=period_window_event_date(window, timing),
                    period_start=interval_start,
                    period_end=interval_end,
                    label=source.label,
                    available_mwh=available_mwh,
                )
            )
    return sorted(remainder, key=lambda entry: (entry.date, entry.source_index))


def _allocate_interval_amounts(
    amount: float,
    period_start_date: date,
    period_end_date: date,
    intervals: list[tuple[date, date]],
    day_count_convention: DayCountConvention,
) -> list[float]:
    """Allocate an amount over intersecting intervals with an exact final residual."""
    eligible_indices = [
        index
        for index, (interval_start, interval_end) in enumerate(intervals)
        if interval_end > period_start_date and interval_start < period_end_date
    ]
    result = [0.0 for _interval in intervals]
    if not eligible_indices:
        return result

    weights = [
        elapsed_hours(
            max(intervals[index][0], period_start_date),
            min(intervals[index][1], period_end_date),
            day_count_convention,
        )
        for index in eligible_indices
    ]
    total_weight = fsum(weights)
    allocated: list[float] = []
    for position, (index, weight) in enumerate(zip(eligible_indices, weights, strict=True)):
        if position == len(eligible_indices) - 1:
            interval_amount = amount - fsum(allocated)
        elif total_weight == 0.0:
            interval_amount = 0.0
        else:
            interval_amount = amount * weight / total_weight
        result[index] = interval_amount
        allocated.append(interval_amount)
    return result


def _request_exceeds_available(requested_mwh: float, available_mwh: float) -> bool:
    if available_mwh < 0.0:
        return requested_mwh > 0.0 or requested_mwh < available_mwh - 1e-9
    return requested_mwh - available_mwh > 1e-9


def _format_mwh(value: float) -> str:
    return f"{value:.1f}"
