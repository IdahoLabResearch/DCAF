# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from datetime import date
from math import inf, nan
from typing import cast

import pytest

from dcaf import (
    GenerationPrice,
    GenerationSettlementEvent,
    EnergyContract,
    EnergyProject,
    GenerationLinkedCashFlowPolicy,
    ProjectAnalysis,
)
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow, CashFlowStream, Generation, GenerationStream


def _annual_generation() -> GenerationStream:
    return GenerationStream(
        [
            Generation(1_000.0, date(2026, 1, 1), label="2026 generation"),
            Generation(1_000.0, date(2027, 1, 1), label="2027 generation"),
            Generation(1_000.0, date(2028, 1, 1), label="2028 generation"),
        ]
    )


def _two_year_generation() -> GenerationStream:
    return GenerationStream(
        [
            Generation(100.0, date(2026, 1, 1)),
            Generation(100.0, date(2027, 1, 1)),
        ]
    )


def _fraction_of_generation_contract(
    generation_share: float = 0.50,
    price: float = 45.0,
) -> EnergyContract:
    return EnergyContract.fraction_of_generation(
        generation_share=generation_share,
        price=GenerationPrice.fixed(price),
        start=date(2026, 1, 1),
        end=date(2027, 1, 1),
    )


def _analyze_scheduled_contract(
    *,
    generation: GenerationStream,
    contract: EnergyContract,
    name: str = "revenue:scheduled_ppa",
) -> ProjectAnalysis:
    return (
        EnergyProject()
        .generation_stream(stream=generation)
        .generation_revenue_contract(name=name, contract=contract)
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(35.0),
        )
        .analyze()
    )


@pytest.fixture
def annual_daily_generation() -> GenerationStream:
    return GenerationStream(
        [
            Generation(
                365.0,
                period_start=date(2026, 1, 1),
                period_end=date(2027, 1, 1),
            )
        ]
    )


def test_generation_revenue_contract_and_remainder_split_ppa_and_merchant_revenue():
    project = (
        EnergyProject()
        .generation_stream(stream=_annual_generation())
        .tax(rate=0.20)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=0.70,
                price=GenerationPrice.fixed(45.0),
                start=date(2026, 1, 1),
                end=date(2028, 1, 1),
                label="PPA Revenue",
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(35.0),
            label="Merchant Revenue",
        )
    )

    analysis = project.analyze()

    ppa = analysis.cashflow_components["revenue:ppa"]
    merchant = analysis.cashflow_components["revenue:merchant"]

    assert [flow.amount for flow in ppa] == pytest.approx([31_500.0, 31_500.0])
    assert [flow.amount for flow in merchant] == pytest.approx([10_500.0, 10_500.0, 35_000.0])
    assert [flow.label for flow in ppa] == ["PPA Revenue", "PPA Revenue"]
    assert [flow.label for flow in merchant] == [
        "Merchant Revenue",
        "Merchant Revenue",
        "Merchant Revenue",
    ]

    assert {flow.pro_forma_category for flow in ppa} == {ProFormaCategory.REVENUE}
    assert {flow.tax_treatment for flow in ppa} == {TaxTreatment.TAXABLE}
    assert analysis.taxable_income.sum() == pytest.approx(119_000.0)
    assert analysis.taxes.sum() == pytest.approx(-23_800.0)

    row_map = analysis.pro_forma(period="year").row_map()
    assert row_map["Revenues"] == pytest.approx((42_000.0, 42_000.0, 35_000.0))
    assert row_map["revenue:ppa"] == pytest.approx((31_500.0, 31_500.0, 0.0))
    assert row_map["revenue:merchant"] == pytest.approx((10_500.0, 10_500.0, 35_000.0))


def test_fixed_and_fraction_contracts_plus_remainder_conserve_each_generation_event():
    """At $1/MWh, revenue directly exposes each component's delivered MWh."""
    generation = GenerationStream(
        [
            Generation(1_000.0, date(2026, 12, 31)),
            Generation(1_200.0, date(2027, 12, 31)),
            Generation(900.0, date(2028, 12, 31)),
        ]
    )
    unit_price_by_date = {entry.date: 1.0 for entry in generation}

    analysis = (
        EnergyProject()
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:fixed_volume_ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=250.0,
                price=GenerationPrice.schedule(unit_price_by_date),
                start=date(2026, 1, 1),
                end=date(2029, 1, 1),
            ),
        )
        .generation_revenue_contract(
            name="revenue:fraction_ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=0.50,
                price=GenerationPrice.callable(lambda _event: 1.0),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    amounts_by_component = {
        name: {flow.date: flow.amount for flow in analysis.cashflow_components[name]}
        for name in ("revenue:fixed_volume_ppa", "revenue:fraction_ppa", "revenue:merchant")
    }
    for entry in generation:
        expected_fixed_mwh = 250.0
        expected_fraction_mwh = entry.amount_mwh * 0.50
        expected_remainder_mwh = entry.amount_mwh - expected_fixed_mwh - expected_fraction_mwh

        assert amounts_by_component["revenue:fixed_volume_ppa"][entry.date] == pytest.approx(
            expected_fixed_mwh
        )
        assert amounts_by_component["revenue:fraction_ppa"][entry.date] == pytest.approx(
            expected_fraction_mwh
        )
        assert amounts_by_component["revenue:merchant"][entry.date] == pytest.approx(
            expected_remainder_mwh
        )
        assert sum(component[entry.date] for component in amounts_by_component.values()) == (
            pytest.approx(entry.amount_mwh)
        )


def test_generation_revenue_replaces_revenue_from_generation_for_whole_project_sales():
    analysis = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(200.0, date(2027, 1, 1)),
                ]
            )
        )
        .generation_revenue(
            price_policy=GenerationPrice.fixed(50.0),
            label="Generation Revenue",
        )
        .analyze()
    )

    revenue = analysis.cashflow_components["revenue"]

    assert [flow.amount for flow in revenue] == pytest.approx([5_000.0, 10_000.0])
    assert [flow.label for flow in revenue] == [
        "Generation Revenue",
        "Generation Revenue",
    ]
    assert not hasattr(EnergyProject(), "revenue_from_generation")


def test_generation_revenue_float_price_is_unescalated_without_project_default():
    analysis = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(200.0, date(2027, 1, 1)),
                ]
            )
        )
        .generation_revenue(price=50.0)
        .analyze()
    )

    revenue = analysis.cashflow_components["revenue"]

    assert [flow.amount for flow in revenue] == pytest.approx([5_000.0, 10_000.0])


