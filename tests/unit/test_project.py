# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from datetime import date
import csv

import pytest

from dcaf import GenerationPrice, EnergyProject
from dcaf.finance import ConstantRateEscalation
from dcaf.finance.amortization import AmortizationSchedule
from dcaf.finance.construction import ConstructionCashFlows
from dcaf.finance.opex import fixed_opex
from dcaf.project._compiler import ProjectCompiler
from dcaf.project.timeline import ProjectTimeline
from dcaf.shared.time import PeriodTruncationWarning, ScheduleTruncationWarning
from dcaf.shared.types import (
    DayCountConvention,
    InterestTreatment,
    ProFormaCategory,
    TaxTreatment,
)
from dcaf.streams import CashFlow, CashFlowStream, Generation, GenerationStream


@pytest.fixture
def financing_case():
    def _financing_case(
        *,
        debt_fraction: float,
        construction_interest_rate: float | None = None,
        interest_treatment: InterestTreatment = "capitalize",
    ):
        project = (
            EnergyProject()
            .generation(
                capacity_mw=1.0,
                capacity_factor=1.0,
                operations_start=date(2026, 1, 1),
                operations_end=date(2027, 1, 1),
            )
            .construction(
                overnight_cost=1_000.0,
                spend_profile="flat",
                construction_start=date(2025, 1, 1),
                period="year",
            )
            .fixed_opex(amount=100.0, frequency="year")
            .tax(rate=0.0)
        )
        if debt_fraction > 0.0:
            project = project.construction_financing(
                debt_fraction=debt_fraction,
                construction_interest_rate=construction_interest_rate,
                interest_treatment=interest_treatment,
                amortization_rate=0.0,
                amortization_term=1,
                amortization_frequency="year",
            )
        return project.analyze()

    return _financing_case


def test_energy_project_single_asset_workflow_builds_analysis_and_metrics():
    project = (
        EnergyProject()
        .wacc(
            debt_fraction=0.4,
            debt_cost=0.08,
            equity_fraction=0.6,
            equity_cost=0.12,
            tax_rate=0.21,
        )
        .generation(
            capacity_mw=100.0,
            capacity_factor=0.5,
            operations_start=date(2026, 1, 1),
            operations_end=date(2028, 1, 1),
        )
        .construction(
            overnight_cost=1_000.0,
            spend_profile="flat",
            construction_start=date(2025, 1, 1),
            period="year",
        )
        .fixed_opex(amount=100.0, frequency="year")
        .variable_cost(rate_per_unit=10.0)
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        .depreciation_macrs(property_class=5)
        .investment_tax_credit(rate=0.10)
        .tax(rate=0.21)
    )

    with pytest.warns(ScheduleTruncationWarning, match="depreciation schedule truncated"):
        analysis = project.analyze()

    assert analysis.generation.sum() == pytest.approx(100.0 * 0.5 * 8760.0 * 2)
    assert analysis.valuation is not None
    assert analysis.valuation.discount_rate == pytest.approx(0.09728)
    assert set(analysis.cashflow_components.keys()) >= {
        "construction",
        "revenue",
        "fixed_opex",
        "variable_cost",
        "depreciation",
        "itc",
        "project:tax_liability",
    }

    metrics = analysis.metrics(valuation_date=date(2025, 1, 1))
    assert metrics.discount_rate == pytest.approx(0.09728)
    assert metrics.discounted_generation > 0.0
    assert metrics.levelized_cost is not None
    if metrics.xirr is not None:
        assert analysis.cashflows.cash_only().npv(metrics.xirr, date(2025, 1, 1)) == pytest.approx(
            0.0, abs=1e-2
        )

    pro_forma = analysis.pro_forma(period="year")
    assert "Free Cash Flow to Equity" in pro_forma.row_map()
    assert "revenue" in pro_forma.row_map()
    assert len(pro_forma.periods) >= 2


@pytest.mark.parametrize("rate", [float("inf"), float("nan")])
def test_energy_project_itc_rejects_non_finite_rate(rate):
    with pytest.raises(ValueError, match="itc rate must be finite"):
        EnergyProject().investment_tax_credit(rate=rate)


def test_energy_project_itc_rejects_negative_rate():
    with pytest.raises(ValueError, match="itc rate must be non-negative"):
        EnergyProject().investment_tax_credit(rate=-0.30)


def test_energy_project_itc_allows_zero_rate():
    project = EnergyProject().investment_tax_credit(rate=0.0)

    assert project._config.itc_rate == pytest.approx(0.0)


class _EmptyGenerationLinkedPolicy:
    def cashflows(self, generation: GenerationStream) -> CashFlowStream:
        return CashFlowStream()


def test_component_key_collision_rejects_policy_and_generation_revenue():
    project = (
        EnergyProject()
        .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        .generation_linked_policy(name="revenue", policy=_EmptyGenerationLinkedPolicy())
    )

    with pytest.raises(
        ValueError,
        match="cashflow component key 'revenue'.*generation_revenue.*generation-linked policy 'revenue'",
    ):
        project.analyze()


def test_component_key_collision_rejects_policy_and_named_fixed_opex():
    project = (
        EnergyProject()
        .fixed_opex(name="operations", amount=100.0)
        .generation_linked_policy(
            name="fixed_opex:operations", policy=_EmptyGenerationLinkedPolicy()
        )
    )

    with pytest.raises(
        ValueError,
        match="cashflow component key 'fixed_opex:operations'.*fixed_opex 'operations'.*generation-linked policy",
    ):
        project.analyze()


def test_component_key_collision_rejects_policy_and_custom_cashflow_stream():
    project = (
        EnergyProject()
        .generation_linked_policy(name="custom:grant", policy=_EmptyGenerationLinkedPolicy())
        .add_cashflow_stream(name="custom:grant", stream=CashFlowStream())
    )

    with pytest.raises(
        ValueError,
        match="cashflow component key 'custom:grant'.*generation-linked policy.*custom cashflow stream",
    ):
        project.analyze()


def test_component_key_collision_rejects_custom_cashflow_and_tax_liability():
    project = (
        EnergyProject()
        .tax(rate=0.21)
        .add_cashflow_stream(name="project:tax_liability", stream=CashFlowStream())
    )

    with pytest.raises(
        ValueError,
        match="cashflow component key 'project:tax_liability'.*tax.*custom cashflow stream",
    ):
        project.analyze()


def test_non_colliding_component_configuration_compiles_normally():
    analysis = (
        EnergyProject()
        .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
        .fixed_opex(name="operations", amount=100.0)
        .generation_linked_policy(name="custom:bonus", policy=_EmptyGenerationLinkedPolicy())
        .add_cashflow_stream(name="custom:grant", stream=CashFlowStream())
        .analyze()
    )

    assert set(analysis.cashflow_components) == {"fixed_opex:operations", "custom:grant"}


def test_energy_project_real_carrying_charge_scenarios_have_expected_relationships():
    """The public project path preserves known cost and additivity relationships."""

    minimum_return_on_equity = 0.10
    interest_rate_pretax = 0.07
    tax_rate = 0.21
    debt_share = 0.5
    annual_inflation = 0.03
    construction_years = 5
    project_life_years = 40
    capacity_mw = 1_000.0
    capacity_factor = 0.93

    interest_rate_aftertax = interest_rate_pretax * (1.0 - tax_rate)
    wacc_aftertax = interest_rate_aftertax * debt_share + minimum_return_on_equity * (
        1.0 - debt_share
    )
    construction_start = date(2027, 1, 1)
    operations_start = date(2027 + construction_years, 1, 1)
    operations_end = date(2027 + construction_years + project_life_years, 1, 1)
    revenue_policy = ConstantRateEscalation(
        reference_date=operations_start,
        rate=annual_inflation,
    )

    def build_project(
        *,
        capex: float = 0.0,
        annual_opex: float = 0.0,
        variable_cost: float = 0.0,
    ) -> EnergyProject:
        project = (
            EnergyProject()
            .tax(rate=tax_rate)
            .generation(
                capacity_mw=capacity_mw,
                capacity_factor=capacity_factor,
                operations_start=operations_start,
                operations_end=operations_end,
                label="Uprate Generation",
            )
        )
        if capex != 0.0:
            project = (
                project.construction(
                    overnight_cost=capex,
                    spend_profile="upfront",
                    construction_start=construction_start,
                    period="year",
                    escalation_policy=ConstantRateEscalation(
                        reference_date=construction_start,
                        rate=annual_inflation,
                    ),
                )
                .depreciation_macrs(property_class=15)
                .investment_tax_credit(rate=0.0)
            )
        if annual_opex != 0.0:
            project = project.fixed_opex(
                amount=annual_opex,
                frequency="year",
                escalation_policy=ConstantRateEscalation(
                    reference_date=operations_start,
                    rate=annual_inflation,
                ),
            )
        if variable_cost != 0.0:
            project = project.variable_cost(rate_per_unit=variable_cost)
        return project

    def levelized_cost(project: EnergyProject) -> float:
        metrics = project.metrics(
            discount_rate=wacc_aftertax,
            valuation_date=operations_start,
            levelized_cost_escalation_policy=revenue_policy,
        )
        assert metrics.levelized_cost is not None
        return metrics.levelized_cost

    variable_cost_lcoe = levelized_cost(build_project(variable_cost=20.0))
    fixed_opex_lcoe = levelized_cost(build_project(annual_opex=162_936_000.0))
    capex_lcoe = levelized_cost(build_project(capex=660_000_000.0))
    combined_lcoe = levelized_cost(
        build_project(
            capex=660_000_000.0,
            annual_opex=162_936_000.0,
            variable_cost=20.0,
        )
    )

    # Variable cost stays nominally flat while the levelized price escalates at 3%,
    # so the equivalent starting price is below the $20/MWh cost rate.
    assert 0.0 < variable_cost_lcoe < 20.0
    # Fixed OPEX assumes 8,760 MWh/year while actual/actual generation includes
    # 8,784-MWh leap years, making the effective cost slightly less than $20/MWh.
    assert fixed_opex_lcoe == pytest.approx(20.0, abs=0.1)
    assert combined_lcoe == pytest.approx(
        capex_lcoe + fixed_opex_lcoe + variable_cost_lcoe,
    )


