# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Unit tests for the function-based outage helpers in dcaf.finance.outage."""

from datetime import date

import pytest

from dcaf import EnergyContract, GenerationPrice, EnergyProject
from dcaf.finance.escalation import ConstantRateEscalation
from dcaf.finance.outage import construction_outage, generator_outage
from dcaf.shared.time import timedelta_fractional_years
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import Generation, GenerationStream


def test_generator_outage_matches_classmethod():
    """``generator_outage`` and ``GenerationStream.from_outage`` agree on amounts and dates."""
    helper = generator_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 11),
    )
    classmethod_stream = GenerationStream.from_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 11),
    )

    assert helper.count() == 1
    assert helper.entries[0].amount_mwh == pytest.approx(classmethod_stream.entries[0].amount_mwh)
    assert helper.entries[0].date == classmethod_stream.entries[0].date
    assert helper.entries[0].label == "Generator Outage"


def test_construction_outage_lost_revenue_only():
    """With no replacement costs the helper produces a single lost-revenue cashflow."""
    stream = construction_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 11),
        sell_price_per_unit=50.0,
    )

    lost_mwh = 1000.0 * 0.92 * 24.0 * 10.0

    assert stream.count() == 1
    assert stream.entries[0].label == "Outage Lost Revenue"
    assert stream.entries[0].amount == pytest.approx(-lost_mwh * 50.0)
    assert stream.entries[0].pro_forma_category is ProFormaCategory.OPERATING_COST
    assert stream.entries[0].tax_treatment is TaxTreatment.DEDUCTIBLE


def test_construction_outage_includes_distinct_fixed_and_daily_costs():
    """Fixed and per-day costs are emitted as separate cashflows with distinct labels."""
    stream = construction_outage(
        capacity_mw=1200.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 21),
        sell_price_per_unit=50.0,
        fixed_cost=2_000_000.0,
        cost_per_day=100_000.0,
    )

    assert stream.count() == 3
    labels = [cf.label for cf in stream.entries]
    assert labels == ["Outage Lost Revenue", "Outage Fixed Cost", "Outage Replacement Power"]

    amounts = {cf.label: cf.amount for cf in stream.entries}
    lost_mwh = 1200.0 * 0.92 * 24.0 * 20.0
    assert amounts["Outage Lost Revenue"] == pytest.approx(-lost_mwh * 50.0)
    assert amounts["Outage Fixed Cost"] == pytest.approx(-2_000_000.0)
    assert amounts["Outage Replacement Power"] == pytest.approx(-100_000.0 * 20.0)
    assert all(cf.amount < 0 for cf in stream.entries)
    assert {cf.pro_forma_category for cf in stream.entries} == {ProFormaCategory.OPERATING_COST}
    assert {cf.tax_treatment for cf in stream.entries} == {TaxTreatment.DEDUCTIBLE}


def test_construction_outage_splits_all_cashflow_types_by_calendar_frequency():
    stream = construction_outage(
        capacity_mw=1.0,
        capacity_factor=1.0,
        start=date(2030, 1, 15),
        end=date(2030, 3, 15),
        sell_price_per_unit=1.0,
        fixed_cost=590.0,
        cost_per_day=10.0,
        frequency="month",
        timing="end",
    )

    assert stream.count() == 9
    assert sorted({flow.date for flow in stream}) == [
        date(2030, 1, 31),
        date(2030, 2, 28),
        date(2030, 3, 14),
    ]
    assert sum(flow.amount for flow in stream if flow.label == "Outage Lost Revenue") == (
        pytest.approx(-59.0 * 24.0)
    )
    assert sum(flow.amount for flow in stream if flow.label == "Outage Fixed Cost") == (
        pytest.approx(-590.0)
    )
    assert sum(flow.amount for flow in stream if flow.label == "Outage Replacement Power") == (
        pytest.approx(-590.0)
    )


def test_construction_outage_uses_annual_frequency_by_default():
    stream = construction_outage(
        capacity_mw=1.0,
        capacity_factor=1.0,
        start=date(2030, 7, 1),
        end=date(2031, 7, 1),
        sell_price_per_unit=1.0,
        fixed_cost=365.0,
        cost_per_day=1.0,
    )

    assert stream.count() == 6
    for label, expected_amounts in (
        ("Outage Lost Revenue", [-184.0 * 24.0, -181.0 * 24.0]),
        ("Outage Fixed Cost", [-184.0, -181.0]),
        ("Outage Replacement Power", [-184.0, -181.0]),
    ):
        flows = [flow for flow in stream if flow.label == label]
        assert [flow.date for flow in flows] == [date(2030, 12, 31), date(2031, 6, 30)]
        assert [flow.amount for flow in flows] == pytest.approx(expected_amounts)