@pytest.mark.parametrize(("price", "expected_revenue"), [(0, 0.0), (-50, -5_000.0)])
def test_generation_revenue_accepts_zero_and_negative_scalar_prices(
    price: int,
    expected_revenue: float,
):
    analysis = (
        EnergyProject()
        .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
        .generation_revenue(price=price)
        .analyze()
    )

    assert analysis.cashflow_components["revenue"].sum() == pytest.approx(expected_revenue)


@pytest.mark.parametrize("price", [float("nan"), float("inf"), float("-inf")])
def test_generation_revenue_rejects_non_finite_scalar_prices(price: float):
    with pytest.raises(ValueError, match="generation_revenue price must be finite"):
        EnergyProject().generation_revenue(price=price)


def test_generation_revenue_rejects_bool_scalar_price():
    with pytest.raises(TypeError, match="generation_revenue price must be a finite scalar"):
        EnergyProject().generation_revenue(price=True)  # type: ignore[arg-type]


def test_generation_revenue_float_price_uses_project_default_escalation():
    analysis = (
        EnergyProject()
        .default_escalation(rate=0.10)
        .generation_stream(stream=_two_year_generation())
        .generation_revenue(price=50.0)
        .analyze()
    )

    revenue = analysis.cashflow_components["revenue"]

    assert [flow.amount for flow in revenue] == pytest.approx([5_000.0, 5_500.0])


def test_generation_revenue_float_price_uses_earliest_date_in_unsorted_generation():
    analysis = (
        EnergyProject()
        .default_escalation(rate=0.10)
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2027, 1, 1)),
                    Generation(100.0, date(2026, 1, 1)),
                ]
            )
        )
        .generation_revenue(price=50.0)
        .analyze()
    )

    revenue = analysis.cashflow_components["revenue"]

    assert [flow.amount for flow in revenue] == pytest.approx([5_000.0, 5_500.0])


def test_generation_revenue_float_price_uses_default_escalation_reference_date():
    analysis = (
        EnergyProject()
        .default_escalation(rate=0.10, amount_reference_date=date(2025, 1, 1))
        .generation_stream(stream=_two_year_generation())
        .generation_revenue(price=50.0)
        .analyze()
    )

    revenue = analysis.cashflow_components["revenue"]

    assert [flow.amount for flow in revenue] == pytest.approx([5_500.0, 6_050.0])


@pytest.mark.parametrize(
    "price",
    [
        GenerationPrice.fixed(50.0),
        GenerationPrice.schedule(
            {
                date(2026, 1, 1): 50.0,
                date(2027, 1, 1): 50.0,
            }
        ),
        GenerationPrice.callable(lambda _event: 50.0),
    ],
    ids=["fixed", "schedule", "callable"],
)
def test_explicit_generation_price_ignores_project_default_escalation(
    price: GenerationPrice,
):
    analysis = (
        EnergyProject()
        .default_escalation(rate=0.10)
        .generation_stream(stream=_two_year_generation())
        .generation_revenue(price_policy=price)
        .analyze()
    )

    revenue = analysis.cashflow_components["revenue"]

    assert [flow.amount for flow in revenue] == pytest.approx([5_000.0, 5_000.0])


def test_generation_revenue_rejects_generation_price_in_scalar_price_argument():
    invalid_price = cast(float, GenerationPrice.fixed(50.0))

    with pytest.raises(TypeError, match="generation_revenue price must be a finite scalar"):
        EnergyProject().generation_revenue(price=invalid_price)


def test_generation_revenue_requires_exactly_one_price_source():
    with pytest.raises(ValueError, match="provide exactly one of price or price_policy"):
        EnergyProject().generation_revenue()

    with pytest.raises(ValueError, match="provide exactly one of price or price_policy"):
        EnergyProject().generation_revenue(
            price=50.0,
            price_policy=GenerationPrice.fixed(50.0),
        )


def test_generation_contract_and_remainder_prices_ignore_project_default_escalation():
    analysis = (
        EnergyProject()
        .default_escalation(rate=0.10)
        .generation_stream(stream=_two_year_generation())
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=0.50,
                price=GenerationPrice.fixed(50.0),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(40.0),
        )
        .analyze()
    )

    assert [flow.amount for flow in analysis.cashflow_components["revenue:ppa"]] == (
        pytest.approx([2_500.0, 2_500.0])
    )
    assert [flow.amount for flow in analysis.cashflow_components["revenue:merchant"]] == (
        pytest.approx([2_000.0, 2_000.0])
    )


def test_generation_revenue_schedule_settles_each_event_at_its_exact_date_price():
    """Exact-date prices settle 100 MWh at $45 and 200 MWh at $47."""
    analysis = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(200.0, date(2027, 1, 1)),
                ]
            )
        )
        .generation_revenue(
            price_policy=GenerationPrice.schedule(
                {
                    date(2026, 1, 1): 45.0,
                    date(2027, 1, 1): 47.0,
                }
            )
        )
        .analyze()
    )

    assert [flow.amount for flow in analysis.cashflow_components["revenue"]] == pytest.approx(
        [4_500.0, 9_400.0]  # [100.0 * 45.0, 200.0 * 47.0]
    )


@pytest.mark.parametrize(
    ("generation", "price_schedule", "message"),
    [
        (
            GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(200.0, date(2027, 1, 1)),
                ]
            ),
            {date(2026, 1, 1): 45.0},
            "price schedule has no entry for 2027-01-01",
        ),
        (
            GenerationStream([Generation(100.0, date(2026, 1, 1))]),
            {
                date(2026, 1, 1): 45.0,
                date(2027, 1, 1): 47.0,
            },
            "revenue price schedule contains 2027-01-01.*component has no settlement",
        ),
    ],
    ids=["missing_settlement_price", "extra_scheduled_date"],
)
def test_generation_revenue_schedule_requires_an_exact_one_to_one_date_domain(
    generation: GenerationStream,
    price_schedule: dict[date, float],
    message: str,
):
    """Tests failure modes for one-to-one generation and price date matching"""
    with pytest.raises(ValueError, match=message):
        (
            EnergyProject()
            .generation_stream(stream=generation)
            .generation_revenue(price_policy=GenerationPrice.schedule(price_schedule))
            .analyze()
        )