def test_energy_project_is_order_independent_across_sections():
    first = (
        EnergyProject()
        .generation(
            capacity_mw=25.0,
            capacity_factor=0.8,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(60.0))
        .fixed_opex(amount=50.0, frequency="year")
        .construction(
            overnight_cost=200.0,
            spend_profile="flat",
            construction_start=date(2025, 1, 1),
            period="year",
        )
        .tax(rate=0.21)
    )

    second = (
        EnergyProject()
        .tax(rate=0.21)
        .construction(
            overnight_cost=200.0,
            spend_profile="flat",
            construction_start=date(2025, 1, 1),
            period="year",
        )
        .fixed_opex(amount=50.0, frequency="year")
        .generation_revenue(price_policy=GenerationPrice.fixed(60.0))
        .generation(
            capacity_mw=25.0,
            capacity_factor=0.8,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
    )

    first_analysis = first.analyze()
    second_analysis = second.analyze()

    assert first_analysis.cashflows.sum() == pytest.approx(second_analysis.cashflows.sum())
    assert first_analysis.cashflows.count() == second_analysis.cashflows.count()
    assert first_analysis.metrics(0.10, date(2025, 1, 1)).npv == pytest.approx(
        second_analysis.metrics(0.10, date(2025, 1, 1)).npv
    )


def test_energy_project_metrics_treats_irr_overflow_as_non_convergence():
    """IRR overflow should be reported as non-convergence rather than an exception."""
    project = (
        EnergyProject(frequency="month")
        .generation(
            capacity_mw=100.0,
            capacity_factor=0.5,
            operations_start=date(2026, 1, 1),
            operations_end=date(2046, 1, 1),
        )
        .construction(
            overnight_cost=1_000_000.0,
            spend_profile="flat",
            construction_start=date(2025, 1, 1),
            period="month",
        )
        .fixed_opex(amount=100.0)
        .variable_cost(rate_per_unit=10.0)
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
    )

    analysis = project.analyze()

    with pytest.raises(ValueError, match="overflow encountered during iteration"):
        analysis.cashflows.cash_only().irr()

    metrics = analysis.metrics(discount_rate=0.10, valuation_date=date(2025, 1, 1))
    assert metrics.xirr is None


def test_energy_project_derives_debt_principal_from_construction():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .construction(
            overnight_cost=1_000.0,
            spend_profile="flat",
            construction_start=date(2025, 1, 1),
            period="year",
        )
        .construction_financing(
            debt_fraction=0.5,
            amortization_rate=0.10,
            amortization_term=1,
            amortization_frequency="year",
        )
    )

    analysis = project.analyze()
    debt_proceeds = analysis.cashflow_components["debt_proceeds"]
    cash_capex = analysis.cashflow_components["construction"].filter(
        pro_forma_category=ProFormaCategory.CAPITAL_COST,
        is_cash=True,
    )

    assert debt_proceeds.sum() == pytest.approx(500.0)
    assert debt_proceeds.cash_only().sum() == pytest.approx(500.0)
    assert [flow.date for flow in debt_proceeds] == [flow.date for flow in cash_capex]
    assert [flow.amount for flow in debt_proceeds] == pytest.approx(
        [-flow.amount * 0.5 for flow in cash_capex]
    )
    assert {flow.pro_forma_category for flow in debt_proceeds} == {
        ProFormaCategory.FINANCING_PROCEEDS
    }
    assert {flow.tax_treatment for flow in debt_proceeds} == {TaxTreatment.NONE}
    assert analysis.cashflow_components["debt_service"].sum() == pytest.approx(-550.0)

    row_map = analysis.pro_forma(period="year").row_map()
    assert sum(row_map["Financing Proceeds"]) == pytest.approx(500.0)


def test_debt_proceeds_use_explicit_bases_instead_of_cash_classification():
    project = EnergyProject().construction_financing(
        debt_fraction=0.5,
        amortization_rate=0.0,
        amortization_term=1,
    )
    compiler = ProjectCompiler.from_config(project._config)
    pro_rata_flow = CashFlow(-100.0, date(2025, 1, 1), is_cash=False)
    full_debt_flow = CashFlow(-20.0, date(2025, 2, 1), is_cash=True)
    construction = ConstructionCashFlows(
        total=CashFlowStream([pro_rata_flow, full_debt_flow]),
        pro_rata_debt_basis=CashFlowStream([pro_rata_flow]),
        full_debt_basis=CashFlowStream([full_debt_flow]),
    )

    proceeds = compiler.build_debt_proceeds(construction)

    assert [flow.amount for flow in proceeds] == pytest.approx([50.0, 20.0])
    assert [flow.is_cash for flow in proceeds] == [False, True]


def test_construction_financing_requires_construction():
    project = EnergyProject().construction_financing(
        debt_fraction=0.5,
        amortization_rate=0.0,
        amortization_term=1,
    )

    with pytest.raises(
        ValueError,
        match="construction_debt requires a construction schedule",
    ):
        project.analyze()


@pytest.mark.parametrize("debt_fraction", [0.0, 0.5, 1.0])
def test_levered_lcoe_matches_hand_case_and_is_invariant_to_costless_financing(
    financing_case,
    debt_fraction,
):
    analysis = financing_case(debt_fraction=debt_fraction)
    metrics = analysis.metrics(
        discount_rate=0.0,
        valuation_date=date(2025, 1, 1),
    )
    assert metrics.levelized_cost is not None

    # Independent hand case: ($1,000 capex + $100 opex) / 8,760 MWh.
    hand_calculated_lcoe = 1_100.0 / 8_760.0

    assert metrics.levelized_cost == pytest.approx(hand_calculated_lcoe)


@pytest.mark.parametrize("debt_fraction", [0.0, 0.5, 1.0])
def test_construction_debt_sources_uses_and_principal_reconcile(
    financing_case,
    debt_fraction,
):
    analysis = financing_case(debt_fraction=debt_fraction)
    construction_uses = abs(
        analysis.cashflow_components["construction"]
        .filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
        .cash_only()
        .sum()
    )
    debt_proceeds = (
        analysis.cashflow_components["debt_proceeds"]
        if "debt_proceeds" in analysis.cashflow_components.keys()
        else CashFlowStream()
    )
    debt_service = (
        analysis.cashflow_components["debt_service"]
        if "debt_service" in analysis.cashflow_components.keys()
        else CashFlowStream()
    )
    cash_debt_sources = debt_proceeds.cash_only().sum()
    implicit_equity_sources = construction_uses * (1.0 - debt_fraction)
    principal_repayments = abs(
        debt_service.filter(pro_forma_category=ProFormaCategory.FINANCING_PRINCIPAL).sum()
    )

    assert cash_debt_sources == pytest.approx(construction_uses * debt_fraction)
    assert cash_debt_sources + implicit_equity_sources == pytest.approx(construction_uses)
    assert principal_repayments == pytest.approx(debt_proceeds.sum())


