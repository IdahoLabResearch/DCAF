# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Construction-debt-proceeds and debt-service build functions."""

from __future__ import annotations

from math import isclose

from dcaf.finance.amortization import AmortizationSchedule, _calendarize_amortization_schedule
from dcaf.finance.construction import ConstructionCashFlows
from dcaf.project._compiler import scheduling
from dcaf.project._compiler.context import AnalysisContext
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlowStream


def build_debt_proceeds(
    context: AnalysisContext, construction: ConstructionCashFlows
) -> CashFlowStream:
    """Build construction-debt funding entries at the underlying cost dates.

    Cash construction costs receive cash debt draws for the configured debt
    fraction. Capitalized construction interest receives an equal non-cash
    financing entry because it is added in full to permanent debt principal.
    Explicit debt schedules do not provide draw timing, so no proceeds are
    inferred for that path.
    """
    config = context.config
    if config.debt_schedule is not None:
        return CashFlowStream()

    debt = config.construction_debt
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


def build_debt(context: AnalysisContext, debt_proceeds: CashFlowStream) -> CashFlowStream:
    """Build the debt service stream from construction debt or schedule config.

    Handles two paths: construction-debt-based amortization (principal
    derived from recorded financing proceeds) and explicit schedule overrides.
    Internally generated payments are allocated across calendar periods;
    explicit schedule dates and amounts are preserved. Returns an empty
    stream when no debt is configured.
    """
    config = context.config

    # Explicit schedule override takes precedence
    if config.debt_schedule is not None:
        sched = config.debt_schedule
        if isinstance(sched, AmortizationSchedule):
            return scheduling.truncate_cashflow_schedule(
                CashFlowStream.from_streams(
                    sched.interest,
                    sched.principal,
                ).sort(),
                boundary=config.timeline.operations_end,
                component_name="debt_service",
            )
        return scheduling.truncate_cashflow_schedule(
            sched,
            boundary=config.timeline.operations_end,
            component_name="debt_service",
        )

    # Construction-debt path
    debt = config.construction_debt
    if debt is None:
        return CashFlowStream()

    # Derive principal from the recorded construction financing.
    if config.construction is None:
        raise ValueError(
            "construction_debt requires a construction schedule to derive "
            "the debt principal — call construction() first"
        )
    principal = debt_proceeds.sum()

    start = (
        debt.amortization_start
        if debt.amortization_start is not None
        else context.require_timeline_date("operations_start")
    )
    schedule = _calendarize_amortization_schedule(
        AmortizationSchedule.build(
            principal=principal,
            annual_rate=debt.amortization_rate,
            term=debt.amortization_term,
            start_date=start,
            frequency=debt.amortization_frequency,
        ),
        frequency=debt.amortization_frequency,
        timing=config.timeline.timing,
        day_count_convention=config.day_count_convention,
    )
    ops_end = config.timeline.operations_end
    return scheduling.truncate_cashflow_schedule(
        CashFlowStream.from_streams(schedule.interest, schedule.principal).sort(),
        boundary=ops_end,
        component_name="debt_service",
    )
