"""Tests for construction spend schedule APIs and helpers."""

import inspect
from datetime import date

import pytest

from dcaf import (
    BELL_CURVE,
    CashFlowStream,
    CashFlowTags,
    ConstructionFinancing,
    ConstructionSpendBuilder,
    FLAT_CURVE,
    LINEAR_CURVE,
    RAMPED_CURVE,
    SpendProfile,
    TRIANGLE_CURVE,
    construction_spend_schedule,
)
from dcaf.construction import ConstructionSpendConfig, _validate_schedule
from dcaf.utils import timedelta_fractional_years


@pytest.mark.parametrize(
    "curve",
    [FLAT_CURVE, BELL_CURVE, RAMPED_CURVE, TRIANGLE_CURVE, LINEAR_CURVE],
    ids=["flat", "bell", "ramped", "triangle", "linear"],
)
def test_curve_sums_to_one(curve):
    assert abs(sum(point[1] for point in curve) - 1.0) < 1e-6


@pytest.mark.parametrize(
    "curve",
    [FLAT_CURVE, BELL_CURVE, RAMPED_CURVE, TRIANGLE_CURVE, LINEAR_CURVE],
    ids=["flat", "bell", "ramped", "triangle", "linear"],
)
def test_curve_starts_at_zero_ends_at_one(curve):
    assert curve[0][0] == 0.0
    assert curve[-1][0] == 1.0
    assert curve[-1][1] == 0.0


def test_default_profile_is_flat_and_visible_in_signatures():
    function_default = inspect.signature(construction_spend_schedule).parameters["profile"].default
    builder_default = inspect.signature(ConstructionSpendBuilder).parameters["profile"].default

    assert function_default == "flat"
    assert builder_default == "flat"


@pytest.mark.parametrize(
    "name",
    ["flat", "bell", "ramped", "triangle", "linear"],
)
def test_named_profiles_lookup(name):
    profile = SpendProfile.curve(name)  # type: ignore[arg-type]
    assert len(profile.schedule) > 0
    assert profile.name == name


def test_default_profile_matches_explicit_flat():
    implicit = construction_spend_schedule(
        1_200_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
    )
    explicit = construction_spend_schedule(
        1_200_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="flat",
    )

    assert [cf.date for cf in implicit.flows] == [cf.date for cf in explicit.flows]
    assert [cf.amount for cf in implicit.flows] == pytest.approx(
        [cf.amount for cf in explicit.flows]
    )


def test_builder_is_immutable():
    base = ConstructionSpendBuilder(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
    )
    escalated = base.escalation(0.05)

    assert base.config.escalation_rate == 0.0
    assert escalated.config.escalation_rate == 0.05


def test_profile_selection_is_single_source_of_truth():
    custom_schedule = ((0.0, 0.6), (0.5, 0.4), (1.0, 0.0))

    builder = (
        ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        .curve("linear")
        .schedule(custom_schedule)
    )

    assert builder.config.profile.name is None
    assert builder.config.profile.schedule == custom_schedule


def test_curve_can_override_custom_schedule():
    custom_schedule = ((0.0, 0.6), (0.5, 0.4), (1.0, 0.0))

    builder = (
        ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1))
        .schedule(custom_schedule)
        .curve("linear")
    )

    assert builder.config.profile.name == "linear"
    assert builder.config.profile.schedule == LINEAR_CURVE


def test_construction_spend_config_normalizes_period():
    config = ConstructionSpendConfig(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        period="quarter",
    )

    assert config.period.value == "quarter"
    assert config.profile.name == "flat"


def test_construction_spend_config_normalizes_profile_and_financing():
    config = ConstructionSpendConfig(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",  # type: ignore[arg-type]
        financing=None,  # type: ignore[arg-type]
    )

    assert config.profile.name == "linear"
    assert config.financing == ConstructionFinancing()
    assert isinstance(ConstructionSpendBuilder.from_config(config).build(), CashFlowStream)