def test_explicit_debt_schedule_is_an_explicit_opening_balance_without_proceeds():
    schedule = AmortizationSchedule.build(
        principal=100.0,
        annual_rate=0.0,
        term=1,
        start_date=date(2026, 1, 1),
        frequency="year",
    )

    analysis = EnergyProject().debt_schedule(schedule=schedule).analyze()

    principal_repayments = abs(
        analysis.cashflow_components["debt_service"]
        .filter(pro_forma_category=ProFormaCategory.FINANCING_PRINCIPAL)
        .sum()
    )
    assert principal_repayments == pytest.approx(100.0)
    assert "debt_proceeds" not in analysis.cashflow_components


def test_energy_project_generation_stream_sets_generation_explicitly():
    project = EnergyProject().generation_stream(
        stream=GenerationStream.from_capacity(10.0, 0.9, date(2030, 1, 1), 1)
    )
    assert project.analyze().generation.count() == 1


def test_energy_project_allows_custom_cashflow_streams():
    bonus = CashFlowStream([CashFlow(100.0, date(2026, 6, 1), label="Bonus")])
    project = EnergyProject().add_cashflow_stream(name="grant", stream=bonus)

    analysis = project.analyze()
    assert analysis.cashflow_components["grant"].sum() == pytest.approx(100.0)


def test_energy_project_day_count_convention_defaults_metrics_and_allows_override():
    stream = CashFlowStream(
        [
            CashFlow(-1000.0, date(2024, 1, 1)),
            CashFlow(1100.0, date(2025, 1, 1)),
        ]
    )
    default = (
        EnergyProject().discount_rate(rate=0.10).add_cashflow_stream(name="case", stream=stream)
    )
    no_leap = (
        EnergyProject(day_count_convention="actual/365-no-leap")
        .discount_rate(rate=0.10)
        .add_cashflow_stream(name="case", stream=stream)
    )
    fixed = (
        EnergyProject(day_count_convention="actual/365-fixed")
        .discount_rate(rate=0.10)
        .add_cashflow_stream(name="case", stream=stream)
    )

    default_metrics = default.metrics(valuation_date=date(2024, 1, 1))
    no_leap_metrics = no_leap.metrics(valuation_date=date(2024, 1, 1))
    fixed_metrics = fixed.metrics(valuation_date=date(2024, 1, 1))
    overridden = no_leap.metrics(
        valuation_date=date(2024, 1, 1),
        convention="actual/365-fixed",
    )

    assert default_metrics.day_count_convention == "actual/actual"
    assert no_leap_metrics.day_count_convention == "actual/365-no-leap"
    assert fixed_metrics.day_count_convention == "actual/365-fixed"
    assert default_metrics.npv == pytest.approx(0.0)
    assert default_metrics.xirr == pytest.approx(0.10)
    assert no_leap_metrics.npv == pytest.approx(0.0)
    assert no_leap_metrics.xirr == pytest.approx(0.10)
    assert fixed_metrics.npv == pytest.approx(-1000.0 + 1100.0 / (1.10 ** (366.0 / 365.0)))
    assert fixed_metrics.xirr != pytest.approx(no_leap_metrics.xirr)
    assert overridden.npv == pytest.approx(fixed_metrics.npv)


def test_energy_project_day_count_convention_affects_lcoe_and_generation_hours():
    def metrics(convention: DayCountConvention):
        return (
            EnergyProject(day_count_convention=convention)
            .discount_rate(rate=0.10)
            .generation(
                capacity_mw=1.0,
                capacity_factor=1.0,
                operations_start=date(2024, 1, 1),
                operations_end=date(2025, 1, 1),
            )
            .construction(overnight_cost=1000.0, cod_date=date(2024, 1, 1))
            .metrics(valuation_date=date(2024, 1, 1))
        )

    no_leap = metrics("actual/365-no-leap")
    fixed = metrics("actual/365-fixed")
    actual = metrics("actual/actual")

    assert no_leap.total_generation == pytest.approx(8760.0)
    assert fixed.total_generation == pytest.approx(8784.0)
    assert actual.total_generation == pytest.approx(8784.0)
    assert no_leap.levelized_cost is not None
    assert fixed.levelized_cost is not None
    assert actual.levelized_cost is not None
    assert no_leap.levelized_cost != pytest.approx(fixed.levelized_cost)
    assert no_leap.levelized_cost != pytest.approx(actual.levelized_cost)


def test_project_day_count_convention_affects_constant_rate_escalation():
    def fixed_opex_amount(convention: DayCountConvention) -> float:
        return (
            EnergyProject(day_count_convention=convention)
            .fixed_opex(
                amount=100.0,
                start=date(2024, 1, 1),
                periods=1,
                escalation=0.10,
                amount_reference_date=date(2024, 1, 1),
            )
            .analyze()
            .cashflow_components["fixed_opex"]
            .sum()
        )

    assert fixed_opex_amount("actual/365-no-leap") == pytest.approx(
        -100.0 * 1.10 ** (364.0 / 365.0)
    )
    assert fixed_opex_amount("actual/365-fixed") == pytest.approx(-110.0)
    assert fixed_opex_amount("actual/actual") == pytest.approx(-100.0 * 1.10 ** (365.0 / 366.0))


def test_project_timeline_operating_years_uses_configured_day_count():
    no_leap = ProjectTimeline(
        operations_start=date(2024, 1, 1),
        operations_end=date(2025, 1, 1),
        day_count_convention="actual/365-no-leap",
    )
    fixed = ProjectTimeline(
        operations_start=date(2024, 1, 1),
        operations_end=date(2025, 1, 1),
        day_count_convention="actual/365-fixed",
    )
    actual = ProjectTimeline(
        operations_start=date(2024, 1, 1),
        operations_end=date(2025, 1, 1),
        day_count_convention="actual/actual",
    )

    assert no_leap.operating_years == pytest.approx(1.0)
    assert fixed.operating_years == pytest.approx(366.0 / 365.0)
    assert actual.operating_years == pytest.approx(1.0)


def test_project_day_count_convention_affects_annual_schedule_proration():
    def opex_total(convention: DayCountConvention) -> float:
        return (
            EnergyProject(day_count_convention=convention)
            .generation(
                capacity_mw=0.0,
                capacity_factor=0.0,
                operations_start=date(2024, 1, 1),
                operations_end=date(2024, 3, 1),
            )
            .fixed_opex(amount=365.0, frequency="year")
            .analyze()
            .cashflow_components["fixed_opex"]
            .sum()
        )

    assert opex_total("actual/365-no-leap") == pytest.approx(-59.0)
    assert opex_total("actual/365-fixed") == pytest.approx(-60.0)
    assert opex_total("actual/actual") == pytest.approx(-365.0 * 60.0 / 366.0)


def test_project_day_count_convention_affects_construction_interest():
    def first_interest(convention: DayCountConvention) -> tuple[float, float]:
        analysis = (
            EnergyProject(day_count_convention=convention)
            .generation(
                capacity_mw=0.0,
                capacity_factor=0.0,
                operations_start=date(2024, 4, 1),
                operations_end=date(2025, 1, 1),
            )
            .construction(
                overnight_cost=1200.0,
                spend_profile="flat",
                construction_start=date(2024, 1, 1),
                construction_end=date(2024, 4, 1),
                period="month",
            )
            .construction_financing(
                debt_fraction=1.0,
                construction_interest_rate=0.12,
                interest_treatment="pay",
                amortization_rate=0.0,
                amortization_term=1,
            )
            .analyze()
        )
        construction = analysis.cashflow_components["construction"]
        first_draw = abs(next(cf.amount for cf in construction if cf.label == "Construction Spend"))
        first_interest_payment = next(
            cf.amount for cf in construction if cf.label == "Interest Payment"
        )
        return first_draw, first_interest_payment

    draw, no_leap_interest = first_interest("actual/365-no-leap")
    assert no_leap_interest == pytest.approx(-(draw * 0.12 * 28.0 / 365.0))
    assert first_interest("actual/365-fixed")[1] == pytest.approx(-(draw * 0.12 * 29.0 / 365.0))
    assert first_interest("actual/actual")[1] == pytest.approx(-(draw * 0.12 * 29.0 / 366.0))