def test_generation_revenue_callable_receives_whole_project_settlement_context() -> None:
    """Verifies that a callable function can be used to define price for a GenerationSettlementEvent"""
    prices = {date(2026, 1, 1): 45.0, date(2027, 1, 1): 47.0}
    events: list[GenerationSettlementEvent] = []

    def price(event: GenerationSettlementEvent) -> float:
        events.append(
            event
        )  # creates an external artifact that the `price` function was called for every generation event
        return prices[event.date]

    analysis = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(200.0, date(2027, 1, 1)),
                ]
            )
        )
        .generation_revenue(price_policy=GenerationPrice.callable(price))
        .analyze()
    )

    assert [flow.amount for flow in analysis.cashflow_components["revenue"]] == pytest.approx(
        [4_500.0, 9_400.0]
    )
    assert [
        (
            event.date,
            event.available_mwh,
            event.requested_mwh,
            event.delivered_mwh,
            event.shortfall_mwh,
            event.allocated_generation_share,
            event.component_name,
        )
        for event in events
    ] == [
        (date(2026, 1, 1), 100.0, 100.0, 100.0, 0.0, 1.0, "revenue"),
        (date(2027, 1, 1), 200.0, 200.0, 200.0, 0.0, 1.0, "revenue"),
    ]


def test_generation_revenue_may_only_be_called_once():
    with pytest.raises(ValueError, match="generation_revenue may only be called once"):
        (
            EnergyProject()
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .generation_revenue(price_policy=GenerationPrice.fixed(60.0))
        )


def test_generation_revenue_is_mutually_exclusive_with_contract_revenue_methods():
    whole_project = EnergyProject().generation_revenue(price_policy=GenerationPrice.fixed(50.0))

    with pytest.raises(
        ValueError,
        match="generation_revenue_contract cannot be combined with generation_revenue",
    ):
        whole_project.generation_revenue_contract(
            name="revenue:ppa",
            contract=_fraction_of_generation_contract(),
        )

    with pytest.raises(
        ValueError,
        match="generation_revenue_remainder cannot be combined with generation_revenue",
    ):
        whole_project.generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(35.0),
        )

    with pytest.raises(
        ValueError,
        match=(
            "generation_revenue cannot be combined with "
            "generation_revenue_contract or generation_revenue_remainder"
        ),
    ):
        (
            EnergyProject()
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=_fraction_of_generation_contract(),
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        )


def test_generation_revenue_contract_requires_remainder_at_analysis_time():
    with pytest.raises(
        ValueError,
        match="generation_revenue_contract requires generation_revenue_remainder",
    ):
        (
            EnergyProject()
            .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=_fraction_of_generation_contract(),
            )
            .analyze()
        )


def test_generation_revenue_remainder_requires_contract_at_analysis_time():
    with pytest.raises(
        ValueError,
        match=("generation_revenue_remainder requires at least one generation_revenue_contract"),
    ):
        (
            EnergyProject()
            .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(35.0),
            )
            .analyze()
        )


def test_generation_revenue_remainder_can_be_registered_before_contract():
    analysis = (
        EnergyProject()
        .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(35.0),
        )
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=_fraction_of_generation_contract(
                generation_share=0.25,
                price=45.0,
            ),
        )
        .analyze()
    )

    assert analysis.cashflow_components["revenue:ppa"].sum() == pytest.approx(1_125.0)
    assert analysis.cashflow_components["revenue:merchant"].sum() == pytest.approx(2_625.0)


def test_only_one_generation_revenue_remainder_is_allowed():
    with pytest.raises(
        ValueError,
        match="only one generation_revenue_remainder may be configured",
    ):
        (
            EnergyProject()
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(35.0),
            )
            .generation_revenue_remainder(
                name="revenue:merchant_2",
                price=GenerationPrice.fixed(36.0),
            )
        )


def test_generation_revenue_policy_and_remainder_prices_must_be_generation_prices():
    bad_price = cast(GenerationPrice, object())

    with pytest.raises(
        TypeError,
        match="generation_revenue price_policy must be a GenerationPrice",
    ):
        EnergyProject().generation_revenue(price_policy=bad_price)

    with pytest.raises(
        TypeError,
        match="generation_revenue_remainder price must be a GenerationPrice",
    ):
        EnergyProject().generation_revenue_remainder(
            name="revenue:merchant",
            price=bad_price,
        )


def test_generation_revenue_methods_require_generation_at_analysis_time():
    with pytest.raises(
        ValueError,
        match="generation revenue requires generation to be configured",
    ):
        EnergyProject().generation_revenue(price_policy=GenerationPrice.fixed(50.0)).analyze()

    with pytest.raises(
        ValueError,
        match="generation revenue requires generation to be configured",
    ):
        (
            EnergyProject()
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=_fraction_of_generation_contract(),
            )
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(35.0),
            )
            .analyze()
        )


def test_generation_linked_policy_names_must_be_unique():
    project = EnergyProject().generation_revenue_contract(
        name="revenue:ppa",
        contract=_fraction_of_generation_contract(),
    )

    with pytest.raises(
        ValueError,
        match="generation-linked policy name 'revenue:ppa' is already configured",
    ):
        project.generation_revenue_contract(
            name="revenue:ppa",
            contract=_fraction_of_generation_contract(generation_share=0.25),
        )

    with pytest.raises(
        ValueError,
        match="generation-linked policy name 'revenue:ppa' is already configured",
    ):
        project.generation_revenue_remainder(
            name="revenue:ppa",
            price=GenerationPrice.fixed(35.0),
        )


def test_fixed_mwh_contract_quantity_and_callable_price_use_settlement_context():
    def scarcity_price(event: GenerationSettlementEvent) -> float:
        assert event.available_mwh == pytest.approx(1_000.0)
        assert event.requested_mwh == pytest.approx(850.0)
        assert event.delivered_mwh == pytest.approx(850.0)
        assert event.shortfall_mwh == pytest.approx(0.0)
        assert event.allocated_generation_share == pytest.approx(0.85)
        return 60.0 if event.allocated_generation_share > 0.80 else 45.0

    analysis = (
        EnergyProject()
        .generation_stream(stream=GenerationStream([Generation(1_000.0, date(2026, 1, 1))]))
        .generation_revenue_contract(
            name="revenue:capacity_ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=850.0,
                price=GenerationPrice.callable(scarcity_price),
                start=date(2026, 1, 1),
                end=date(2027, 1, 1),
                label="Capacity PPA",
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(30.0),
            label="Merchant",
        )
        .analyze()
    )

    assert analysis.cashflow_components["revenue:capacity_ppa"].sum() == pytest.approx(51_000.0)
    assert analysis.cashflow_components["revenue:merchant"].sum() == pytest.approx(4_500.0)


