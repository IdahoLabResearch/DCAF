from datetime import date
import csv

import pytest

from dcaf import EnergyProject
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow, CashFlowStream, Generation, GenerationStream


def test_energy_project_single_asset_workflow_builds_analysis_and_metrics():
    project = (
        EnergyProject("demo")
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 12, 31),
            frequency="year",
        )
        .capital_structure(
            debt_fraction=0.4,
            cost_of_debt=0.08,
            equity_fraction=0.6,
            cost_of_equity=0.12,
        )
        .generation(
            capacity_mw=100.0,
            capacity_factor=0.5,
        )
        .construction(
            overnight_cost=1_000.0,
            spend_profile="flat",
            period="year",
        )
        .annual_opex_cost(100.0)
        .variable_cost(10.0)
        .market(sell_price_per_unit=50.0)
        .macrs_depreciation(5)
        .itc(0.10)
        .tax(rate=0.21)
    )

    analysis = project.analyze()

    assert analysis.generation.sum() == pytest.approx(100.0 * 0.5 * 8760.0 * 2)
    assert analysis.capital_structure is not None
    assert analysis.capital_structure.coe == pytest.approx(0.12)
    assert analysis.capital_structure.wacc == pytest.approx(0.09728)
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
            0.0, abs=1e-6
        )

    pro_forma = analysis.pro_forma(period="year")
    assert "Free Cash Flow to Equity" in pro_forma.row_map()
    assert "default:revenue" in pro_forma.row_map()
    assert len(pro_forma.periods) >= 2


def test_energy_project_is_order_independent_across_sections():
    first = (
        EnergyProject("ordered-a")
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .generation(capacity_mw=25.0, capacity_factor=0.8)
        .market(sell_price_per_unit=60.0)
        .annual_opex_cost(50.0)
        .construction(overnight_cost=200.0, spend_profile="flat", period="year")
        .tax(rate=0.21)
    )

    second = (
        EnergyProject("ordered-b")
        .tax(rate=0.21)
        .construction(overnight_cost=200.0, spend_profile="flat", period="year")
        .annual_opex_cost(50.0)
        .market(sell_price_per_unit=60.0)
        .generation(capacity_mw=25.0, capacity_factor=0.8)
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
    )

    first_analysis = first.analyze()
    second_analysis = second.analyze()

    assert first_analysis.cashflows.sum() == pytest.approx(second_analysis.cashflows.sum())
    assert first_analysis.cashflows.count() == second_analysis.cashflows.count()
    assert first_analysis.metrics(0.10, date(2025, 1, 1)).npv == pytest.approx(
        second_analysis.metrics(0.10, date(2025, 1, 1)).npv
    )


def test_energy_project_supports_multiple_assets_and_asset_specific_market_overrides():
    project = (
        EnergyProject("portfolio")
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .generation(
            asset="uprate",
            capacity_mw=100.0,
            capacity_factor=0.5,
            carrier="electricity",
        )
        .generation(
            asset="solar",
            capacity_mw=50.0,
            capacity_factor=0.25,
            carrier="electricity",
        )
        .market(
            carrier="electricity", sell_price_per_unit=40.0
        )  # default price per unit electricity
        .market(
            asset="solar", carrier="electricity", sell_price_per_unit=60.0
        )  # override specific for "solar" asset
    )

    analysis = project.analyze()

    uprate_generation = analysis.generation_by_asset["uprate"].sum()
    solar_generation = analysis.generation_by_asset["solar"].sum()
    assert analysis.cashflow_components["uprate:revenue"].sum() == pytest.approx(
        uprate_generation * 40.0
    )
    assert analysis.cashflow_components["solar:revenue"].sum() == pytest.approx(
        solar_generation * 60.0
    )
    assert analysis.generation.sum() == pytest.approx(uprate_generation + solar_generation)


def test_energy_project_prices_mixed_carrier_generation_per_carrier_market():
    project = (
        EnergyProject("mixed-carrier")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .generation(
            stream=GenerationStream(
                [
                    Generation(10.0, date(2026, 1, 1), carrier="electricity", label="Electricity"),
                    Generation(5.0, date(2026, 1, 1), carrier="steam", label="Steam"),
                ]
            )
        )
        .market(carrier="electricity", sell_price_per_unit=50.0)
        .market(carrier="steam", sell_price_per_unit=20.0)
    )

    analysis = project.analyze()

    assert analysis.cashflow_components["default:revenue"].sum() == pytest.approx(600.0)


def test_energy_project_derives_debt_principal_from_construction_financing():
    project = (
        EnergyProject("financed")
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .construction(overnight_cost=1_000.0, spend_profile="flat", period="year")
        .construction_financing(debt_fraction=0.5)
        .debt(annual_rate=0.10, term=1, frequency="year")
    )

    analysis = project.analyze()
    assert analysis.cashflow_components["default:debt_service"].sum() == pytest.approx(-550.0)