def test_energy_project_generation_outage_reduces_modeled_generation_economics():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_outage(
            start=date(2026, 5, 1),
            end=date(2026, 5, 11),
            label="Refueling extension",
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        .variable_cost(rate_per_unit=5.0)
        .production_tax_credit(rate_per_unit=1.0, years=1)
    )

    analysis = project.analyze()
    base_mwh = 10.0 * 8760.0
    lost_mwh = 10.0 * 24.0 * 10.0
    net_mwh = base_mwh - lost_mwh

    assert analysis.generation.count() == 2
    assert analysis.generation.sum() == pytest.approx(net_mwh)
    assert analysis.cashflow_components["revenue"].sum() == pytest.approx(net_mwh * 50.0)
    assert analysis.cashflow_components["variable_cost"].sum() == pytest.approx(-net_mwh * 5.0)
    assert analysis.cashflow_components["ptc"].sum() == pytest.approx(net_mwh)
    assert analysis.metrics(discount_rate=0.08, valuation_date=date(2026, 1, 1)).levelized_cost


@pytest.mark.parametrize("capacity_factor", [-0.1, 1.1, float("inf"), float("nan")])
def test_energy_project_generation_outage_rejects_invalid_capacity_factor(
    capacity_factor,
):
    with pytest.raises(ValueError, match="capacity_factor must be between 0 and 1"):
        EnergyProject().generation_outage(
            start=date(2026, 5, 1),
            end=date(2026, 5, 11),
            capacity_mw=10.0,
            capacity_factor=capacity_factor,
        )


def test_energy_project_generation_outage_allows_zero_capacity_factor():
    project = EnergyProject().generation_outage(
        start=date(2026, 5, 1),
        end=date(2026, 5, 11),
        capacity_mw=10.0,
        capacity_factor=0.0,
    )

    assert project._config.generation_outages[0].capacity_factor == pytest.approx(0.0)


@pytest.mark.parametrize("rate_per_unit", [float("inf"), float("nan")])
def test_energy_project_ptc_rejects_non_finite_rate(rate_per_unit):
    with pytest.raises(ValueError, match="ptc rate_per_unit must be finite"):
        EnergyProject().production_tax_credit(rate_per_unit=rate_per_unit, years=1)


def test_energy_project_ptc_rejects_negative_rate():
    with pytest.raises(ValueError, match="ptc rate_per_unit must be non-negative"):
        EnergyProject().production_tax_credit(rate_per_unit=-1.0, years=1)


def test_energy_project_ptc_allows_zero_rate():
    project = EnergyProject().production_tax_credit(rate_per_unit=0.0, years=1)

    assert project._config.ptc is not None
    assert project._config.ptc.rate_per_unit == pytest.approx(0.0)


def test_energy_project_ptc_is_tax_credit_not_taxable_income():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(10.0))
        .production_tax_credit(rate_per_unit=2.0, years=1)
        .tax(rate=0.21)
    )

    analysis = project.analyze()
    revenue = analysis.cashflow_components["revenue"].sum()
    ptc_stream = analysis.cashflow_components["ptc"]

    assert ptc_stream.sum() == pytest.approx(1.0 * 8760.0 * 2.0)
    assert {flow.tax_treatment for flow in ptc_stream} == {TaxTreatment.NONE}
    assert analysis.taxable_income.sum() == pytest.approx(revenue)
    assert analysis.taxes.sum() == pytest.approx(-revenue * 0.21)


def test_energy_project_construction_outage_preserves_generation():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        .construction_outage(
            start=date(2025, 5, 1),
            end=date(2025, 5, 11),
            capacity_mw=1000.0,
            capacity_factor=0.92,
            fixed_cost=1_000_000.0,
            cost_per_day=10_000.0,
            lost_revenue_label="Construction outage lost revenue",
        )
    )

    analysis = project.analyze()
    lost_mwh = 1000.0 * 0.92 * 24.0 * 10.0
    expected_impact = -(lost_mwh * 50.0 + 1_000_000.0 + 10_000.0 * 10.0)
    impact = analysis.cashflow_components["construction_outage"]

    assert analysis.generation.sum() == pytest.approx(10.0 * 8760.0)
    assert impact.sum() == pytest.approx(expected_impact)
    assert {flow.pro_forma_category for flow in impact} == {ProFormaCategory.OPERATING_COST}
    assert {flow.tax_treatment for flow in impact} == {TaxTreatment.DEDUCTIBLE}


@pytest.mark.parametrize("capacity_factor", [-0.1, 1.1, float("inf"), float("nan")])
def test_energy_project_construction_outage_rejects_invalid_capacity_factor(
    capacity_factor,
):
    with pytest.raises(ValueError, match="capacity_factor must be between 0 and 1"):
        EnergyProject().construction_outage(
            start=date(2025, 5, 1),
            end=date(2025, 5, 11),
            capacity_mw=1000.0,
            capacity_factor=capacity_factor,
        )


def test_energy_project_construction_outage_allows_zero_capacity_factor():
    project = EnergyProject().construction_outage(
        start=date(2025, 5, 1),
        end=date(2025, 5, 11),
        capacity_mw=1000.0,
        capacity_factor=0.0,
    )

    assert project._config.construction_outages["default"].capacity_factor == pytest.approx(0.0)


def test_energy_project_construction_outage_explicit_price_and_lcoe():
    base_project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
    )
    outage_project = base_project.construction_outage(
        start=date(2025, 5, 1),
        end=date(2025, 5, 11),
        capacity_mw=1000.0,
        capacity_factor=0.92,
        sell_price_per_unit=45.0,
    )

    base_lcoe = base_project.metrics(discount_rate=0.08, valuation_date=date(2025, 1, 1))
    outage_lcoe = outage_project.metrics(discount_rate=0.08, valuation_date=date(2025, 1, 1))

    assert base_lcoe.levelized_cost is not None
    assert outage_lcoe.levelized_cost is not None
    assert outage_lcoe.levelized_cost > base_lcoe.levelized_cost


def test_energy_project_construction_outage_requires_price_or_market():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .construction_outage(
            start=date(2025, 5, 1),
            end=date(2025, 5, 11),
            capacity_mw=1000.0,
            capacity_factor=0.92,
        )
    )

    with pytest.raises(ValueError, match="requires sell_price_per_unit"):
        project.analyze()


def test_energy_project_construction_outage_models_two_construction_outages():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=220.0,
            capacity_factor=0.92,
            operations_start=date(2032, 1, 1),
            operations_end=date(2033, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(45.0))
        .construction_outage(
            name="refueling_1",
            start=date(2028, 4, 1),
            end=date(2028, 4, 11),
            capacity_mw=1000.0,
            capacity_factor=0.92,
        )
        .construction_outage(
            name="refueling_2",
            start=date(2030, 10, 1),
            end=date(2030, 10, 11),
            capacity_mw=1000.0,
            capacity_factor=0.92,
        )
    )

    analysis = project.analyze()
    expected_per_outage = -(1000.0 * 0.92 * 24.0 * 10.0 * 45.0)

    assert analysis.generation.sum() == pytest.approx(220.0 * 0.92 * 8784.0)
    assert analysis.cashflow_components["construction_outage:refueling_1"].sum() == (
        pytest.approx(expected_per_outage)
    )
    assert analysis.cashflow_components["construction_outage:refueling_2"].sum() == (
        pytest.approx(expected_per_outage)
    )


def test_energy_project_prorates_partial_operating_periods_from_generation_dates():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=0.5,
            operations_start=date(2026, 6, 1),
            operations_end=date(2027, 9, 1),
        )
        .fixed_opex(amount=120.0, frequency="year")
    )

    analysis = project.analyze()
    exclusive_end = date(2027, 9, 1)
    expected_operating_years = (exclusive_end - date(2026, 6, 1)).days / 365.0
    expected_fractional_year = (exclusive_end - date(2027, 6, 1)).days / 365.0
    expected_generation = 10.0 * 0.5 * 8760.0 * (1.0 + expected_fractional_year)
    expected_opex = -120.0 * (1.0 + expected_fractional_year)

    assert analysis.timeline.operating_years == pytest.approx(expected_operating_years)
    assert analysis.generation.count() == 2
    assert analysis.generation.sum() == pytest.approx(expected_generation)
    assert analysis.generation.entries[0].period_start == date(2026, 6, 1)
    assert analysis.generation.entries[0].period_end == date(2027, 6, 1)
    assert analysis.generation.entries[1].period_start == date(2027, 6, 1)
    assert analysis.generation.entries[1].period_end == exclusive_end
    assert analysis.cashflow_components["fixed_opex"].sum() == pytest.approx(expected_opex)


