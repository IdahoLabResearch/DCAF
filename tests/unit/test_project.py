from datetime import date
import csv

import pytest

from dcaf import EnergyProject
from dcaf.finance import ConstantRateEscalation
from dcaf.finance.amortization import AmortizationSchedule
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow, CashFlowStream, Generation, GenerationStream


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
        .revenue_from_generation(sell_price_per_unit=50.0)
        .depreciation_macrs(property_class=5)
        .investment_tax_credit(rate=0.10)
        .tax(rate=0.21)
    )

    analysis = project.analyze()

    assert analysis.generation.sum() == pytest.approx(100.0 * 0.5 * 8760.0 * 2)
    assert analysis.valuation is not None
    assert analysis.valuation.discount_rate == pytest.approx(0.09728)
    assert set(analysis.cashflow_components.keys()) >= {
        "default:construction",
        "default:revenue",
        "default:fixed_opex",
        "default:variable_cost",
        "default:depreciation",
        "default:itc",
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
    assert "default:revenue" in pro_forma.row_map()
    assert len(pro_forma.periods) >= 2


def test_energy_project_levelized_cost_matches_real_carrying_charge_methodology():
    """LCOE should solve for the starting price that drives project NPV to zero."""

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
    operations_end = date(2027 + construction_years + project_life_years - 1, 12, 31)
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
                carrier="electricity",
                source="nuclear-uprate",
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

    def assert_levelized_cost_solves_project(project: EnergyProject) -> float:
        metrics = project.metrics(
            discount_rate=wacc_aftertax,
            valuation_date=operations_start,
            levelized_cost_escalation_policy=revenue_policy,
        )
        assert metrics.levelized_cost is not None

        # Verify by evaluating the LCOE objective at the solved price.
        # The LCOE solve operates on capex + opex + tax + tax_credit
        # categories with recomputed taxes, so we verify through the
        # same objective rather than a full-project NPV.
        analysis = project.analyze()
        basis = analysis.generation.to_revenue(
            price_per_mwh=1.0,
            escalation_policy=revenue_policy,
        )
        from dcaf.metrics.lcoe import _lcoe_objective

        obj = _lcoe_objective(
            price=metrics.levelized_cost,
            basis_stream=basis,
            component_streams=analysis.cashflow_components,
            tax_rate=tax_rate,
            discount_rate=wacc_aftertax,
            valuation_date=operations_start,
            convention="actual/365",
        )
        assert obj == pytest.approx(0.0, abs=1.0)
        return metrics.levelized_cost

    fc_only_lcoe = assert_levelized_cost_solves_project(build_project(variable_cost=20.0))
    om_only_lcoe = assert_levelized_cost_solves_project(build_project(annual_opex=162_936_000.0))
    capex_only_lcoe = assert_levelized_cost_solves_project(build_project(capex=660_000_000.0))
    combined_lcoe = assert_levelized_cost_solves_project(
        build_project(
            capex=660_000_000.0,
            annual_opex=162_936_000.0,
            variable_cost=20.0,
        )
    )

    assert om_only_lcoe == pytest.approx(20.0, abs=0.1)
    assert fc_only_lcoe < 20.0
    assert combined_lcoe > capex_only_lcoe


def test_energy_project_is_order_independent_across_sections():
    first = (
        EnergyProject()
        .generation(
            capacity_mw=25.0,
            capacity_factor=0.8,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .revenue_from_generation(sell_price_per_unit=60.0)
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
        .revenue_from_generation(sell_price_per_unit=60.0)
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
        .revenue_from_generation(sell_price_per_unit=50.0)
    )

    analysis = project.analyze()

    with pytest.raises(ValueError, match="overflow encountered during iteration"):
        analysis.cashflows.cash_only().irr()

    metrics = analysis.metrics(discount_rate=0.10, valuation_date=date(2025, 1, 1))
    assert metrics.xirr is None


def test_energy_project_prices_mixed_carrier_generation_per_carrier_market():
    project = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(10.0, date(2026, 1, 1), carrier="electricity", label="Electricity"),
                    Generation(5.0, date(2026, 1, 1), carrier="steam", label="Steam"),
                ]
            )
        )
        .revenue_from_generation(carrier="electricity", sell_price_per_unit=50.0)
        .revenue_from_generation(carrier="steam", sell_price_per_unit=20.0)
    )

    analysis = project.analyze()

    assert analysis.cashflow_components["default:revenue"].sum() == pytest.approx(600.0)


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
    assert analysis.cashflow_components["default:debt_service"].sum() == pytest.approx(-550.0)


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
        .revenue_from_generation(sell_price_per_unit=50.0)
        .variable_cost(rate_per_unit=5.0)
        .production_tax_credit(rate_per_unit=1.0, years=1)
    )

    analysis = project.analyze()
    base_mwh = 10.0 * 8760.0
    lost_mwh = 10.0 * 24.0 * 10.0
    net_mwh = base_mwh - lost_mwh

    assert analysis.generation.count() == 2
    assert analysis.generation.sum() == pytest.approx(net_mwh)
    assert analysis.cashflow_components["default:revenue"].sum() == pytest.approx(net_mwh * 50.0)
    assert analysis.cashflow_components["default:variable_cost"].sum() == pytest.approx(
        -net_mwh * 5.0
    )
    assert analysis.cashflow_components["default:ptc"].sum() == pytest.approx(net_mwh)
    assert analysis.metrics(discount_rate=0.08, valuation_date=date(2026, 1, 1)).levelized_cost