def test_construction_outage_sign_of_inputs_ignored():
    """Positive or negative ``fixed_cost`` / ``cost_per_day`` produce identical cost magnitudes."""
    positive = construction_outage(
        capacity_mw=100.0,
        capacity_factor=0.5,
        start=date(2030, 1, 1),
        end=date(2030, 1, 5),
        sell_price_per_unit=40.0,
        fixed_cost=500_000.0,
        cost_per_day=10_000.0,
    )
    negative = construction_outage(
        capacity_mw=100.0,
        capacity_factor=0.5,
        start=date(2030, 1, 1),
        end=date(2030, 1, 5),
        sell_price_per_unit=40.0,
        fixed_cost=-500_000.0,
        cost_per_day=-10_000.0,
    )

    assert positive.sum() == pytest.approx(negative.sum())


def test_construction_outage_zero_cost_arguments_omit_flows():
    """Passing zero for ``fixed_cost`` and ``cost_per_day`` omits those line items."""
    stream = construction_outage(
        capacity_mw=100.0,
        capacity_factor=0.5,
        start=date(2030, 1, 1),
        end=date(2030, 1, 5),
        sell_price_per_unit=40.0,
        fixed_cost=0.0,
        cost_per_day=0.0,
    )

    assert stream.count() == 1
    assert stream.entries[0].label == "Outage Lost Revenue"


def test_construction_outage_escalation_applied():
    """Annual escalation grows ``sell_price_per_unit`` from the reference date forward."""
    reference = date(2025, 1, 1)
    booking = date(2030, 5, 10)
    stream = construction_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 11),
        sell_price_per_unit=50.0,
        escalation=0.02,
        amount_reference_date=reference,
    )

    lost_mwh = 1000.0 * 0.92 * 24.0 * 10.0
    years = timedelta_fractional_years(reference, booking)
    expected = -lost_mwh * 50.0 * (1.02**years)
    assert stream.entries[0].amount == pytest.approx(expected, rel=1e-6)


def test_construction_outage_escalation_policy_exclusive():
    """Combining ``escalation`` and ``escalation_policy`` raises (delegated from to_revenue)."""
    policy = ConstantRateEscalation(rate=0.02, period="year", reference_date=date(2025, 1, 1))
    with pytest.raises(ValueError):
        construction_outage(
            capacity_mw=1000.0,
            capacity_factor=0.92,
            start=date(2030, 5, 1),
            end=date(2030, 5, 11),
            sell_price_per_unit=50.0,
            escalation=0.02,
            escalation_policy=policy,
        )


def test_construction_outage_zero_days_invalid():
    """``start == end`` raises a clear validation error from from_outage."""
    with pytest.raises(ValueError, match="end must be after"):
        construction_outage(
            capacity_mw=100.0,
            capacity_factor=0.5,
            start=date(2030, 1, 1),
            end=date(2030, 1, 1),
            sell_price_per_unit=40.0,
        )


@pytest.mark.parametrize(
    "timing,expected",
    [("begin", date(2030, 5, 1)), ("middle", date(2030, 5, 5)), ("end", date(2030, 5, 10))],
)
def test_construction_outage_timing_variants(timing, expected):
    """All cashflows are booked at the date implied by the timing convention."""
    stream = construction_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 11),
        sell_price_per_unit=50.0,
        fixed_cost=100_000.0,
        cost_per_day=10_000.0,
        timing=timing,
    )

    assert {cf.date for cf in stream.entries} == {expected}


def test_construction_outage_custom_labels():
    """Custom labels propagate to all generated cashflows."""
    stream = construction_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 11),
        sell_price_per_unit=50.0,
        fixed_cost=100_000.0,
        cost_per_day=10_000.0,
        lost_revenue_label="EPU Refuel #1 Lost Revenue",
        fixed_cost_label="EPU Refuel #1 Fixed Cost",
        daily_cost_label="EPU Refuel #1 Replacement Power",
    )

    labels = [cf.label for cf in stream.entries]
    assert labels == [
        "EPU Refuel #1 Lost Revenue",
        "EPU Refuel #1 Fixed Cost",
        "EPU Refuel #1 Replacement Power",
    ]


def test_construction_outage_equivalence_with_builder_method():
    """The standalone helper produces the same totals as the builder method."""
    sell_price = 45.0
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(sell_price))
        .construction_outage(
            capacity_mw=1000.0,
            capacity_factor=0.92,
            start=date(2025, 5, 1),
            end=date(2025, 5, 11),
            sell_price_per_unit=sell_price,
            fixed_cost=500_000.0,
            cost_per_day=20_000.0,
        )
    )
    analysis = project.analyze()
    builder_stream = analysis.cashflow_components["construction_outage"]

    helper_stream = construction_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2025, 5, 1),
        end=date(2025, 5, 11),
        sell_price_per_unit=sell_price,
        fixed_cost=500_000.0,
        cost_per_day=20_000.0,
    )

    assert builder_stream.sum() == pytest.approx(helper_stream.sum())
    assert builder_stream.count() == helper_stream.count()
    builder_labels = sorted(cf.label for cf in builder_stream)
    helper_labels = sorted(cf.label for cf in helper_stream)
    assert builder_labels == helper_labels


