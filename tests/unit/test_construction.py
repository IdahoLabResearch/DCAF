"""Tests for construction spend schedule APIs, helpers, and reference accessors."""

import inspect
from datetime import date, timedelta

import pytest

from dcaf.finance import (
    ConstantRateEscalation,
    EscalationBuilder,
    IndexSeriesEscalation,
    get_spend_profile,
    get_spend_profiles,
)
from dcaf.finance.construction import (
    ConstructionFinancing,
    ConstructionSpendBuilder,
    ConstructionSpendConfig,
    SpendProfile,
    _validate_schedule,
    construction_spend_schedule,
)
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.shared.time import timedelta_fractional_years
from dcaf.streams import CashFlowStream

SPEND_PROFILES = get_spend_profiles()
FLAT_CURVE = SPEND_PROFILES["flat"]
BELL_CURVE = SPEND_PROFILES["bell"]
RAMPED_CURVE = SPEND_PROFILES["ramped"]
TRIANGLE_CURVE = SPEND_PROFILES["triangle"]
LINEAR_CURVE = SPEND_PROFILES["linear"]
UPFRONT_CURVE = SPEND_PROFILES["upfront"]


def _annual_factor(start: date, end: date, rate: float) -> float:
    return (1.0 + rate) ** ((end - start).days / 365.0)


def _midpoint_date(start: date, end: date) -> date:
    return start + timedelta(days=((end - start).days // 2))


@pytest.mark.parametrize(
    "curve",
    [FLAT_CURVE, BELL_CURVE, RAMPED_CURVE, TRIANGLE_CURVE, LINEAR_CURVE, UPFRONT_CURVE],
    ids=["flat", "bell", "ramped", "triangle", "linear", "upfront"],
)
def test_curve_sums_to_one(curve):
    assert abs(sum(point[1] for point in curve) - 1.0) < 1e-6


@pytest.mark.parametrize(
    "curve",
    [FLAT_CURVE, BELL_CURVE, RAMPED_CURVE, TRIANGLE_CURVE, LINEAR_CURVE, UPFRONT_CURVE],
    ids=["flat", "bell", "ramped", "triangle", "linear", "upfront"],
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
    ["flat", "bell", "ramped", "triangle", "linear", "upfront"],
)
def test_named_profiles_lookup(name):
    profile = SpendProfile.curve(name)  # type: ignore[arg-type]
    assert len(profile.schedule) > 0
    assert profile.name == name


def test_get_spend_profile_matches_named_profile():
    """The public accessor exposes the same built-in schedule used by SpendProfile."""
    assert get_spend_profile("flat") == SpendProfile.curve("flat").schedule


def test_upfront_profile_books_total_cost_on_start_date():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="upfront",
    )

    assert len(stream.entries) == 1
    assert stream.entries[0].date == date(2025, 1, 1)
    assert stream.entries[0].amount == pytest.approx(-1_000_000)


def test_upfront_financing_accrues_interest_from_start_date():
    start_date = date(2025, 1, 1)
    end_date = date(2026, 1, 1)
    interest_rate = 0.12
    stream = construction_spend_schedule(
        1_000_000,
        start_date,
        end_date,
        period="year",
        profile="upfront",
        financing=ConstructionFinancing.debt(1.0, interest_rate=interest_rate, treatment="pay"),
    )

    assert [entry.label for entry in stream.entries] == ["Construction Spend", "Interest Payment"]
    assert stream.entries[0].date == start_date
    assert stream.entries[1].date == end_date
    assert stream.entries[1].amount == pytest.approx(-1_000_000 * interest_rate)


def test_spend_profiles_are_read_only():
    """Public spend-profile registry is exposed as a read-only mapping."""
    with pytest.raises(TypeError):
        SPEND_PROFILES["custom"] = FLAT_CURVE  # type: ignore[index]


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

    assert [cf.date for cf in implicit.entries] == [cf.date for cf in explicit.entries]
    assert [cf.amount for cf in implicit.entries] == pytest.approx(
        [cf.amount for cf in explicit.entries]
    )


def test_builder_is_immutable():
    base = ConstructionSpendBuilder(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
    )
    escalated = base.escalation(0.05)

    assert base.config.escalation == 0.0
    assert escalated.config.escalation == 0.05


def test_builder_escalation_preserves_existing_kwargs():
    base = ConstructionSpendBuilder(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
    ).escalation(
        0.01,
        escalation_period="month",
        amount_reference_date=date(2024, 1, 1),
    )
    updated = base.escalation(0.02)

    assert updated.config.escalation == 0.02
    assert updated.config.escalation_period.value == "month"
    assert updated.config.amount_reference_date == date(2024, 1, 1)


def test_builder_escalation_policy_resets_simple_config_and_applies_override():
    policy = IndexSeriesEscalation(
        reference_date=date(2025, 1, 1),
        points=((date(2025, 1, 1), 100.0), (date(2026, 1, 1), 110.0)),
    )
    builder = (
        ConstructionSpendBuilder(1_000_000, date(2025, 7, 1), date(2026, 7, 1), period="year")
        .escalation(0.03, amount_reference_date=date(2025, 1, 1))
        .escalation_policy(policy)
    )

    assert builder.config.escalation == 0.0
    assert builder.config.escalation_period.value == "year"
    assert builder.config.amount_reference_date is None

    midpoint = _midpoint_date(date(2025, 7, 1), date(2026, 7, 1))
    stream = builder.build()
    assert stream.entries[0].amount == pytest.approx(-1_000_000 * policy.factor(midpoint))


def test_builder_escalation_after_policy_returns_to_simple_behavior():
    policy = IndexSeriesEscalation(
        reference_date=date(2025, 1, 1),
        points=((date(2025, 1, 1), 100.0), (date(2026, 1, 1), 110.0)),
    )
    builder = (
        ConstructionSpendBuilder(1_000_000, date(2025, 7, 1), date(2026, 7, 1), period="year")
        .escalation_policy(policy)
        .escalation(0.05)
    )

    midpoint = _midpoint_date(date(2025, 7, 1), date(2026, 7, 1))
    expected_amount = -1_000_000 * _annual_factor(date(2025, 7, 1), midpoint, 0.05)
    stream = builder.build()
    assert stream.entries[0].amount == pytest.approx(expected_amount)


def test_builder_escalation_policy_rejects_unbuilt_builder():
    with pytest.raises(TypeError, match="call \\.build\\(\\) first"):
        ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1)).escalation_policy(
            EscalationBuilder(reference_date=date(2025, 1, 1)).constant_rate(0.02)
        )


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