def test_energy_project_explicit_fractional_periods_use_complete_day_truncation():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            start=date(2026, 1, 1),
            periods=1.5,
            frequency="year",
        )
        .fixed_opex(
            amount=365.0,
            start=date(2026, 1, 1),
            periods=1.5,
            frequency="year",
        )
    )

    with pytest.warns(PeriodTruncationWarning) as caught:
        analysis = project.analyze()

    assert len(caught) == 2
    assert all("last included date is 2027-07-01" in str(item.message) for item in caught)
    assert analysis.generation.count() == 2
    assert analysis.generation.sum() == pytest.approx((365 + 182) * 24)
    assert analysis.cashflow_components["fixed_opex"].sum() == pytest.approx(-547.0)


def test_project_pro_forma_can_write_csv(tmp_path):
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
    )

    pro_forma = project.analyze().pro_forma(period="year")
    output_path = tmp_path / "pro_forma.csv"

    pro_forma.to_csv(output_path)

    with output_path.open(newline="", encoding="utf-8") as csvfile:
        rows = list(csv.reader(csvfile))
    row_map = {row[0]: row[1:] for row in rows[1:]}

    assert rows[0] == ["Row", "2026-01-01"]
    assert rows[1][0] == "Revenues"
    assert float(row_map["Revenues"][0]) == pytest.approx(1.0 * 8760.0 * 50.0)
    assert float(row_map["Free Cash Flow to Equity"][0]) == pytest.approx(1.0 * 8760.0 * 50.0)
    assert float(row_map["revenue"][0]) == pytest.approx(1.0 * 8760.0 * 50.0)


def test_project_pro_forma_groups_categories_and_computes_subtotals():
    schedule = AmortizationSchedule.build(
        principal=100.0,
        annual_rate=0.10,
        term=1,
        start_date=date(2026, 1, 1),
        frequency="year",
    )
    project = (
        EnergyProject()
        .add_cashflow_stream(
            name="revenue",
            stream=CashFlowStream(
                [
                    CashFlow(
                        200.0,
                        date(2026, 1, 1),
                        label="Revenue",
                        pro_forma_category="revenue",
                        tax_treatment="taxable",
                    )
                ]
            ),
        )
        .add_cashflow_stream(
            name="opex",
            stream=CashFlowStream(
                [
                    CashFlow(
                        -50.0,
                        date(2026, 2, 1),
                        label="Operating Cost",
                        pro_forma_category="operating_cost",
                        tax_treatment="deductible",
                    )
                ]
            ),
        )
        .add_cashflow_stream(
            name="capex",
            stream=CashFlowStream(
                [
                    CashFlow(
                        -100.0,
                        date(2026, 3, 1),
                        label="Capital Cost",
                        pro_forma_category="capital_cost",
                    )
                ]
            ),
        )
        .add_cashflow_stream(
            name="depreciation",
            stream=CashFlowStream(
                [
                    CashFlow(
                        -20.0,
                        date(2026, 12, 31),
                        label="Depreciation",
                        is_cash=False,
                        pro_forma_category="depreciation",
                        tax_treatment="deductible",
                    )
                ]
            ),
        )
        .add_cashflow_stream(
            name="tax_credit",
            stream=CashFlowStream(
                [
                    CashFlow(
                        10.0,
                        date(2026, 12, 31),
                        label="Tax Credit",
                        pro_forma_category="tax_credit",
                    )
                ]
            ),
        )
        .debt_schedule(schedule=schedule)
        .tax(rate=0.20)
    )

    analysis = project.analyze()
    pro_forma = analysis.pro_forma(period="year")
    row_names = [row.name for row in pro_forma.rows]
    expected_summary_rows = [
        "Revenues",
        "Operating Costs",
        "EBITDA",
        "Depreciation",
        "EBIT",
        "Taxes",
        "Tax Credits",
        "Capital Costs",
        "Free Cash Flow to the Firm",
        "Financing Proceeds",
        "Financing Interest",
        "Interest Tax Shield",
        "Financing Principal",
        "Free Cash Flow to Equity",
    ]

    assert pro_forma.periods == (date(2026, 1, 1),)
    assert row_names[: len(expected_summary_rows)] == expected_summary_rows

    row_map = pro_forma.row_map()
    assert row_map["Revenues"] == pytest.approx((200.0,))
    assert row_map["Operating Costs"] == pytest.approx((-50.0,))
    assert row_map["EBITDA"] == pytest.approx((150.0,))
    assert row_map["Depreciation"] == pytest.approx((-20.0,))
    assert row_map["EBIT"] == pytest.approx((130.0,))
    assert row_map["Taxes"] == pytest.approx((-24.0,))
    assert row_map["Tax Credits"] == pytest.approx((10.0,))
    assert row_map["Capital Costs"] == pytest.approx((-100.0,))
    assert row_map["Free Cash Flow to the Firm"] == pytest.approx((34.0,))
    assert row_map["Financing Proceeds"] == pytest.approx((0.0,))
    assert row_map["Financing Interest"] == pytest.approx((-10.0,))
    assert row_map["Interest Tax Shield"] == pytest.approx((2.0,))
    assert row_map["Financing Principal"] == pytest.approx((-100.0,))
    assert row_map["Free Cash Flow to Equity"] == pytest.approx((-74.0,))

    assert row_map["revenue"] == pytest.approx((200.0,))
    assert row_map["opex"] == pytest.approx((-50.0,))
    assert row_map["capex"] == pytest.approx((-100.0,))
    assert row_map["depreciation"] == pytest.approx((-20.0,))
    assert row_map["tax_credit"] == pytest.approx((10.0,))
    assert row_map["debt_service"] == pytest.approx((-110.0,))
    assert "debt_proceeds" not in row_map
    assert "project:tax_liability" not in row_map


@pytest.mark.parametrize(
    ("annual_rate", "expected_refund", "expected_shield"),
    [(0.0, 20.0, 0.0), (0.10, 22.0, 2.0)],
)
def test_project_pro_forma_interest_tax_shield_preserves_refund_policy(
    annual_rate,
    expected_refund,
    expected_shield,
):
    schedule = AmortizationSchedule.build(
        principal=100.0,
        annual_rate=annual_rate,
        term=1,
        start_date=date(2026, 1, 1),
        frequency="year",
    )
    analysis = (
        EnergyProject()
        .add_cashflow_stream(
            name="deduction",
            stream=CashFlowStream(
                [
                    CashFlow(
                        -100.0,
                        date(2026, 1, 1),
                        pro_forma_category="operating_cost",
                        tax_treatment="deductible",
                    )
                ]
            ),
        )
        .debt_schedule(schedule=schedule)
        .tax(rate=0.20, allow_refund=True)
        .analyze()
    )

    row_map = analysis.pro_forma().row_map()

    assert row_map["Taxes"] == pytest.approx((expected_refund,))
    assert row_map["Financing Interest"] == pytest.approx((-100.0 * annual_rate,))
    assert row_map["Interest Tax Shield"] == pytest.approx((expected_shield,))


# --- Multiple named cost items ---


def test_energy_project_supports_multiple_named_fixed_opex_items():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .fixed_opex(name="om", amount=100.0, label="O&M")
        .fixed_opex(name="insurance", amount=50.0, label="Insurance")
        .fixed_opex(name="land_lease", amount=30.0, label="Land Lease")
    )

    analysis = project.analyze()
    assert "fixed_opex:om" in analysis.cashflow_components
    assert "fixed_opex:insurance" in analysis.cashflow_components
    assert "fixed_opex:land_lease" in analysis.cashflow_components
    assert analysis.cashflow_components["fixed_opex:om"].sum() == pytest.approx(-100.0)
    assert analysis.cashflow_components["fixed_opex:insurance"].sum() == pytest.approx(-50.0)
    assert analysis.cashflow_components["fixed_opex:land_lease"].sum() == pytest.approx(-30.0)


def test_energy_project_default_named_fixed_opex_uses_backward_compatible_key():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .fixed_opex(amount=100.0, frequency="year")
    )

    analysis = project.analyze()
    assert "fixed_opex" in analysis.cashflow_components
    assert analysis.cashflow_components["fixed_opex"].sum() == pytest.approx(-100.0)


@pytest.mark.parametrize("periods", [-1.0, 0.0])
def test_energy_project_fixed_opex_rejects_non_positive_periods(periods):
    with pytest.raises(ValueError, match="fixed_opex periods must be positive"):
        EnergyProject().fixed_opex(
            amount=100.0,
            start=date(2026, 1, 1),
            periods=periods,
        )


def test_energy_project_fixed_opex_allows_positive_fractional_periods():
    project = EnergyProject().fixed_opex(
        amount=100.0,
        start=date(2026, 1, 1),
        periods=1.5,
    )

    assert project._config.fixed_opex_items["default"].periods == pytest.approx(1.5)