def test_construction_spend_config_rejects_invalid_financing_type():
    with pytest.raises(TypeError, match="ConstructionFinancing instance or None"):
        ConstructionSpendConfig(
            1_000_000,
            date(2025, 1, 1),
            date(2026, 1, 1),
            financing="debt",  # type: ignore[arg-type]
        )


def test_validate_schedule_rejects_wrong_sum():
    with pytest.raises(ValueError, match="sum to 1.0"):
        _validate_schedule(((0.0, 0.5), (0.5, 0.3), (1.0, 0.0)))


def test_validate_schedule_rejects_non_monotonic():
    with pytest.raises(ValueError, match="monotonically increasing"):
        _validate_schedule(((0.0, 0.5), (0.8, 0.5), (0.5, 0.0), (1.0, 0.0)))


def test_validate_schedule_rejects_bad_start():
    with pytest.raises(ValueError, match="First duration fraction"):
        _validate_schedule(((0.1, 0.5), (0.5, 0.5), (1.0, 0.0)))


def test_validate_schedule_rejects_bad_end():
    with pytest.raises(ValueError, match="Last duration fraction"):
        _validate_schedule(((0.0, 0.5), (0.5, 0.5)))


def test_validate_schedule_rejects_negative_spend():
    with pytest.raises(ValueError, match="non-negative"):
        _validate_schedule(((0.0, 1.5), (0.5, -0.5), (1.0, 0.0)))


def test_validate_schedule_rejects_nonzero_last_spend():
    with pytest.raises(ValueError, match="Last point must have spend_fraction = 0"):
        _validate_schedule(((0.0, 0.5), (1.0, 0.5)))


def test_build_rejects_negative_cost():
    with pytest.raises(ValueError, match="total_cost must be positive"):
        construction_spend_schedule(-100, date(2025, 1, 1), date(2026, 1, 1))


def test_build_rejects_bad_dates():
    with pytest.raises(ValueError, match="end_date must be after"):
        construction_spend_schedule(1000, date(2026, 1, 1), date(2025, 1, 1))


def test_build_rejects_bad_debt_fraction():
    with pytest.raises(ValueError, match="debt_fraction"):
        ConstructionFinancing.debt(1.5)


def test_build_rejects_interest_without_debt():
    with pytest.raises(ValueError, match="requires debt_fraction"):
        ConstructionFinancing(interest_rate=0.05)


def test_build_rejects_unknown_curve():
    with pytest.raises(ValueError, match="Unknown curve"):
        construction_spend_schedule(
            1000,
            date(2025, 1, 1),
            date(2026, 1, 1),
            profile="nonexistent",  # type: ignore[arg-type]
        )


def test_build_rejects_unknown_interest_treatment():
    with pytest.raises(ValueError, match="Unknown interest treatment"):
        ConstructionFinancing.debt(
            1.0,
            interest_rate=0.06,
            treatment="capitalise",  # type: ignore[arg-type]
        )


def test_direct_api_returns_cashflow_stream():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 7, 1),
        profile="linear",
    )

    assert isinstance(stream, CashFlowStream)


def test_linear_schedule_total_spend():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
    )
    total = sum(cf.amount for cf in stream.flows)
    assert abs(total - (-1_000_000)) < 1.0


def test_flat_schedule_roughly_equal_periods():
    stream = construction_spend_schedule(
        1_200_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
    )
    amounts = [abs(cf.amount) for cf in stream.flows]
    avg = sum(amounts) / len(amounts)
    for amount in amounts:
        assert abs(amount - avg) / avg < 0.15


def test_all_flows_tagged_capex_expense_no_debt():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 7, 1),
        profile="linear",
    )

    for cf in stream.flows:
        assert cf.has_tag(CashFlowTags.CAPEX)
        assert cf.has_tag(CashFlowTags.EXPENSE)
        assert cf.label == "Construction Spend"
        assert cf.is_cash is True