def test_fixed_mwh_contract_prorates_partial_contract_calendar_period():
    generation = GenerationStream(
        [
            Generation(
                amount_mwh=1_200.0,
                period_start=date(2026, 7, 1),
                period_end=date(2027, 1, 1),
            )
        ]
    )
    analysis = (
        EnergyProject(frequency="month", timing="end")
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=600.0,
                frequency="year",
                price=GenerationPrice.fixed(10.0),
                start=date(2026, 7, 1),
                end=date(2027, 1, 1),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(30.0),
        )
        .analyze()
    )

    contract_revenue = analysis.cashflow_components["revenue:ppa"]
    remainder_revenue = analysis.cashflow_components["revenue:merchant"]
    assert contract_revenue.count() == 6
    assert [flow.date for flow in contract_revenue] == [
        date(2026, 7, 31),
        date(2026, 8, 31),
        date(2026, 9, 30),
        date(2026, 10, 31),
        date(2026, 11, 30),
        date(2026, 12, 31),
    ]
    expected_contract_mwh = 600.0 * 184.0 / 365.0
    assert contract_revenue.sum() == pytest.approx(expected_contract_mwh * 10.0)
    assert remainder_revenue.sum() == pytest.approx((1_200.0 - expected_contract_mwh) * 30.0)