def test_energy_project_supports_multiple_named_variable_cost_items():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .variable_cost(rate_per_unit=5.0, name="fuel")
        .variable_cost(rate_per_unit=2.0, name="water")
    )

    analysis = project.analyze()
    gen_mwh = analysis.generation.sum()
    assert "variable_cost:fuel" in analysis.cashflow_components
    assert "variable_cost:water" in analysis.cashflow_components
    assert analysis.cashflow_components["variable_cost:fuel"].sum() == pytest.approx(-5.0 * gen_mwh)
    assert analysis.cashflow_components["variable_cost:water"].sum() == pytest.approx(
        -2.0 * gen_mwh
    )


def test_energy_project_variable_cost_ignores_rate_sign():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .variable_cost(rate_per_unit=-5.0)
    )

    analysis = project.analyze()
    gen_mwh = analysis.generation.sum()
    assert analysis.cashflow_components["variable_cost"].sum() == pytest.approx(-5.0 * gen_mwh)


def test_energy_project_named_fixed_opex_replaces_by_name():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .fixed_opex(name="om", amount=100.0)
        .fixed_opex(name="om", amount=200.0)
    )

    analysis = project.analyze()
    assert "fixed_opex:om" in analysis.cashflow_components
    assert analysis.cashflow_components["fixed_opex:om"].sum() == pytest.approx(-200.0)


def test_energy_project_explicit_zero_escalation_overrides_project_default():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2028, 1, 1),
        )
        .default_escalation(rate=0.10)
        .fixed_opex(name="flat", amount=100.0, escalation=0.0)
        .fixed_opex(name="default", amount=100.0)
    )

    analysis = project.analyze()

    assert analysis.cashflow_components["fixed_opex:flat"].sum() == pytest.approx(-200.0)
    assert analysis.cashflow_components["fixed_opex"].sum() == pytest.approx(-230.94, abs=0.1)


def test_energy_project_fixed_opex_matches_standalone_booking_convention():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2029, 1, 1),
        )
        .fixed_opex(amount=500_000.0, frequency="year", escalation=0.025)
    )
    standalone = fixed_opex(
        amount=500_000.0,
        start=date(2026, 1, 1),
        periods=3,
        frequency="year",
        escalation=0.025,
    )

    project_opex = project.analyze().cashflow_components["fixed_opex"]

    assert [flow.date for flow in project_opex] == [flow.date for flow in standalone]
    assert [flow.amount for flow in project_opex] == pytest.approx(
        [flow.amount for flow in standalone]
    )


# --- Debt principal derivation: capitalize vs pay ---


def test_energy_project_debt_principal_is_not_tax_deductible():
    revenue = CashFlowStream(
        [
            CashFlow(
                200.0,
                date(2026, 1, 1),
                label="Revenue",
                pro_forma_category=ProFormaCategory.REVENUE,
                tax_treatment=TaxTreatment.TAXABLE,
            )
        ]
    )
    schedule = AmortizationSchedule.build(
        principal=100.0,
        annual_rate=0.10,
        term=1,
        start_date=date(2026, 1, 1),
        frequency="year",
    )
    project = (
        EnergyProject()
        .add_cashflow_stream(name="revenue", stream=revenue)
        .debt_schedule(schedule=schedule)
        .tax(rate=0.21)
    )

    analysis = project.analyze()
    debt_service = analysis.cashflow_components["debt_service"]

    assert debt_service.sum() == pytest.approx(-110.0)
    assert debt_service.filter(
        pro_forma_category=ProFormaCategory.FINANCING_INTEREST
    ).sum() == pytest.approx(-10.0)
    assert debt_service.filter(
        pro_forma_category=ProFormaCategory.FINANCING_PRINCIPAL
    ).sum() == pytest.approx(-100.0)
    assert analysis.taxable_income.sum() == pytest.approx(190.0)
    assert analysis.taxes.sum() == pytest.approx(-39.9)


def test_energy_project_debt_principal_capitalize_vs_pay():
    """Capitalized interest increases the permanent debt principal; 'pay' does not."""

    def _build(interest_treatment):
        return (
            EnergyProject()
            .generation(
                capacity_mw=1.0,
                capacity_factor=1.0,
                operations_start=date(2026, 1, 1),
                operations_end=date(2027, 1, 1),
            )
            .construction(
                overnight_cost=1_000.0,
                spend_profile="flat",
                construction_start=date(2025, 1, 1),
                period="month",
            )
            .construction_financing(
                debt_fraction=0.5,
                construction_interest_rate=0.10,
                interest_treatment=interest_treatment,
                amortization_rate=0.05,
                amortization_term=1,
                amortization_frequency="year",
            )
            .analyze()
        )

    capitalize_analysis = _build("capitalize")
    pay_analysis = _build("pay")

    cap_debt = abs(capitalize_analysis.cashflow_components["debt_service"].sum())
    pay_debt = abs(pay_analysis.cashflow_components["debt_service"].sum())
    cap_proceeds = capitalize_analysis.cashflow_components["debt_proceeds"]
    pay_proceeds = pay_analysis.cashflow_components["debt_proceeds"]

    # Both should have debt service
    assert cap_debt > 0.0
    assert pay_debt > 0.0

    # Capitalize mode rolls interest into the principal, producing larger debt service
    assert cap_debt > pay_debt

    cap_principal = abs(
        capitalize_analysis.cashflow_components["debt_service"]
        .filter(pro_forma_category=ProFormaCategory.FINANCING_PRINCIPAL)
        .sum()
    )
    capitalized_interest_stream = capitalize_analysis.cashflow_components["construction"].filter(
        is_cash=False
    )
    capitalized_interest = abs(capitalized_interest_stream.sum())
    capitalized_interest_financing = cap_proceeds.filter(is_cash=False)
    paid_interest_stream = pay_analysis.cashflow_components["construction"].filter(
        pro_forma_category=ProFormaCategory.FINANCING_INTEREST
    )
    pay_principal = abs(
        pay_analysis.cashflow_components["debt_service"]
        .filter(pro_forma_category=ProFormaCategory.FINANCING_PRINCIPAL)
        .sum()
    )
    assert cap_proceeds.sum() == pytest.approx(cap_principal)
    assert capitalized_interest_financing.sum() == pytest.approx(capitalized_interest)
    assert capitalized_interest_stream.entries
    assert all(not flow.is_cash for flow in capitalized_interest_stream)
    assert paid_interest_stream.entries
    assert all(flow.is_cash for flow in paid_interest_stream)
    assert paid_interest_stream.sum() < 0.0
    assert [flow.date for flow in capitalized_interest_financing] == [
        flow.date for flow in capitalized_interest_stream
    ]
    assert [flow.amount for flow in capitalized_interest_financing] == pytest.approx(
        [-flow.amount for flow in capitalized_interest_stream]
    )
    assert pay_proceeds.filter(is_cash=False).sum() == pytest.approx(0.0)

    # Verify the pay mode principal derives only from cash capex
    construction = pay_analysis.cashflow_components["construction"]
    capex = construction.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
    cash_basis = abs(capex.cash_only().sum())
    expected_pay_principal = cash_basis * 0.5
    assert pay_principal == pytest.approx(expected_pay_principal)
    assert pay_proceeds.sum() == pytest.approx(expected_pay_principal)
    assert pay_debt == pytest.approx(expected_pay_principal * 1.05)


# --- Valuation configuration ---


def test_energy_project_wacc_is_independent_from_project_tax_rate():
    project = (
        EnergyProject()
        .tax(rate=0.21)
        .wacc(
            debt_fraction=0.4,
            debt_cost=0.08,
            equity_fraction=0.6,
            equity_cost=0.12,
            tax_rate=0.30,
        )
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
    )

    analysis = project.analyze()
    assert analysis.valuation is not None
    assert analysis.valuation.discount_rate == pytest.approx(0.12 * 0.6 + 0.08 * 0.4 * (1 - 0.30))
    assert analysis.tax_rate == pytest.approx(0.21)


def test_energy_project_wacc_rejects_negative_tax_rate():
    with pytest.raises(ValueError, match="tax_rate must be non-negative"):
        EnergyProject().wacc(
            debt_fraction=0.4,
            debt_cost=0.08,
            equity_fraction=0.6,
            equity_cost=0.12,
            tax_rate=-0.21,
        )