def test_all_flows_negative():
    stream = construction_spend_schedule(
        100_000,
        date(2025, 1, 1),
        date(2025, 7, 1),
        profile="linear",
    )

    for cf in stream.flows:
        assert cf.amount < 0


def test_construction_spend_flows_booked_at_period_end():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 4, 1),
        profile="linear",
    )

    spend_flows = [cf for cf in stream.flows if cf.label == "Construction Spend"]
    assert [cf.date for cf in spend_flows] == [
        date(2025, 2, 1),
        date(2025, 3, 1),
        date(2025, 4, 1),
    ]


def test_construction_spend_final_stub_flow_booked_at_stub_end():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 15),
        date(2025, 4, 10),
        profile="linear",
    )

    spend_flows = [cf for cf in stream.flows if cf.label == "Construction Spend"]
    assert [cf.date for cf in spend_flows] == [
        date(2025, 2, 15),
        date(2025, 3, 15),
        date(2025, 4, 10),
    ]


def test_month_end_monthly_schedule_stays_anchored_to_start_date():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 31),
        date(2025, 5, 31),
        profile="linear",
    )

    spend_flows = [cf for cf in stream.flows if cf.label == "Construction Spend"]
    assert [cf.date for cf in spend_flows] == [
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
        date(2025, 5, 31),
    ]


def test_late_month_quarterly_schedule_stays_anchored_to_start_date():
    stream = construction_spend_schedule(
        1000,
        date(2025, 8, 31),
        date(2026, 5, 31),
        period="quarter",
        profile="linear",
    )

    spend_flows = [cf for cf in stream.flows if cf.label == "Construction Spend"]
    assert [cf.date for cf in spend_flows] == [
        date(2025, 11, 30),
        date(2026, 2, 28),
        date(2026, 5, 31),
    ]


def test_custom_profile_total_spend():
    profile = SpendProfile.custom(((0.0, 0.6), (0.5, 0.4), (1.0, 0.0)))
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile=profile,
    )

    total = sum(cf.amount for cf in stream.flows)
    assert abs(total - (-1_000_000)) < 1.0


def test_builder_schedule_wraps_custom_profile():
    builder = ConstructionSpendBuilder(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
    ).schedule(((0.0, 0.6), (0.5, 0.4), (1.0, 0.0)))

    assert builder.config.profile.name is None
    assert builder.config.profile.schedule == ((0.0, 0.6), (0.5, 0.4), (1.0, 0.0))


def test_escalation_increases_total():
    base = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2027, 1, 1),
        profile="linear",
    )
    escalated = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2027, 1, 1),
        profile="linear",
        escalation_rate=0.05,
    )

    base_total = abs(sum(cf.amount for cf in base.flows))
    escalated_total = abs(sum(cf.amount for cf in escalated.flows))
    assert escalated_total > base_total


def test_debt_financing_does_not_change_total_capex_outflow():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(0.7),
    )

    total = sum(cf.amount for cf in stream.flows)
    assert abs(total - (-1_000_000)) < 1.0
    assert all(cf.label != "Debt Draw" for cf in stream.flows)
    assert all(cf.label != "Equity Draw" for cf in stream.flows)


def test_debt_financing_keeps_full_capex_tagged_as_expense():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 7, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(0.5),
    )

    expense_total = sum(
        cf.amount for cf in stream.flows if cf.has_tag(CashFlowTags.EXPENSE)
    )
    capex_total = sum(
        cf.amount for cf in stream.flows if cf.has_tag(CashFlowTags.CAPEX)
    )

    assert abs(expense_total - (-1000)) < 1.0
    assert abs(capex_total - (-1000)) < 1.0
    for cf in stream.flows:
        assert cf.label == "Construction Spend"
        assert cf.has_tag(CashFlowTags.CAPEX)
        assert cf.has_tag(CashFlowTags.EXPENSE)
        assert cf.is_cash is True