def test_fixed_mwh_contract_retains_full_quantity_when_only_generation_is_partial():
    generation = GenerationStream(
        [
            Generation(
                amount_mwh=1_200.0,
                period_start=date(2026, 7, 1),
                period_end=date(2027, 1, 1),
            )
        ]
    )
    analysis = (
        EnergyProject(frequency="month", timing="end")
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=600.0,
                frequency="year",
                price=GenerationPrice.fixed(1.0),
                start=date(2026, 1, 1),
                end=date(2027, 1, 1),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    assert analysis.cashflow_components["revenue:ppa"].sum() == pytest.approx(600.0)
    assert analysis.cashflow_components["revenue:merchant"].sum() == pytest.approx(600.0)


@pytest.mark.parametrize(
    ("convention", "annual_mwh", "expected_contract_mwh"),
    [
        ("actual/actual", 366.0, 182.0),
        ("actual/365-no-leap", 365.0, 181.0),
    ],
)
def test_fixed_mwh_contract_proration_uses_project_day_count_convention(
    convention,
    annual_mwh,
    expected_contract_mwh,
):
    generation = GenerationStream(
        [
            Generation(
                amount_mwh=500.0,
                period_start=date(2024, 1, 1),
                period_end=date(2024, 7, 1),
            )
        ]
    )
    analysis = (
        EnergyProject(day_count_convention=convention)
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=annual_mwh,
                price=GenerationPrice.fixed(1.0),
                start=date(2024, 1, 1),
                end=date(2024, 7, 1),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    assert analysis.cashflow_components["revenue:ppa"].sum() == pytest.approx(expected_contract_mwh)


def test_fixed_mwh_contract_allocates_one_period_quantity_across_sources():
    generation = GenerationStream(
        [
            Generation(
                amount_mwh=1_000.0,
                period_start=date(2026, 1, 1),
                period_end=date(2027, 1, 1),
            ),
            Generation(
                amount_mwh=3_000.0,
                period_start=date(2026, 1, 1),
                period_end=date(2027, 1, 1),
            ),
        ]
    )
    analysis = (
        EnergyProject()
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=400.0,
                price=GenerationPrice.fixed(1.0),
                start=date(2026, 1, 1),
                end=date(2027, 1, 1),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    contract_revenue = analysis.cashflow_components["revenue:ppa"]
    assert [flow.amount for flow in contract_revenue] == pytest.approx([100.0, 300.0])
    assert contract_revenue.sum() == pytest.approx(400.0)
    assert analysis.cashflow_components["revenue:merchant"].sum() == pytest.approx(3_600.0)


def test_fixed_mwh_contract_validates_quantity_periods_before_financial_aggregation(
    annual_daily_generation: GenerationStream,
):
    with pytest.raises(
        ValueError,
        match=r"generation-linked contracts request 30.0 MWh in .*but only 28.0 MWh is available",
    ):
        (
            EnergyProject(frequency="year")
            .generation_stream(stream=annual_daily_generation)
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=EnergyContract.fixed_mwh_per_generation_event(
                    amount_mwh=30.0,
                    price=GenerationPrice.fixed(1.0),
                    start=date(2026, 1, 1),
                    end=date(2027, 1, 1),
                    frequency="month",
                ),
            )
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(1.0),
            )
            .analyze()
        )


def test_fixed_mwh_contract_aggregates_valid_quantity_periods_after_validation(
    annual_daily_generation: GenerationStream,
):
    analysis = (
        EnergyProject(frequency="year")
        .generation_stream(stream=annual_daily_generation)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fixed_mwh_per_generation_event(
                amount_mwh=28.0,
                price=GenerationPrice.fixed(1.0),
                start=date(2026, 1, 1),
                end=date(2027, 1, 1),
                frequency="month",
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    contract_revenue = analysis.cashflow_components["revenue:ppa"]
    remainder_revenue = analysis.cashflow_components["revenue:merchant"]
    assert [(flow.date, flow.amount) for flow in contract_revenue] == [
        (date(2026, 12, 31), pytest.approx(336.0))
    ]
    assert remainder_revenue.sum() == pytest.approx(29.0)
    assert contract_revenue.sum() + remainder_revenue.sum() == pytest.approx(
        annual_daily_generation.sum()
    )


def test_fraction_contract_prorates_comparisons_at_midmonth_boundaries():
    generation = GenerationStream(
        [
            Generation(
                amount_mwh=365.0,
                period_start=date(2026, 1, 1),
                period_end=date(2027, 1, 1),
            )
        ]
    )
    analysis = (
        EnergyProject(frequency="month", timing="end")
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=0.5,
                price=GenerationPrice.fixed(1.0),
                start=date(2026, 7, 15),
                end=date(2026, 10, 15),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    contract_revenue = analysis.cashflow_components["revenue:ppa"]
    remainder_revenue = analysis.cashflow_components["revenue:merchant"]
    assert [flow.date for flow in contract_revenue] == [
        date(2026, 7, 31),
        date(2026, 8, 31),
        date(2026, 9, 30),
        date(2026, 10, 14),
    ]
    assert contract_revenue.sum() == pytest.approx(46.0)
    assert remainder_revenue.sum() == pytest.approx(319.0)
    assert contract_revenue.sum() + remainder_revenue.sum() == pytest.approx(generation.sum())


def test_fraction_contract_counts_overlapping_generation_within_spanning_term():
    events: list[GenerationSettlementEvent] = []

    def record_event(event: GenerationSettlementEvent) -> float:
        events.append(event)
        return 1.0

    generation = GenerationStream(
        [
            Generation(
                amount_mwh=151.0,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 6, 1),
            ),
            Generation(
                amount_mwh=153.0,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 9, 1),
            ),
        ]
    )
    analysis = (
        EnergyProject()
        .generation_stream(stream=generation)
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=1.0,
                price=GenerationPrice.callable(record_event),
                start=date(2026, 3, 1),
                end=date(2026, 7, 1),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    assert [(event.period_start, event.period_end) for event in events] == [
        (date(2026, 3, 1), date(2026, 6, 1)),
        (date(2026, 4, 1), date(2026, 7, 1)),
    ]
    assert [event.available_mwh for event in events] == pytest.approx([92.0, 91.0])
    assert analysis.cashflow_components["revenue:ppa"].sum() == pytest.approx(183.0)
    assert analysis.cashflow_components["revenue:merchant"].sum() == pytest.approx(121.0)


def test_custom_mwh_generation_schedule_contract_quantity_and_scheduled_price_match_dates():
    analysis = (
        EnergyProject()
        .generation_stream(stream=_annual_generation())
        .generation_revenue_contract(
            name="revenue:scheduled_ppa",
            contract=EnergyContract.custom_mwh_generation_schedule(
                requested_generation=GenerationStream(
                    [
                        Generation(100.0, date(2026, 1, 1)),
                        Generation(110.0, date(2027, 1, 1)),
                    ]
                ),
                price=GenerationPrice.schedule(
                    {
                        date(2026, 1, 1): 45.0,
                        date(2027, 1, 1): 47.0,
                    }
                ),
                label="Scheduled PPA",
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(35.0),
        )
        .analyze()
    )

    assert [flow.amount for flow in analysis.cashflow_components["revenue:scheduled_ppa"]] == (
        pytest.approx([4_500.0, 5_170.0])
    )
    assert [flow.amount for flow in analysis.cashflow_components["revenue:merchant"]] == (
        pytest.approx([31_500.0, 31_150.0, 35_000.0])
    )


def test_custom_mwh_generation_schedule_matches_capacity_based_generation():
    analysis = (
        EnergyProject()
        .generation(
            capacity_mw=1.0,
            operations_start=date(2026, 1, 1),
            operations_end=date(2027, 1, 1),
        )
        .generation_revenue_contract(
            name="revenue:scheduled_ppa",
            contract=EnergyContract.custom_mwh_generation_schedule(
                requested_generation=GenerationStream(
                    [
                        Generation(
                            100.0,
                            period_start=date(2026, 1, 1),
                            period_end=date(2027, 1, 1),
                        )
                    ]
                ),
                price=GenerationPrice.schedule({date(2026, 12, 31): 50.0}),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(30.0),
        )
        .analyze()
    )

    assert analysis.generation.entries[0].date is None
    assert analysis.generation.entries[0].period_start == date(2026, 1, 1)
    assert analysis.generation.entries[0].period_end == date(2027, 1, 1)
    assert analysis.cashflow_components["revenue:scheduled_ppa"].sum() == pytest.approx(5_000.0)


def test_custom_mwh_generation_schedule_rejects_date_missing_from_project_generation():
    with pytest.raises(
        ValueError,
        match="revenue:scheduled_ppa custom MWh period.*found 0",
    ):
        _analyze_scheduled_contract(
            generation=GenerationStream([Generation(1_000.0, date(2026, 1, 1))]),
            contract=EnergyContract.custom_mwh_generation_schedule(
                requested_generation=GenerationStream([Generation(100.0, date(2027, 1, 1))]),
                price=GenerationPrice.fixed(45.0),
            ),
        )


def test_custom_mwh_generation_schedule_allocates_request_across_adjacent_sources():
    generation = GenerationStream(
        [
            Generation(
                181.0,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 7, 1),
            ),
            Generation(
                184.0,
                period_start=date(2026, 7, 1),
                period_end=date(2027, 1, 1),
            ),
        ]
    )
    analysis = _analyze_scheduled_contract(
        generation=generation,
        contract=EnergyContract.custom_mwh_generation_schedule(
            requested_generation=GenerationStream(
                [
                    Generation(
                        182.5,
                        period_start=date(2026, 1, 1),
                        period_end=date(2027, 1, 1),
                    )
                ]
            ),
            price=GenerationPrice.fixed(1.0),
        ),
    )

    contract_revenue = analysis.cashflow_components["revenue:scheduled_ppa"]
    remainder_revenue = analysis.cashflow_components["revenue:merchant"]
    assert [flow.date for flow in contract_revenue] == [date(2026, 6, 30), date(2026, 12, 31)]
    assert [flow.amount for flow in contract_revenue] == pytest.approx([90.5, 92.0])
    assert [flow.amount for flow in remainder_revenue] == pytest.approx([90.5 * 35.0, 92.0 * 35.0])
    assert contract_revenue.sum() + remainder_revenue.sum() / 35.0 == pytest.approx(
        generation.sum()
    )


def test_custom_mwh_generation_schedule_prorates_request_across_overlapping_sources():
    generation = GenerationStream(
        [
            Generation(
                243.0,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 9, 1),
            ),
            Generation(
                245.0,
                period_start=date(2026, 5, 1),
                period_end=date(2027, 1, 1),
            ),
        ]
    )
    analysis = _analyze_scheduled_contract(
        generation=generation,
        contract=EnergyContract.custom_mwh_generation_schedule(
            requested_generation=GenerationStream(
                [
                    Generation(
                        368.0,
                        period_start=date(2026, 3, 1),
                        period_end=date(2026, 11, 1),
                    )
                ]
            ),
            price=GenerationPrice.fixed(1.0),
        ),
    )

    contract_revenue = analysis.cashflow_components["revenue:scheduled_ppa"]
    remainder_revenue = analysis.cashflow_components["revenue:merchant"]
    # Each source supplies 1 MWh/day, and the request overlaps each for 184 days.
    assert [flow.date for flow in contract_revenue] == [date(2026, 8, 31), date(2026, 10, 31)]
    assert [flow.amount for flow in contract_revenue] == pytest.approx([184.0, 184.0])
    assert [flow.amount for flow in remainder_revenue] == pytest.approx([59.0 * 35.0, 61.0 * 35.0])
    assert contract_revenue.sum() + remainder_revenue.sum() / 35.0 == pytest.approx(
        generation.sum()
    )


def test_custom_mwh_generation_schedule_rejects_request_above_matched_generation():
    with pytest.raises(
        ValueError,
        match=(
            "generation-linked contracts request 120.0 MWh in .*but only 100.0 MWh is available"
        ),
    ):
        _analyze_scheduled_contract(
            generation=GenerationStream([Generation(100.0, date(2026, 1, 1))]),
            contract=EnergyContract.custom_mwh_generation_schedule(
                requested_generation=GenerationStream([Generation(120.0, date(2026, 1, 1))]),
                price=GenerationPrice.fixed(45.0),
            ),
        )


def test_custom_mwh_generation_schedule_rejects_shortfall_across_adjacent_sources():
    with pytest.raises(
        ValueError,
        match=r"generation-linked contracts request .*but only .* is available",
    ):
        _analyze_scheduled_contract(
            generation=GenerationStream(
                [
                    Generation(
                        181.0,
                        period_start=date(2026, 1, 1),
                        period_end=date(2026, 7, 1),
                    ),
                    Generation(
                        184.0,
                        period_start=date(2026, 7, 1),
                        period_end=date(2027, 1, 1),
                    ),
                ]
            ),
            contract=EnergyContract.custom_mwh_generation_schedule(
                requested_generation=GenerationStream(
                    [
                        Generation(
                            400.0,
                            period_start=date(2026, 1, 1),
                            period_end=date(2027, 1, 1),
                        )
                    ]
                ),
                price=GenerationPrice.fixed(45.0),
            ),
        )


def test_custom_mwh_generation_schedule_validates_sequential_requests_before_aggregation(
    annual_daily_generation: GenerationStream,
):
    with pytest.raises(
        ValueError,
        match=r"generation-linked contracts request 190.0 MWh in .*but only 184.0 MWh is available",
    ):
        _analyze_scheduled_contract(
            generation=annual_daily_generation,
            contract=EnergyContract.custom_mwh_generation_schedule(
                requested_generation=GenerationStream(
                    [
                        Generation(
                            10.0,
                            period_start=date(2026, 1, 1),
                            period_end=date(2026, 7, 1),
                        ),
                        Generation(
                            190.0,
                            period_start=date(2026, 7, 1),
                            period_end=date(2027, 1, 1),
                        ),
                    ]
                ),
                price=GenerationPrice.fixed(1.0),
            ),
        )


def test_custom_mwh_generation_schedule_aggregates_valid_sequential_requests(
    annual_daily_generation: GenerationStream,
):
    events: list[GenerationSettlementEvent] = []

    def record_event(event: GenerationSettlementEvent) -> float:
        events.append(event)
        return 1.0

    analysis = _analyze_scheduled_contract(
        generation=annual_daily_generation,
        contract=EnergyContract.custom_mwh_generation_schedule(
            requested_generation=GenerationStream(
                [
                    Generation(
                        10.0,
                        period_start=date(2026, 1, 1),
                        period_end=date(2026, 7, 1),
                    ),
                    Generation(
                        180.0,
                        period_start=date(2026, 7, 1),
                        period_end=date(2027, 1, 1),
                    ),
                ]
            ),
            price=GenerationPrice.callable(record_event),
        ),
    )

    contract_revenue = analysis.cashflow_components["revenue:scheduled_ppa"]
    remainder_revenue = analysis.cashflow_components["revenue:merchant"]
    assert [(flow.date, flow.amount) for flow in contract_revenue] == [
        (date(2026, 12, 31), pytest.approx(190.0))
    ]
    assert len(events) == 1
    assert (events[0].period_start, events[0].period_end) == (
        date(2026, 1, 1),
        date(2027, 1, 1),
    )
    assert events[0].available_mwh == pytest.approx(365.0)
    assert remainder_revenue.sum() / 35.0 == pytest.approx(175.0)
    assert contract_revenue.sum() + remainder_revenue.sum() / 35.0 == pytest.approx(
        annual_daily_generation.sum()
    )


def test_custom_mwh_generation_schedule_validates_overlapping_requests_before_aggregation(
    annual_daily_generation: GenerationStream,
):
    with pytest.raises(
        ValueError,
        match=r"generation-linked contracts request 100.0 MWh in .*but only 91.0 MWh is available",
    ):
        _analyze_scheduled_contract(
            generation=annual_daily_generation,
            contract=EnergyContract.custom_mwh_generation_schedule(
                requested_generation=GenerationStream(
                    [
                        Generation(
                            100.0,
                            period_start=date(2026, 1, 1),
                            period_end=date(2026, 7, 1),
                        ),
                        Generation(
                            100.0,
                            period_start=date(2026, 4, 1),
                            period_end=date(2026, 10, 1),
                        ),
                    ]
                ),
                price=GenerationPrice.fixed(1.0),
            ),
        )


def test_custom_mwh_generation_schedule_reports_unique_availability_after_aggregation(
    annual_daily_generation: GenerationStream,
):
    events: list[GenerationSettlementEvent] = []

    def record_event(event: GenerationSettlementEvent) -> float:
        events.append(event)
        return 1.0

    analysis = _analyze_scheduled_contract(
        generation=annual_daily_generation,
        contract=EnergyContract.custom_mwh_generation_schedule(
            requested_generation=GenerationStream(
                [
                    Generation(
                        50.0,
                        period_start=date(2026, 1, 1),
                        period_end=date(2026, 7, 1),
                    ),
                    Generation(
                        50.0,
                        period_start=date(2026, 4, 1),
                        period_end=date(2026, 10, 1),
                    ),
                ]
            ),
            price=GenerationPrice.callable(record_event),
        ),
    )

    contract_revenue = analysis.cashflow_components["revenue:scheduled_ppa"]
    remainder_revenue = analysis.cashflow_components["revenue:merchant"]
    assert [(flow.date, flow.amount) for flow in contract_revenue] == [
        (date(2026, 9, 30), pytest.approx(100.0))
    ]
    assert len(events) == 1
    assert events[0].available_mwh == pytest.approx(273.0)
    assert remainder_revenue.sum() / 35.0 == pytest.approx(265.0)
    assert contract_revenue.sum() + remainder_revenue.sum() / 35.0 == pytest.approx(
        annual_daily_generation.sum()
    )


def test_scheduled_generation_price_rejects_date_missing_from_project_generation():
    with pytest.raises(
        ValueError,
        match="revenue:ppa price schedule contains 2027-01-01.*component has no settlement",
    ):
        _analyze_scheduled_contract(
            generation=GenerationStream([Generation(100.0, date(2026, 1, 1))]),
            name="revenue:ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=0.50,
                price=GenerationPrice.schedule(
                    {
                        date(2026, 1, 1): 45.0,
                        date(2027, 1, 1): 47.0,
                    }
                ),
            ),
        )


def test_contract_term_uses_inclusive_start_and_exclusive_end_entry_date_rule():
    analysis = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2025, 12, 31)),
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(100.0, date(2028, 1, 1)),
                ]
            )
        )
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=EnergyContract.fraction_of_generation(
                generation_share=1.0,
                price=GenerationPrice.fixed(10.0),
                start=date(2026, 1, 1),
                end=date(2028, 1, 1),
            ),
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(10.0),
        )
        .analyze()
    )

    ppa = analysis.cashflow_components["revenue:ppa"]
    assert [flow.date for flow in ppa] == [date(2026, 1, 1)]
    assert ppa.sum() == pytest.approx(1_000.0)