def test_energy_project_wacc_allows_zero_tax_rate():
    project = EnergyProject().wacc(
        debt_fraction=0.4,
        debt_cost=0.08,
        equity_fraction=0.6,
        equity_cost=0.12,
        tax_rate=0.0,
    )

    assert project._config.valuation is not None
    assert project._config.valuation.discount_rate == pytest.approx(0.12 * 0.6 + 0.08 * 0.4)


def test_energy_project_discount_rate_sets_default_metrics_rate():
    project = (
        EnergyProject()
        .discount_rate(rate=0.10)
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
    )

    analysis = project.analyze()
    assert analysis.valuation is not None
    assert analysis.valuation.discount_rate == pytest.approx(0.10)
    assert analysis.metrics().discount_rate == pytest.approx(0.10)


@pytest.mark.parametrize("rate", [-1.0, -1.1])
def test_energy_project_discount_rate_rejects_invalid_negative_rate(rate):
    with pytest.raises(ValueError, match="discount_rate must be greater than -1.0"):
        EnergyProject().discount_rate(rate=rate)


def test_energy_project_discount_rate_allows_negative_rate_above_minus_one():
    project = EnergyProject().discount_rate(rate=-0.01)

    assert project._config.valuation is not None
    assert project._config.valuation.discount_rate == pytest.approx(-0.01)


def test_energy_project_metrics_override_builder_valuation_rate():
    project = (
        EnergyProject()
        .discount_rate(rate=0.10)
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .construction(
            overnight_cost=100.0,
            spend_profile="flat",
            construction_start=date(2025, 1, 1),
            period="year",
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
    )

    analysis = project.analyze()
    assert analysis.metrics().discount_rate == pytest.approx(0.10)
    assert analysis.metrics(discount_rate=0.12).discount_rate == pytest.approx(0.12)


@pytest.mark.parametrize("discount_rate", [-1.1, -1.0])
def test_energy_project_metrics_override_rejects_invalid_negative_discount_rate(
    discount_rate,
):
    analysis = (
        EnergyProject()
        .discount_rate(rate=0.10)
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        .analyze()
    )

    with pytest.raises(ValueError, match="discount_rate must be greater than -1.0"):
        analysis.metrics(discount_rate=discount_rate)


def test_energy_project_metrics_override_allows_negative_discount_rate_above_minus_one():
    analysis = (
        EnergyProject()
        .discount_rate(rate=0.10)
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        .analyze()
    )

    assert analysis.metrics(discount_rate=-0.01).discount_rate == pytest.approx(-0.01)


@pytest.mark.parametrize("tax_rate", [float("inf"), float("nan")])
def test_energy_project_tax_rejects_non_finite_rate(tax_rate):
    with pytest.raises(ValueError, match="tax rate must be finite"):
        EnergyProject().tax(rate=tax_rate)


def test_energy_project_tax_rejects_negative_rate():
    with pytest.raises(ValueError, match="tax rate must be non-negative"):
        EnergyProject().tax(rate=-0.21)


def test_energy_project_tax_allows_zero_rate():
    project = (
        EnergyProject()
        .tax(rate=0.0)
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
    )

    analysis = project.analyze()
    assert analysis.tax_rate == pytest.approx(0.0)
    assert analysis.cashflow_components["project:tax_liability"].sum() == pytest.approx(0.0)


@pytest.mark.parametrize("life", [-1, 0])
def test_energy_project_vdb_depreciation_rejects_non_positive_life(life):
    with pytest.raises(ValueError, match="VDB life must be positive"):
        EnergyProject().depreciation_vdb(life=life)


def test_energy_project_vdb_depreciation_allows_positive_life():
    project = EnergyProject().depreciation_vdb(life=5)

    assert project._config.depreciation is not None
    assert project._config.depreciation.life == 5


@pytest.mark.parametrize("salvage_value", [float("inf"), float("nan")])
def test_energy_project_vdb_depreciation_rejects_non_finite_salvage_value(
    salvage_value,
):
    with pytest.raises(ValueError, match="VDB salvage_value must be finite"):
        EnergyProject().depreciation_vdb(life=5, salvage_value=salvage_value)


def test_energy_project_vdb_depreciation_rejects_negative_salvage_value():
    with pytest.raises(ValueError, match="VDB salvage_value must be non-negative"):
        EnergyProject().depreciation_vdb(life=5, salvage_value=-1.0)


def test_energy_project_vdb_depreciation_allows_zero_salvage_value():
    project = EnergyProject().depreciation_vdb(life=5, salvage_value=0.0)

    assert project._config.depreciation is not None
    assert project._config.depreciation.salvage_value == pytest.approx(0.0)


@pytest.mark.parametrize("factor", [float("inf"), float("nan")])
def test_energy_project_vdb_depreciation_rejects_non_finite_factor(factor):
    with pytest.raises(ValueError, match="VDB factor must be finite"):
        EnergyProject().depreciation_vdb(life=5, factor=factor)


@pytest.mark.parametrize("factor", [-1.0, 0.0])
def test_energy_project_vdb_depreciation_rejects_non_positive_factor(factor):
    with pytest.raises(ValueError, match="VDB factor must be positive"):
        EnergyProject().depreciation_vdb(life=5, factor=factor)


def test_energy_project_vdb_depreciation_allows_positive_factor():
    project = EnergyProject().depreciation_vdb(life=5, factor=1.5)

    assert project._config.depreciation is not None
    assert project._config.depreciation.factor == pytest.approx(1.5)


@pytest.mark.parametrize(
    ("valuation_rate", "valuation_date"),
    [
        (None, None),
        (0.10, None),
        (None, date(2026, 1, 1)),
    ],
)
def test_energy_project_vdb_best_of_convention_requires_valuation_inputs(
    valuation_rate,
    valuation_date,
):
    with pytest.raises(
        ValueError,
        match="valuation_rate and valuation_date are required",
    ):
        EnergyProject().depreciation_vdb(
            life=5,
            convention="best-of-half-year-mid-quarter",
            valuation_rate=valuation_rate,
            valuation_date=valuation_date,
        )


@pytest.mark.parametrize(
    ("valuation_rate", "valuation_date"),
    [
        (0.10, None),
        (None, date(2026, 1, 1)),
        (0.10, date(2026, 1, 1)),
    ],
)
def test_energy_project_vdb_none_convention_rejects_valuation_inputs(
    valuation_rate,
    valuation_date,
):
    with pytest.raises(
        ValueError,
        match="valuation_rate and valuation_date are only supported",
    ):
        EnergyProject().depreciation_vdb(
            life=5,
            convention="none",
            valuation_rate=valuation_rate,
            valuation_date=valuation_date,
        )


@pytest.mark.parametrize("valuation_rate", [float("inf"), float("nan")])
def test_energy_project_vdb_depreciation_rejects_non_finite_valuation_rate(
    valuation_rate,
):
    with pytest.raises(ValueError, match="VDB valuation_rate must be finite"):
        EnergyProject().depreciation_vdb(
            life=5,
            convention="best-of-half-year-mid-quarter",
            valuation_rate=valuation_rate,
            valuation_date=date(2026, 1, 1),
        )


def test_energy_project_vdb_best_of_convention_allows_valuation_inputs():
    project = EnergyProject().depreciation_vdb(
        life=5,
        convention="best-of-half-year-mid-quarter",
        valuation_rate=0.10,
        valuation_date=date(2026, 1, 1),
    )

    assert project._config.depreciation is not None
    assert project._config.depreciation.valuation_rate == pytest.approx(0.10)
    assert project._config.depreciation.valuation_date == date(2026, 1, 1)


# --- operations_end exclusivity ---


def test_energy_project_operations_end_is_exclusive():
    """operations_end=2027-01-01 covers all of 2026 (a full year)."""
    project = EnergyProject().generation(
        capacity_mw=10.0,
        capacity_factor=1.0,
        operations_start=date(2026, 1, 1),
        operations_end=date(2027, 1, 1),
    )

    analysis = project.analyze()
    assert analysis.generation.count() == 1
    assert analysis.generation.sum() == pytest.approx(10.0 * 8760.0)


@pytest.mark.parametrize("capacity_mw", [-1.0, float("inf"), float("nan")])
def test_energy_project_generation_rejects_invalid_capacity_mw(capacity_mw):
    with pytest.raises(ValueError, match="capacity_mw"):
        EnergyProject().generation(
            capacity_mw=capacity_mw,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )


@pytest.mark.parametrize("capacity_factor", [-0.1, 1.1, float("inf"), float("nan")])
def test_energy_project_generation_rejects_invalid_capacity_factor(capacity_factor):
    with pytest.raises(ValueError, match="capacity_factor must be between 0 and 1"):
        EnergyProject().generation(
            capacity_mw=10.0,
            capacity_factor=capacity_factor,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )


@pytest.mark.parametrize("periods", [-1.0, 0.0])
def test_energy_project_generation_rejects_non_positive_periods(periods):
    with pytest.raises(ValueError, match="generation periods must be positive"):
        EnergyProject().generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            start=date(2026, 1, 1),
            periods=periods,
        )