def test_construction_financing_normalizes_servicing_period():
    financing = ConstructionFinancing.debt(
        0.8,
        interest_rate=0.06,
        servicing_period="year",
    )

    assert financing.servicing_period.value == "year"


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
    total = sum(cf.amount for cf in stream.entries)
    assert abs(total - (-1_000_000)) < 1.0


def test_flat_schedule_roughly_equal_periods():
    stream = construction_spend_schedule(
        1_200_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
    )
    amounts = [abs(cf.amount) for cf in stream.entries]
    avg = sum(amounts) / len(amounts)
    for amount in amounts:
        assert abs(amount - avg) / avg < 0.15


def test_all_flows_classified_as_capital_cost_no_debt():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 7, 1),
        profile="linear",
    )

    for cf in stream.entries:
        assert cf.pro_forma_category is ProFormaCategory.CAPITAL_COST
        assert cf.tax_treatment is TaxTreatment.NONE
        assert cf.label == "Construction Spend"
        assert cf.is_cash is True


def test_all_flows_negative():
    stream = construction_spend_schedule(
        100_000,
        date(2025, 1, 1),
        date(2025, 7, 1),
        profile="linear",
    )

    for cf in stream.entries:
        assert cf.amount < 0


def test_construction_spend_flows_booked_at_period_end():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 4, 1),
        profile="linear",
    )

    spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
    # Booking dates use calendar month-ends, capped by construction phase end
    # (end_date - 1 day = 2025-03-31).
    assert [cf.date for cf in spend_flows] == [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
    ]


def test_construction_spend_final_stub_flow_booked_at_stub_end():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 15),
        date(2025, 4, 10),
        profile="linear",
    )

    spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
    # Booking dates use calendar month-ends, capped by construction phase end
    # (end_date - 1 day = 2025-04-09).
    assert [cf.date for cf in spend_flows] == [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
    ]