@pytest.mark.parametrize(
    "contract",
    [
        EnergyContract.fraction_of_generation(
            generation_share=0.50,
            price=GenerationPrice.fixed(1.0),
        ),
        EnergyContract.fixed_mwh_per_generation_event(
            amount_mwh=50.0,
            price=GenerationPrice.fixed(1.0),
            start=date(2026, 1, 1),
            end=date(2027, 1, 1),
        ),
        EnergyContract.custom_mwh_generation_schedule(
            requested_generation=GenerationStream(
                [
                    Generation(50.0, date(2026, 1, 1)),
                    Generation(10.0, date(2026, 5, 11)),
                ]
            ),
            price=GenerationPrice.fixed(1.0),
        ),
    ],
    ids=["fraction", "fixed_mwh", "custom_schedule"],
)
def test_generation_contracts_ignore_negative_generation_events(contract: EnergyContract):
    """Contracts settle only nonnegative delivered generation; outages remain in the remainder."""
    analysis = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(-20.0, date(2026, 5, 11)),
                ]
            )
        )
        .generation_revenue_contract(
            name="revenue:ppa",
            contract=contract,
        )
        .generation_revenue_remainder(
            name="revenue:merchant",
            price=GenerationPrice.fixed(1.0),
        )
        .analyze()
    )

    ppa = analysis.cashflow_components["revenue:ppa"]
    merchant = analysis.cashflow_components["revenue:merchant"]
    assert [flow.amount for flow in ppa] == pytest.approx([50.0])
    assert [flow.date for flow in ppa] == [date(2026, 1, 1)]
    assert merchant.sum() == pytest.approx(30.0)