def test_energy_project_generation_allows_positive_fractional_periods():
    project = EnergyProject().generation(
        capacity_mw=10.0,
        capacity_factor=1.0,
        start=date(2026, 1, 1),
        periods=1.5,
    )

    assert project._config.generation is not None
    assert project._config.generation.periods == pytest.approx(1.5)


@pytest.mark.parametrize(
    ("operations_start", "operations_end"),
    [
        (date(2026, 1, 1), date(2026, 1, 1)),
        (date(2026, 2, 1), date(2026, 1, 1)),
    ],
)
def test_energy_project_generation_rejects_invalid_operations_date_order(
    operations_start,
    operations_end,
):
    with pytest.raises(ValueError, match="operations_end must be after operations_start"):
        EnergyProject().generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=operations_start,
            operations_end=operations_end,
        )


# --- construction_debt with stream override ---


def test_energy_project_construction_debt_rejects_when_stream_override_present():
    stream = CashFlowStream([CashFlow(-500.0, date(2025, 6, 1), label="Custom")])
    with pytest.raises(ValueError, match="construction_debt cannot be configured"):
        EnergyProject().construction_stream(stream=stream).construction_financing(
            debt_fraction=0.5,
            amortization_rate=0.05,
            amortization_term=10,
        )


@pytest.mark.parametrize("debt_fraction", [float("inf"), float("nan")])
def test_energy_project_construction_financing_rejects_non_finite_debt_fraction(
    debt_fraction,
):
    with pytest.raises(ValueError, match="debt_fraction must be finite"):
        EnergyProject().construction_financing(
            debt_fraction=debt_fraction,
            amortization_rate=0.05,
            amortization_term=10,
        )


@pytest.mark.parametrize("debt_fraction", [-0.1, 1.1])
def test_energy_project_construction_financing_rejects_out_of_range_debt_fraction(
    debt_fraction,
):
    with pytest.raises(ValueError, match="debt_fraction must be between 0 and 1"):
        EnergyProject().construction_financing(
            debt_fraction=debt_fraction,
            amortization_rate=0.05,
            amortization_term=10,
        )


@pytest.mark.parametrize("construction_interest_rate", [float("inf"), float("nan")])
def test_energy_project_construction_financing_rejects_non_finite_construction_interest_rate(
    construction_interest_rate,
):
    with pytest.raises(ValueError, match="construction_interest_rate must be finite"):
        EnergyProject().construction_financing(
            debt_fraction=0.5,
            construction_interest_rate=construction_interest_rate,
            amortization_rate=0.05,
            amortization_term=10,
        )


def test_energy_project_construction_financing_rejects_negative_construction_interest_rate():
    with pytest.raises(
        ValueError,
        match="construction_interest_rate must be non-negative",
    ):
        EnergyProject().construction_financing(
            debt_fraction=0.5,
            construction_interest_rate=-0.01,
            amortization_rate=0.05,
            amortization_term=10,
        )


def test_energy_project_construction_financing_allows_zero_construction_interest_rate():
    project = EnergyProject().construction_financing(
        debt_fraction=0.5,
        construction_interest_rate=0.0,
        amortization_rate=0.05,
        amortization_term=10,
    )

    assert project._config.construction_debt is not None
    assert project._config.construction_debt.construction_interest_rate == pytest.approx(0.0)


def test_energy_project_construction_financing_rejects_negative_amortization_rate():
    with pytest.raises(ValueError, match="amortization_rate must be non-negative"):
        EnergyProject().construction_financing(
            debt_fraction=0.5,
            amortization_rate=-0.01,
            amortization_term=10,
        )


def test_energy_project_construction_financing_allows_zero_amortization_rate():
    project = EnergyProject().construction_financing(
        debt_fraction=0.5,
        amortization_rate=0.0,
        amortization_term=10,
    )

    assert project._config.construction_debt is not None
    assert project._config.construction_debt.amortization_rate == pytest.approx(0.0)


@pytest.mark.parametrize("amortization_term", [-1, 0])
def test_energy_project_construction_financing_rejects_non_positive_amortization_term(
    amortization_term,
):
    with pytest.raises(ValueError, match="amortization_term must be positive"):
        EnergyProject().construction_financing(
            debt_fraction=0.5,
            amortization_rate=0.05,
            amortization_term=amortization_term,
        )


# --- Overnight cost without spend profile ---


def test_energy_project_overnight_cost_without_spend_profile():
    """When no spend_profile is given, overnight cost is booked as single cash flow at COD."""
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .construction(overnight_cost=1_000.0)
    )

    analysis = project.analyze()
    construction = analysis.cashflow_components["construction"]
    assert construction.count() == 1
    assert construction.sum() == pytest.approx(-1_000.0)
    # Booked at operations_start (COD default)
    assert construction.entries[0].date == date(2026, 1, 1)


def test_energy_project_overnight_cost_with_explicit_cod_date():
    """Explicit cod_date overrides operations_start for overnight booking."""
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .construction(overnight_cost=500.0, cod_date=date(2025, 7, 1))
    )

    analysis = project.analyze()
    construction = analysis.cashflow_components["construction"]
    assert construction.entries[0].date == date(2025, 7, 1)


def test_energy_project_construction_date_order_ignored_without_spend_profile():
    """construction_end is unused when construction is booked as one overnight-cost flow."""
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .construction(
            overnight_cost=1_000.0,
            construction_start=date(2025, 1, 1),
            construction_end=date(2025, 1, 1),
        )
    )

    analysis = project.analyze()
    construction = analysis.cashflow_components["construction"]
    assert construction.count() == 1
    assert construction.sum() == pytest.approx(-1_000.0)
    assert construction.entries[0].date == date(2026, 1, 1)


@pytest.mark.parametrize("overnight_cost", [float("inf"), float("nan")])
def test_energy_project_construction_rejects_non_finite_overnight_cost(
    overnight_cost,
):
    with pytest.raises(ValueError, match="overnight_cost must be finite"):
        EnergyProject().construction(overnight_cost=overnight_cost)


@pytest.mark.parametrize("overnight_cost", [-1.0, 0.0])
def test_energy_project_construction_rejects_non_positive_overnight_cost(
    overnight_cost,
):
    with pytest.raises(ValueError, match="overnight_cost must be positive"):
        EnergyProject().construction(overnight_cost=overnight_cost)


def test_energy_project_construction_rejects_spend_profile_without_construction_start():
    with pytest.raises(
        ValueError,
        match="construction_start is required when spend_profile is provided",
    ):
        EnergyProject().construction(
            overnight_cost=1_000.0,
            spend_profile="flat",
        )


@pytest.mark.parametrize(
    ("construction_start", "construction_end"),
    [
        (date(2025, 1, 1), date(2025, 1, 1)),
        (date(2025, 2, 1), date(2025, 1, 1)),
    ],
)
def test_energy_project_construction_rejects_invalid_construction_date_order(
    construction_start,
    construction_end,
):
    with pytest.raises(
        ValueError,
        match="construction_end must be after construction_start",
    ):
        EnergyProject().construction(
            overnight_cost=1_000.0,
            spend_profile="flat",
            construction_start=construction_start,
            construction_end=construction_end,
        )  # Note that spend_profile is not None, so the end date is necessary


# --- Generation stream infers operations dates ---


def test_energy_project_generation_stream_infers_operations_dates():
    """Operations dates are inferred from min/max dates in generation_stream."""
    stream = GenerationStream(
        [
            Generation(100.0, date(2026, 1, 1), label="Q1"),
            Generation(100.0, date(2026, 7, 1), label="Q3"),
        ]
    )
    project = (
        EnergyProject()
        .generation_stream(stream=stream)
        .fixed_opex(amount=100.0, start=date(2026, 1, 1), periods=1)
    )

    with pytest.warns(ScheduleTruncationWarning, match="fixed_opex schedule requested"):
        analysis = project.analyze()
    assert analysis.generation.count() == 2
    assert analysis.timeline.operations_start == date(2026, 1, 1)
    # operations_end is exclusive; inferred boundary is one day past the latest entry.
    assert analysis.timeline.operations_end == date(2026, 7, 2)