def test_project_construction_outage_uses_project_frequency_and_explicit_timing():
    analysis = (
        EnergyProject(frequency="month", timing="begin")
        .generation(
            capacity_mw=1.0,
            capacity_factor=1.0,
            operations_start=date(2031, 1, 1),
            operations_end=date(2032, 1, 1),
        )
        .construction_outage(
            capacity_mw=1.0,
            capacity_factor=1.0,
            start=date(2030, 1, 15),
            end=date(2030, 3, 15),
            sell_price_per_unit=1.0,
            timing="end",
        )
        .analyze()
    )

    outage = analysis.cashflow_components["construction_outage"]
    assert [flow.date for flow in outage] == [
        date(2030, 1, 31),
        date(2030, 2, 28),
        date(2030, 3, 14),
    ]


def test_construction_outage_market_price_lookup_via_builder():
    """An explicit fixed market policy remains fixed when used for outage fallback."""
    project = (
        EnergyProject()
        .default_escalation(rate=0.10, amount_reference_date=date(2024, 5, 11))
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=GenerationPrice.fixed(42.0))
        .construction_outage(
            start=date(2025, 5, 1),
            end=date(2025, 5, 11),
            capacity_mw=1000.0,
            capacity_factor=0.92,
        )
    )
    analysis = project.analyze()
    impact = analysis.cashflow_components["construction_outage"]
    expected = -(1000.0 * 0.92 * 24.0 * 10.0 * 42.0)
    assert impact.sum() == pytest.approx(expected)


def test_construction_outage_scalar_market_price_inherits_default_escalation():
    project = (
        EnergyProject()
        .default_escalation(rate=0.10, amount_reference_date=date(2025, 1, 1))
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price=50.0)
        .construction_outage(
            start=date(2026, 1, 1),
            end=date(2026, 1, 2),
            capacity_mw=1.0,
            capacity_factor=1.0,
            timing="begin",
        )
    )

    impact = project.analyze().cashflow_components["construction_outage"]

    assert impact.sum() == pytest.approx(-(24.0 * 50.0 * 1.10))


def test_construction_outage_scalar_market_price_uses_earliest_generation_reference():
    project = (
        EnergyProject()
        .default_escalation(rate=0.10)
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(100.0, date(2027, 1, 1)),
                ]
            )
        )
        .generation_revenue(price=50.0)
        .construction_outage(
            start=date(2027, 1, 1),
            end=date(2027, 1, 2),
            capacity_mw=1.0,
            capacity_factor=1.0,
            timing="begin",
        )
    )

    impact = project.analyze().cashflow_components["construction_outage"]

    assert impact.sum() == pytest.approx(-(24.0 * 50.0 * 1.10))


def test_construction_outage_scalar_market_price_deflates_before_generation_reference():
    project = (
        EnergyProject()
        .default_escalation(rate=0.10)
        .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
        .generation_revenue(price=50.0)
        .construction_outage(
            start=date(2025, 1, 1),
            end=date(2025, 1, 2),
            capacity_mw=1.0,
            capacity_factor=1.0,
            timing="begin",
        )
    )

    impact = project.analyze().cashflow_components["construction_outage"]

    assert impact.sum() == pytest.approx(-(24.0 * 50.0 / 1.10))


@pytest.mark.parametrize(
    "market_price",
    [
        GenerationPrice.schedule({date(2026, 12, 31): 42.0}),
        GenerationPrice.callable(lambda _event: 42.0),
    ],
)
def test_construction_outage_requires_explicit_price_for_dynamic_generation_revenue(
    market_price: GenerationPrice,
):
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue(price_policy=market_price)
        .construction_outage(
            start=date(2025, 5, 1),
            end=date(2025, 5, 11),
            capacity_mw=1000.0,
            capacity_factor=0.92,
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "requires sell_price_per_unit unless generation_revenue is configured with price "
            "or a fixed price_policy"
        ),
    ):
        project.analyze()


def test_construction_outage_requires_explicit_price_with_contract_revenue():
    """Verifies exception is raised if `sell_price_per_unit` is not specified when a non-constant generation
    price is provided for generation revenue."""
    project = (
        EnergyProject()
        .generation(
            capacity_mw=10.0,
            capacity_factor=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=0.50,
                price=GenerationPrice.fixed(50.0),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(30.0),
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