def test_capitalized_interest_is_not_cash():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            0.8,
            interest_rate=0.06,
            treatment="capitalize",
        ),
    )

    capitalized_interest = [cf for cf in stream.flows if cf.label == "Capitalized Interest"]
    assert len(capitalized_interest) > 0
    for cf in capitalized_interest:
        assert cf.is_cash is False
        assert cf.has_tag(CashFlowTags.CAPEX)
        assert cf.amount < 0


def test_paid_interest_is_cash():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            0.8,
            interest_rate=0.06,
            treatment="pay",
        ),
    )

    paid_interest = [cf for cf in stream.flows if cf.label == "Interest Payment"]
    assert len(paid_interest) > 0
    for cf in paid_interest:
        assert cf.is_cash is True
        assert cf.has_tag(CashFlowTags.EXPENSE)
        assert cf.amount < 0


@pytest.mark.parametrize(
    ("treatment", "label"),
    [
        ("pay", "Interest Payment"),
        ("capitalize", "Capitalized Interest"),
    ],
)
def test_interest_flows_booked_at_period_end(treatment, label):
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 4, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.06,
            treatment=treatment,  # type: ignore[arg-type]
        ),
    )

    interest_flows = [cf for cf in stream.flows if cf.label == label]
    assert [cf.date for cf in interest_flows] == [
        date(2025, 3, 1),
        date(2025, 4, 1),
    ]


@pytest.mark.parametrize(
    ("treatment", "label"),
    [
        ("pay", "Interest Payment"),
        ("capitalize", "Capitalized Interest"),
    ],
)
def test_interest_excludes_current_period_draws(treatment, label):
    stream = construction_spend_schedule(
        1200,
        date(2025, 1, 1),
        date(2025, 4, 1),
        profile="flat",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.12,
            treatment=treatment,  # type: ignore[arg-type]
        ),
    )

    spend_flows = [cf for cf in stream.flows if cf.label == "Construction Spend"]
    interest_flows = [cf for cf in stream.flows if cf.label == label]

    assert len(interest_flows) == 2

    second_period_years = timedelta_fractional_years(date(2025, 2, 1), date(2025, 3, 1))
    expected_second_interest = spend_flows[0].amount * 0.12 * second_period_years
    assert interest_flows[0].amount == pytest.approx(expected_second_interest)


def test_paid_interest_third_period_uses_only_prior_draws():
    stream = construction_spend_schedule(
        1200,
        date(2025, 1, 1),
        date(2025, 4, 1),
        profile="flat",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.12,
            treatment="pay",
        ),
    )

    spend_flows = [cf for cf in stream.flows if cf.label == "Construction Spend"]
    interest_flows = [cf for cf in stream.flows if cf.label == "Interest Payment"]

    third_period_years = timedelta_fractional_years(date(2025, 3, 1), date(2025, 4, 1))
    expected_third_interest = (
        spend_flows[0].amount + spend_flows[1].amount
    ) * 0.12 * third_period_years
    assert interest_flows[1].amount == pytest.approx(expected_third_interest)


def test_capitalized_interest_accumulates():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.06,
            treatment="capitalize",
        ),
    )

    interest_flows = [cf for cf in stream.flows if cf.label == "Capitalized Interest"]
    amounts = [abs(cf.amount) for cf in interest_flows]
    assert amounts[-1] > amounts[0]


def test_paid_interest_does_not_accumulate_on_balance():
    paid_stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.06,
            treatment="pay",
        ),
    )
    capitalized_stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.06,
            treatment="capitalize",
        ),
    )

    paid_total = abs(
        sum(cf.amount for cf in paid_stream.flows if cf.label == "Interest Payment")
    )
    capitalized_total = abs(
        sum(cf.amount for cf in capitalized_stream.flows if cf.label == "Capitalized Interest")
    )

    assert capitalized_total > paid_total


def test_quarterly_period():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        period="quarter",
        profile="linear",
    )

    assert len(stream.flows) == 4
    total = sum(cf.amount for cf in stream.flows)
    assert abs(total - (-1_000_000)) < 1.0