def test_overlapping_contracts_validate_against_gross_generation_not_registration_order():
    with pytest.raises(
        ValueError,
        match=(
            "generation-linked contracts request 130.0 MWh in .*but only 100.0 MWh is available"
        ),
    ):
        (
            EnergyProject()
            .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
            .generation_revenue_contract(
                name="revenue:first",
                contract=EnergyContract.fraction_of_generation(
                    generation_share=0.80,
                    price=GenerationPrice.fixed(10.0),
                    start=date(2026, 1, 1),
                    end=date(2027, 1, 1),
                ),
            )
            .generation_revenue_contract(
                name="revenue:second",
                contract=EnergyContract.fraction_of_generation(
                    generation_share=0.50,
                    price=GenerationPrice.fixed(20.0),
                    start=date(2026, 1, 1),
                    end=date(2027, 1, 1),
                ),
            )
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(30.0),
            )
            .analyze()
        )


def test_fixed_quantity_shortfall_error_identifies_contract_date_requested_and_available():
    with pytest.raises(
        ValueError,
        match=(
            "generation-linked contracts request 120.0 MWh in .*but only 100.0 MWh is available"
        ),
    ):
        (
            EnergyProject()
            .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=EnergyContract.fixed_mwh_per_generation_event(
                    amount_mwh=120.0,
                    price=GenerationPrice.fixed(10.0),
                    start=date(2026, 1, 1),
                    end=date(2027, 1, 1),
                ),
            )
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(30.0),
            )
            .analyze()
        )


def test_fixed_quantity_validates_prorated_available_generation_period():
    with pytest.raises(
        ValueError,
        match=(
            r"generation-linked contracts request 201\.6 MWh in "
            r"\[2026-07-01, 2027-01-01\), but only 184\.0 MWh is available"
        ),
    ):
        (
            EnergyProject()
            .generation_stream(
                stream=GenerationStream(
                    [
                        Generation(
                            amount_mwh=365.0,
                            period_start=date(2026, 1, 1),
                            period_end=date(2027, 1, 1),
                        )
                    ]
                )
            )
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=EnergyContract.fixed_mwh_per_generation_event(
                    amount_mwh=400.0,
                    price=GenerationPrice.fixed(1.0),
                    start=date(2026, 7, 1),
                    end=date(2027, 1, 1),
                ),
            )
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(1.0),
            )
            .analyze()
        )


def test_missing_scheduled_price_entry_raises_clear_error():
    with pytest.raises(ValueError, match="price schedule has no entry for 2027-01-01"):
        (
            EnergyProject()
            .generation_stream(
                stream=GenerationStream(
                    [
                        Generation(100.0, date(2026, 1, 1)),
                        Generation(100.0, date(2027, 1, 1)),
                    ]
                )
            )
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=EnergyContract.fraction_of_generation(
                    generation_share=0.50,
                    price=GenerationPrice.schedule({date(2026, 1, 1): 10.0}),
                    start=date(2026, 1, 1),
                    end=date(2028, 1, 1),
                ),
            )
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(30.0),
            )
            .analyze()
        )


def test_callable_price_must_return_finite_value():
    with pytest.raises(ValueError, match="generation price must be finite"):
        (
            EnergyProject()
            .generation_stream(stream=GenerationStream([Generation(100.0, date(2026, 1, 1))]))
            .generation_revenue_contract(
                name="revenue:ppa",
                contract=EnergyContract.fraction_of_generation(
                    generation_share=0.50,
                    price=GenerationPrice.callable(lambda event: nan),
                    start=date(2026, 1, 1),
                    end=date(2027, 1, 1),
                ),
            )
            .generation_revenue_remainder(
                name="revenue:merchant",
                price=GenerationPrice.fixed(30.0),
            )
            .analyze()
        )