def test_energy_project_construction_outage_preserves_generation():
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .revenue_from_generation(sell_price_per_unit=50.0)
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
    impact = analysis.cashflow_components["default:construction_outage"]

    assert analysis.generation.sum() == pytest.approx(10.0 * 8760.0)
    assert impact.sum() == pytest.approx(expected_impact)
    assert {flow.pro_forma_category for flow in impact} == {ProFormaCategory.OPERATING_COST}
    assert {flow.tax_treatment for flow in impact} == {TaxTreatment.DEDUCTIBLE}


def test_energy_project_construction_outage_explicit_price_and_lcoe():
    base_project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .revenue_from_generation(sell_price_per_unit=50.0)
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
            source="nuclear-uprate",
        )
        .revenue_from_generation(sell_price_per_unit=45.0)
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

    assert analysis.generation.sum() == pytest.approx(220.0 * 0.92 * 8760.0 * 366.0 / 365.0)
    assert analysis.cashflow_components["default:construction_outage:refueling_1"].sum() == (
        pytest.approx(expected_per_outage)
    )
    assert analysis.cashflow_components["default:construction_outage:refueling_2"].sum() == (
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
    assert analysis.cashflow_components["default:fixed_opex"].sum() == pytest.approx(expected_opex)


def test_project_pro_forma_can_write_csv(tmp_path):
    project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .revenue_from_generation(sell_price_per_unit=50.0)
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
    assert float(row_map["default:revenue"][0]) == pytest.approx(1.0 * 8760.0 * 50.0)


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
    assert row_map["Financing Interest"] == pytest.approx((-10.0,))
    assert row_map["Interest Tax Shield"] == pytest.approx((2.0,))
    assert row_map["Financing Principal"] == pytest.approx((-100.0,))
    assert row_map["Free Cash Flow to Equity"] == pytest.approx((-74.0,))

    assert row_map["revenue"] == pytest.approx((200.0,))
    assert row_map["opex"] == pytest.approx((-50.0,))
    assert row_map["capex"] == pytest.approx((-100.0,))
    assert row_map["depreciation"] == pytest.approx((-20.0,))
    assert row_map["tax_credit"] == pytest.approx((10.0,))
    assert row_map["default:debt_service"] == pytest.approx((-110.0,))
    assert "project:tax_liability" not in row_map


def test_energy_project_cashflow_modifiers_can_rewrite_components_before_tax():
    base_project = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .revenue_from_generation(sell_price_per_unit=1.0)
        .tax(rate=0.21)
    )

    def halve_revenue(components):
        updated = dict(components.items())
        updated["default:revenue"] = updated["default:revenue"].apply(
            lambda flow: flow.replace(amount=flow.amount * 0.5)
        )
        return updated

    modified_project = base_project.modify_cashflow_components(modifier=halve_revenue)

    base_analysis = base_project.analyze()
    modified_analysis = modified_project.analyze()

    assert modified_analysis.cashflow_components["default:revenue"].sum() == pytest.approx(
        base_analysis.cashflow_components["default:revenue"].sum() * 0.5
    )
    assert modified_analysis.taxable_income.sum() == pytest.approx(
        base_analysis.taxable_income.sum() * 0.5
    )
    assert modified_analysis.taxes.sum() == pytest.approx(base_analysis.taxes.sum() * 0.5)


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
    assert "default:fixed_opex:om" in analysis.cashflow_components
    assert "default:fixed_opex:insurance" in analysis.cashflow_components
    assert "default:fixed_opex:land_lease" in analysis.cashflow_components
    assert analysis.cashflow_components["default:fixed_opex:om"].sum() == pytest.approx(-100.0)
    assert analysis.cashflow_components["default:fixed_opex:insurance"].sum() == pytest.approx(
        -50.0
    )
    assert analysis.cashflow_components["default:fixed_opex:land_lease"].sum() == pytest.approx(
        -30.0
    )


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
    assert "default:fixed_opex" in analysis.cashflow_components
    assert analysis.cashflow_components["default:fixed_opex"].sum() == pytest.approx(-100.0)


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
    assert "default:variable_cost:fuel" in analysis.cashflow_components
    assert "default:variable_cost:water" in analysis.cashflow_components
    assert analysis.cashflow_components["default:variable_cost:fuel"].sum() == pytest.approx(
        -5.0 * gen_mwh
    )
    assert analysis.cashflow_components["default:variable_cost:water"].sum() == pytest.approx(
        -2.0 * gen_mwh
    )


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
    assert "default:fixed_opex:om" in analysis.cashflow_components
    assert analysis.cashflow_components["default:fixed_opex:om"].sum() == pytest.approx(-200.0)


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

    assert analysis.cashflow_components["default:fixed_opex:flat"].sum() == pytest.approx(-200.0)
    assert analysis.cashflow_components["default:fixed_opex"].sum() == pytest.approx(
        -230.94, abs=0.1
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
    debt_service = analysis.cashflow_components["default:debt_service"]

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

    cap_debt = abs(capitalize_analysis.cashflow_components["default:debt_service"].sum())
    pay_debt = abs(pay_analysis.cashflow_components["default:debt_service"].sum())

    # Both should have debt service
    assert cap_debt > 0.0
    assert pay_debt > 0.0

    # Capitalize mode rolls interest into the principal, producing larger debt service
    assert cap_debt > pay_debt

    # Verify the pay mode principal derives only from cash capex
    construction = pay_analysis.cashflow_components["default:construction"]
    capex = construction.filter(pro_forma_category=ProFormaCategory.CAPITAL_COST)
    cash_basis = abs(capex.cash_only().sum())
    expected_pay_principal = cash_basis * 0.5
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
        .revenue_from_generation(sell_price_per_unit=50.0)
    )

    analysis = project.analyze()
    assert analysis.valuation is not None
    assert analysis.valuation.discount_rate == pytest.approx(0.10)
    assert analysis.metrics().discount_rate == pytest.approx(0.10)


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
        .revenue_from_generation(sell_price_per_unit=50.0)
    )

    analysis = project.analyze()
    assert analysis.metrics().discount_rate == pytest.approx(0.10)
    assert analysis.metrics(discount_rate=0.12).discount_rate == pytest.approx(0.12)


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


# --- construction_debt with stream override ---


def test_energy_project_construction_debt_rejects_when_stream_override_present():
    stream = CashFlowStream([CashFlow(-500.0, date(2025, 6, 1), label="Custom")])
    with pytest.raises(ValueError, match="construction_debt cannot be configured"):
        EnergyProject().construction_stream(stream=stream).construction_financing(
            debt_fraction=0.5,
            amortization_rate=0.05,
            amortization_term=10,
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
    construction = analysis.cashflow_components["default:construction"]
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
    construction = analysis.cashflow_components["default:construction"]
    assert construction.entries[0].date == date(2025, 7, 1)


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

    analysis = project.analyze()
    assert analysis.generation.count() == 2
    assert analysis.timeline.operations_start == date(2026, 1, 1)
    # operations_end is exclusive; inferred boundary is one day past the latest entry.
    assert analysis.timeline.operations_end == date(2026, 7, 2)