def test_month_end_monthly_schedule_stays_anchored_to_start_date():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 31),
        date(2025, 5, 31),
        profile="linear",
    )

    spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
    # Booking dates use calendar month-ends, capped by construction phase end
    # (end_date - 1 day = 2025-05-30).
    assert [cf.date for cf in spend_flows] == [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
    ]


def test_late_month_quarterly_schedule_stays_anchored_to_start_date():
    stream = construction_spend_schedule(
        1000,
        date(2025, 8, 31),
        date(2026, 5, 31),
        period="quarter",
        profile="linear",
    )

    spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
    # Booking dates use calendar quarter-ends, capped by construction phase end
    # (end_date - 1 day = 2026-05-30).
    assert [cf.date for cf in spend_flows] == [
        date(2025, 9, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
    ]


def test_custom_profile_total_spend():
    profile = SpendProfile.custom(((0.0, 0.6), (0.5, 0.4), (1.0, 0.0)))
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile=profile,
    )

    total = sum(cf.amount for cf in stream.entries)
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
        escalation=0.05,
    )

    base_total = abs(sum(cf.amount for cf in base.entries))
    escalated_total = abs(sum(cf.amount for cf in escalated.entries))
    assert escalated_total > base_total


def test_construction_escalation_uses_period_midpoint():
    start_date = date(2025, 1, 1)
    end_date = date(2026, 1, 1)
    stream = construction_spend_schedule(
        1_000_000,
        start_date,
        end_date,
        period="year",
        profile="flat",
        escalation=0.05,
    )

    midpoint = _midpoint_date(start_date, end_date)
    expected_amount = -1_000_000 * _annual_factor(start_date, midpoint, 0.05)
    assert len(stream.entries) == 1
    assert stream.entries[0].amount == pytest.approx(expected_amount)


def test_construction_supports_explicit_nonannual_escalation_period():
    start_date = date(2025, 1, 1)
    end_date = date(2026, 1, 1)
    stream = construction_spend_schedule(
        1_000_000,
        start_date,
        end_date,
        period="year",
        profile="flat",
        escalation=0.01,
        escalation_period="month",
    )

    midpoint = _midpoint_date(start_date, end_date)
    policy = ConstantRateEscalation(start_date, rate=0.01, period="month")
    expected_amount = -1_000_000 * policy.factor(midpoint)
    assert stream.entries[0].amount == pytest.approx(expected_amount)


def test_construction_supports_earlier_amount_reference_date():
    start_date = date(2025, 7, 1)
    end_date = date(2026, 7, 1)
    reference_date = date(2025, 1, 1)
    stream = construction_spend_schedule(
        1_000_000,
        start_date,
        end_date,
        period="year",
        profile="flat",
        escalation=0.12,
        amount_reference_date=reference_date,
    )

    midpoint = _midpoint_date(start_date, end_date)
    expected_amount = -1_000_000 * _annual_factor(reference_date, midpoint, 0.12)
    assert stream.entries[0].amount == pytest.approx(expected_amount)


def test_construction_supports_escalation_policy():
    start_date = date(2025, 7, 1)
    end_date = date(2026, 7, 1)
    policy = IndexSeriesEscalation(
        reference_date=date(2025, 1, 1),
        points=((date(2025, 1, 1), 100.0), (date(2026, 1, 1), 110.0)),
    )
    stream = construction_spend_schedule(
        1_000_000,
        start_date,
        end_date,
        period="year",
        profile="flat",
        escalation_policy=policy,
    )

    midpoint = _midpoint_date(start_date, end_date)
    assert stream.entries[0].amount == pytest.approx(-1_000_000 * policy.factor(midpoint))


def test_construction_rejects_mixed_simple_and_policy_inputs():
    policy = ConstantRateEscalation(reference_date=date(2025, 1, 1), rate=0.02)

    with pytest.raises(ValueError, match="cannot be combined"):
        construction_spend_schedule(
            1_000_000,
            date(2025, 1, 1),
            date(2026, 1, 1),
            escalation=0.02,
            escalation_policy=policy,
        )