@pytest.mark.parametrize("generation_share", [-0.01, 1.01, nan])
def test_fraction_of_generation_contract_rejects_invalid_generation_share(generation_share):
    with pytest.raises(ValueError, match="generation_share must be between 0 and 1"):
        EnergyContract.fraction_of_generation(
            generation_share=generation_share,
            price=GenerationPrice.fixed(45.0),
            start=date(2026, 1, 1),
            end=date(2027, 1, 1),
        )


def test_contract_rejects_invalid_date_range():
    with pytest.raises(ValueError, match="end must be after start"):
        EnergyContract.fraction_of_generation(
            generation_share=0.50,
            price=GenerationPrice.fixed(45.0),
            start=date(2027, 1, 1),
            end=date(2027, 1, 1),
        )


def test_fixed_contract_rejects_negative_requested_mwh():
    with pytest.raises(ValueError, match="amount_mwh must be non-negative"):
        EnergyContract.fixed_mwh_per_generation_event(
            amount_mwh=-1.0,
            price=GenerationPrice.fixed(45.0),
            start=date(2026, 1, 1),
            end=date(2027, 1, 1),
        )


def test_custom_mwh_generation_schedule_contract_rejects_negative_requested_mwh():
    with pytest.raises(ValueError, match="requested_generation amounts must be non-negative"):
        EnergyContract.custom_mwh_generation_schedule(
            requested_generation=GenerationStream([Generation(-1.0, date(2026, 1, 1))]),
            price=GenerationPrice.fixed(45.0),
        )


def test_custom_mwh_generation_schedule_contract_rejects_duplicate_generation_periods():
    with pytest.raises(ValueError, match="requested_generation periods must be unique"):
        EnergyContract.custom_mwh_generation_schedule(
            requested_generation=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(110.0, date(2026, 1, 1)),
                ]
            ),
            price=GenerationPrice.fixed(45.0),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"quantity_mode": cast(object, "unsupported")}, "quantity_mode must be"),
        (
            {"quantity_mode": "fraction_of_generation", "generation_share": 0.5, "amount_mwh": 1.0},
            "fraction-of-generation contract cannot include",
        ),
        (
            {
                "quantity_mode": "fixed_mwh_per_generation_event",
                "amount_mwh": 1.0,
                "generation_share": 0.5,
            },
            "fixed-MWh contract cannot include",
        ),
        (
            {
                "quantity_mode": "custom_mwh_generation_schedule",
                "requested_generation": GenerationStream(),
                "amount_mwh": 1.0,
            },
            "custom generation schedule contract cannot include",
        ),
    ],
)
def test_direct_energy_contract_construction_rejects_invalid_quantity_states(kwargs, message):
    with pytest.raises(ValueError, match=message):
        EnergyContract(price=GenerationPrice.fixed(45.0), **kwargs)


def test_generation_price_rejects_non_finite_price():
    with pytest.raises(ValueError, match="price must be finite"):
        GenerationPrice.fixed(nan)


def test_direct_generation_price_construction_canonicalizes_valid_states():
    def callback(_event: GenerationSettlementEvent) -> float:
        return 42.0

    fixed = GenerationPrice(mode="fixed", fixed_price=45)
    schedule = GenerationPrice(
        mode="schedule",
        price_schedule=((date(2027, 1, 1), 47), (date(2026, 1, 1), 45)),
    )
    callable_price = GenerationPrice(mode="callable", price_callable=callback)

    assert fixed.fixed_price == 45.0
    assert schedule.price_schedule == ((date(2026, 1, 1), 45.0), (date(2027, 1, 1), 47.0))
    assert callable_price.price_callable is callback


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": cast(object, "unsupported")}, "mode must be"),
        ({"mode": "fixed"}, "requires fixed_price"),
        ({"mode": "schedule"}, "must contain at least one entry"),
        ({"mode": "callable"}, "requires a callable callback"),
        (
            {"mode": "fixed", "fixed_price": 45.0, "price_schedule": ((date(2026, 1, 1), 46.0),)},
            "cannot include a schedule or callback",
        ),
        (
            {
                "mode": "schedule",
                "fixed_price": 45.0,
                "price_schedule": ((date(2026, 1, 1), 46.0),),
            },
            "cannot include fixed_price or callback",
        ),
        (
            {
                "mode": "schedule",
                "price_schedule": ((date(2026, 1, 1), 46.0),),
                "price_callable": lambda _event: 45.0,
            },
            "cannot include fixed_price or callback",
        ),
        (
            {"mode": "callable", "fixed_price": 45.0, "price_callable": lambda _event: 45.0},
            "cannot include fixed_price or a schedule",
        ),
        (
            {
                "mode": "callable",
                "price_schedule": ((date(2026, 1, 1), 45.0),),
                "price_callable": lambda _event: 45.0,
            },
            "cannot include fixed_price or a schedule",
        ),
        ({"mode": "fixed", "fixed_price": inf}, "fixed_price must be finite"),
        (
            {"mode": "schedule", "price_schedule": ((date(2026, 1, 1), nan),)},
            "price schedule prices must be finite",
        ),
        (
            {
                "mode": "schedule",
                "price_schedule": ((date(2026, 1, 1), 45.0), (date(2026, 1, 1), 46.0)),
            },
            "price schedule dates must be unique",
        ),
        (
            {"mode": "callable", "price_callable": cast(object, 45.0)},
            "requires a callable callback",
        ),
    ],
)
def test_direct_generation_price_construction_rejects_invalid_states(kwargs, message):
    with pytest.raises(ValueError, match=message):
        GenerationPrice(**kwargs)


def test_generation_linked_policy_registers_generic_custom_policy():
    class BonusPolicy:
        def cashflows(self, generation: GenerationStream) -> CashFlowStream:
            return CashFlowStream(
                [
                    CashFlow(
                        amount=generation.sum() * 2.0,
                        date=date(2026, 1, 1),
                        label="Generation Bonus",
                        pro_forma_category=ProFormaCategory.REVENUE,
                        tax_treatment=TaxTreatment.TAXABLE,
                    )
                ]
            )

    assert isinstance(BonusPolicy(), GenerationLinkedCashFlowPolicy)

    analysis = (
        EnergyProject()
        .generation_stream(
            stream=GenerationStream(
                [
                    Generation(100.0, date(2026, 1, 1)),
                    Generation(200.0, date(2027, 1, 1)),
                ]
            )
        )
        .generation_linked_policy(name="revenue:bonus", policy=BonusPolicy())
        .analyze()
    )

    assert analysis.cashflow_components["revenue:bonus"].sum() == pytest.approx(600.0)