def test_energy_project_generation_stream_override_rejects_simple_inputs():
    with pytest.raises(ValueError, match="generation stream override"):
        EnergyProject().generation(
            stream=GenerationStream.from_capacity(10.0, 0.9, date(2030, 1, 1), 1),
            capacity_mw=10.0,
        )


def test_energy_project_allows_custom_cashflow_streams():
    bonus = CashFlowStream([CashFlow(100.0, date(2026, 6, 1), label="Bonus")])
    project = (
        EnergyProject("custom")
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .add_cashflow_stream("grant", bonus)
    )

    analysis = project.analyze()
    assert analysis.cashflow_components["grant"].sum() == pytest.approx(100.0)


def test_energy_project_prorates_partial_operating_periods_from_timeline_dates():
    project = (
        EnergyProject("partial-periods")
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 6, 1),
            operations_end=date(2027, 8, 31),
            frequency="year",
        )
        .generation(capacity_mw=10.0, capacity_factor=0.5)
        .annual_opex_cost(120.0)
    )

    analysis = project.analyze()
    # operations_end is inclusive; exclusive boundary is 2027-09-01
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
        EnergyProject("csv-export")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .generation(capacity_mw=1.0, capacity_factor=1.0)
        .market(sell_price_per_unit=50.0)
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
    project = (
        EnergyProject("pro-forma-subtotals")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .add_cashflow_stream(
            "revenue",
            CashFlowStream(
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
            "opex",
            CashFlowStream(
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
            "capex",
            CashFlowStream(
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
            "depreciation",
            CashFlowStream(
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
            "tax_credit",
            CashFlowStream(
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
        .debt(annual_rate=0.10, term=1, frequency="year", principal=100.0, start=date(2026, 1, 1))
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
        EnergyProject("modified")
        .timeline(
            construction_start=date(2025, 1, 1),
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .generation(capacity_mw=1.0, capacity_factor=1.0)
        .market(sell_price_per_unit=1.0)
        .tax(rate=0.21)
    )

    def halve_revenue(components):
        updated = dict(components.items())
        updated["default:revenue"] = updated["default:revenue"].apply(
            lambda flow: flow.replace(amount=flow.amount * 0.5)
        )
        return updated

    modified_project = base_project.modify_cashflow_components(halve_revenue)

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
        EnergyProject("multi-opex")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
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
        EnergyProject("compat")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .annual_opex_cost(100.0)
    )

    analysis = project.analyze()
    assert "default:fixed_opex" in analysis.cashflow_components
    assert analysis.cashflow_components["default:fixed_opex"].sum() == pytest.approx(-100.0)


def test_energy_project_supports_multiple_named_variable_cost_items():
    project = (
        EnergyProject("multi-var")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .generation(capacity_mw=10.0, capacity_factor=1.0)
        .variable_cost(5.0, name="fuel")
        .variable_cost(2.0, name="water")
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


def test_energy_project_named_fixed_opex_update_merges_by_name():
    project = (
        EnergyProject("merge")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .fixed_opex(name="om", amount=100.0)
        .fixed_opex(name="om", amount=200.0)  # update, not duplicate
    )

    analysis = project.analyze()
    assert "default:fixed_opex:om" in analysis.cashflow_components
    assert analysis.cashflow_components["default:fixed_opex:om"].sum() == pytest.approx(-200.0)


def test_energy_project_explicit_zero_escalation_overrides_project_default():
    project = (
        EnergyProject("zero-escalation")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 12, 31),
            frequency="year",
        )
        .default_escalation(0.10)
        .fixed_opex(name="flat", amount=100.0, escalation=0.0)
        .fixed_opex(name="default", amount=100.0)
    )

    analysis = project.analyze()

    assert analysis.cashflow_components["default:fixed_opex:flat"].sum() == pytest.approx(-200.0)
    assert analysis.cashflow_components["default:fixed_opex"].sum() == pytest.approx(-210.0)


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
    project = (
        EnergyProject("levered-tax")
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .add_cashflow_stream("revenue", revenue)
        .debt(annual_rate=0.10, term=1, frequency="year", principal=100.0, start=date(2026, 1, 1))
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
    common = dict(
        construction_start=date(2025, 1, 1),
        operations_start=date(2026, 1, 1),
        operations_end=date(2026, 12, 31),
        frequency="year",
    )

    def _build(interest_treatment):
        return (
            EnergyProject(interest_treatment)
            .timeline(**common)
            .construction(overnight_cost=1_000.0, spend_profile="flat", period="month")
            .construction_financing(
                debt_fraction=0.5,
                interest_rate=0.10,
                interest_treatment=interest_treatment,
            )
            .debt(annual_rate=0.05, term=1, frequency="year")
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


# --- Tax rate propagation: order-independent ---


def test_energy_project_tax_rate_propagates_to_wacc_regardless_of_call_order():
    """Tax rate set via .tax() should flow to capital_structure.wacc regardless of order."""
    tax_first = (
        EnergyProject()
        .tax(rate=0.21)
        .capital_structure(
            debt_fraction=0.4, cost_of_debt=0.08, equity_fraction=0.6, cost_of_equity=0.12
        )
    )

    cap_first = (
        EnergyProject()
        .capital_structure(
            debt_fraction=0.4, cost_of_debt=0.08, equity_fraction=0.6, cost_of_equity=0.12
        )
        .tax(rate=0.21)
    )

    tax_first_analysis = tax_first.timeline(
        operations_start=date(2026, 1, 1), operations_end=date(2026, 12, 31)
    ).analyze()
    cap_first_analysis = cap_first.timeline(
        operations_start=date(2026, 1, 1), operations_end=date(2026, 12, 31)
    ).analyze()

    assert tax_first_analysis.capital_structure is not None
    assert cap_first_analysis.capital_structure is not None
    assert tax_first_analysis.capital_structure.wacc == pytest.approx(
        cap_first_analysis.capital_structure.wacc
    )
    expected_wacc = 0.12 * 0.6 + 0.08 * 0.4 * (1 - 0.21)
    assert tax_first_analysis.capital_structure.wacc == pytest.approx(expected_wacc)


def test_energy_project_tax_rate_update_propagates_to_wacc():
    """Calling .tax() a second time should update the WACC computation."""
    project = (
        EnergyProject()
        .capital_structure(
            debt_fraction=0.4, cost_of_debt=0.08, equity_fraction=0.6, cost_of_equity=0.12
        )
        .tax(rate=0.15)
        .tax(rate=0.30)
        .timeline(operations_start=date(2026, 1, 1), operations_end=date(2026, 12, 31))
    )

    analysis = project.analyze()
    assert analysis.capital_structure is not None
    expected_wacc = 0.12 * 0.6 + 0.08 * 0.4 * (1 - 0.30)
    assert analysis.capital_structure.wacc == pytest.approx(expected_wacc)


def test_energy_project_explicit_capital_structure_tax_rate_takes_precedence():
    """An explicit tax_rate on capital_structure overrides the project tax rate."""
    project = (
        EnergyProject()
        .tax(rate=0.21)
        .capital_structure(
            debt_fraction=0.4,
            cost_of_debt=0.08,
            equity_fraction=0.6,
            cost_of_equity=0.12,
            tax_rate=0.30,
        )
        .timeline(operations_start=date(2026, 1, 1), operations_end=date(2026, 12, 31))
    )

    analysis = project.analyze()
    assert analysis.capital_structure is not None
    expected_wacc = 0.12 * 0.6 + 0.08 * 0.4 * (1 - 0.30)
    assert analysis.capital_structure.wacc == pytest.approx(expected_wacc)


# --- operations_end inclusivity ---


def test_energy_project_operations_end_is_inclusive():
    """operations_end=2026-12-31 should produce a full year of operation."""
    project = (
        EnergyProject()
        .timeline(
            operations_start=date(2026, 1, 1),
            operations_end=date(2026, 12, 31),
            frequency="year",
        )
        .generation(capacity_mw=10.0, capacity_factor=1.0)
    )

    analysis = project.analyze()
    assert analysis.generation.count() == 1
    assert analysis.generation.sum() == pytest.approx(10.0 * 8760.0)


def test_energy_project_operating_years_derives_inclusive_end():
    """operating_years=2 from 2026-01-01 should set operations_end=2027-12-31."""
    project = EnergyProject().timeline(
        operations_start=date(2026, 1, 1),
        operating_years=2,
    )
    assert project.timeline_config.operations_end == date(2027, 12, 31)
    assert project.timeline_config.operating_years == pytest.approx(2.0)


def test_energy_project_operations_end_allows_same_day_as_start():
    """operations_end == operations_start is valid (single-day operation)."""
    project = EnergyProject().timeline(
        operations_start=date(2026, 1, 1),
        operations_end=date(2026, 1, 1),
        frequency="year",
    )
    assert project.timeline_config.operations_end == date(2026, 1, 1)


# --- construction_financing with stream override ---


def test_energy_project_construction_financing_rejects_when_stream_override_present():
    stream = CashFlowStream([CashFlow(-500.0, date(2025, 6, 1), label="Custom")])
    with pytest.raises(ValueError, match="construction_financing cannot be configured"):
        EnergyProject().construction(stream=stream).construction_financing(debt_fraction=0.5)