def test_debt_financing_does_not_change_total_capex_outflow():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(0.7),
    )

    total = sum(cf.amount for cf in stream.entries)
    assert abs(total - (-1_000_000)) < 1.0
    assert all(cf.label != "Debt Draw" for cf in stream.entries)
    assert all(cf.label != "Equity Draw" for cf in stream.entries)


def test_debt_financing_keeps_full_capex_tagged_as_expense():
    stream = construction_spend_schedule(
        1000,
        date(2025, 1, 1),
        date(2025, 7, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(0.5),
    )

    capital_cost_total = sum(
        cf.amount for cf in stream.entries if cf.pro_forma_category is ProFormaCategory.CAPITAL_COST
    )

    assert abs(capital_cost_total - (-1000)) < 1.0
    for cf in stream.entries:
        assert cf.label == "Construction Spend"
        assert cf.pro_forma_category is ProFormaCategory.CAPITAL_COST
        assert cf.tax_treatment is TaxTreatment.NONE
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

    capitalized_interest = [cf for cf in stream.entries if cf.label == "Capitalized Interest"]
    assert len(capitalized_interest) > 0
    for cf in capitalized_interest:
        assert cf.is_cash is False
        assert cf.pro_forma_category is ProFormaCategory.CAPITAL_COST
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

    paid_interest = [cf for cf in stream.entries if cf.label == "Interest Payment"]
    assert len(paid_interest) > 0
    for cf in paid_interest:
        assert cf.is_cash is True
        assert cf.pro_forma_category is ProFormaCategory.FINANCING_INTEREST
        assert cf.tax_treatment is TaxTreatment.NONE
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

    interest_flows = [cf for cf in stream.entries if cf.label == label]
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

    spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
    interest_flows = [cf for cf in stream.entries if cf.label == label]

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

    spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
    interest_flows = [cf for cf in stream.entries if cf.label == "Interest Payment"]

    third_period_years = timedelta_fractional_years(date(2025, 3, 1), date(2025, 4, 1))
    expected_third_interest = (
        (spend_flows[0].amount + spend_flows[1].amount) * 0.12 * third_period_years
    )
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

    interest_flows = [cf for cf in stream.entries if cf.label == "Capitalized Interest"]
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

    paid_total = abs(sum(cf.amount for cf in paid_stream.entries if cf.label == "Interest Payment"))
    capitalized_total = abs(
        sum(cf.amount for cf in capitalized_stream.entries if cf.label == "Capitalized Interest")
    )

    assert capitalized_total > paid_total


def test_annual_interest_servicing_uses_prior_year_balance_for_monthly_spend():
    stream = construction_spend_schedule(
        24_000,
        date(2025, 1, 1),
        date(2027, 1, 1),
        period="month",
        profile="flat",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.12,
            treatment="pay",
            servicing_period="year",
        ),
    )

    spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
    interest_flows = [cf for cf in stream.entries if cf.label == "Interest Payment"]

    assert len(interest_flows) == 1
    assert interest_flows[0].date == date(2027, 1, 1)

    opening_balance = -sum(cf.amount for cf in spend_flows if cf.date <= date(2026, 1, 1))
    expected_interest = -opening_balance * 0.12
    assert interest_flows[0].amount == pytest.approx(expected_interest)


def test_annual_capitalized_interest_rolls_forward_between_service_periods():
    stream = construction_spend_schedule(
        36_000,
        date(2025, 1, 1),
        date(2028, 1, 1),
        period="month",
        profile="flat",
        financing=ConstructionFinancing.debt(
            1.0,
            interest_rate=0.10,
            treatment="capitalize",
            servicing_period="year",
        ),
    )

    interest_flows = [cf for cf in stream.entries if cf.label == "Capitalized Interest"]

    assert [cf.date for cf in interest_flows] == [date(2027, 1, 1), date(2028, 1, 1)]
    assert abs(interest_flows[1].amount) > abs(interest_flows[0].amount)


def test_quarterly_period():
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        period="quarter",
        profile="linear",
    )

    assert len(stream.entries) == 4
    total = sum(cf.amount for cf in stream.entries)
    assert abs(total - (-1_000_000)) < 1.0
