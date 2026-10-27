# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for construction spend schedule APIs, helpers, and reference accessors."""

import inspect
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

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
from strategies import DAY_COUNT_CONVENTIONS, TIMINGS

SPEND_PROFILES = get_spend_profiles()
FLAT_CURVE = SPEND_PROFILES["flat"]
BELL_CURVE = SPEND_PROFILES["bell"]
RAMPED_CURVE = SPEND_PROFILES["ramped"]
TRIANGLE_CURVE = SPEND_PROFILES["triangle"]
LINEAR_CURVE = SPEND_PROFILES["linear"]
UPFRONT_CURVE = SPEND_PROFILES["upfront"]


def _annual_factor(start: date, end: date, rate: float) -> float:
    return (1.0 + rate) ** ((end - start).days / 365.0)


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
        timing="begin",
    ).escalation(
        0.01,
        escalation_period="month",
        amount_reference_date=date(2024, 1, 1),
    )
    updated = base.escalation(0.02)

    assert updated.config.escalation == 0.02
    assert updated.config.escalation_period.value == "month"
    assert updated.config.amount_reference_date == date(2024, 1, 1)
    assert updated.config.timing == "begin"


def test_builder_escalation_policy_resets_simple_config_and_applies_override():
    policy = IndexSeriesEscalation(
        reference_date=date(2025, 1, 1),
        points=((date(2025, 1, 1), 100.0), (date(2026, 1, 1), 110.0)),
    )
    builder = (
        ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1), period="year")
        .escalation(0.03, amount_reference_date=date(2025, 1, 1))
        .escalation_policy(policy)
    )

    assert builder.config.escalation == 0.0
    assert builder.config.escalation_period.value == "year"
    assert builder.config.amount_reference_date is None

    stream = builder.build()
    assert stream.entries[0].amount == pytest.approx(-1_000_000 * policy.factor(date(2025, 12, 31)))


def test_builder_escalation_after_policy_returns_to_simple_behavior():
    policy = IndexSeriesEscalation(
        reference_date=date(2025, 1, 1),
        points=((date(2025, 1, 1), 100.0), (date(2026, 1, 1), 110.0)),
    )
    builder = (
        ConstructionSpendBuilder(1_000_000, date(2025, 1, 1), date(2026, 1, 1), period="year")
        .escalation_policy(policy)
        .escalation(0.05)
    )

    expected_amount = -1_000_000 * _annual_factor(date(2025, 1, 1), date(2025, 12, 31), 0.05)
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
        date(2025, 4, 9),
    ]


def test_month_end_monthly_schedule_uses_calendar_periods():
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
        date(2025, 5, 30),
    ]


def test_late_month_quarterly_schedule_uses_calendar_periods():
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
        date(2026, 5, 30),
    ]


def test_custom_profile_partial_calendar_periods_conserve_total_spend():
    profile = SpendProfile.custom(((0.0, 0.6), (0.5, 0.4), (1.0, 0.0)))
    stream = construction_spend_schedule(
        1_000_000,
        date(2025, 7, 1),
        date(2030, 4, 16),
        period="year",
        profile=profile,
    )

    assert sum(cf.amount for cf in stream.entries) == pytest.approx(-1_000_000)


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


def test_construction_escalation_uses_calendar_period_booking_dates():
    start_date = date(2025, 7, 1)
    end_date = date(2026, 7, 1)
    stream = construction_spend_schedule(
        1_000_000,
        start_date,
        end_date,
        period="year",
        profile="flat",
        escalation=0.05,
        day_count_convention="actual/365-fixed",
    )

    expected_amounts = [
        -1_000_000 * 184.0 / 365.0 * _annual_factor(start_date, date(2025, 12, 31), 0.05),
        -1_000_000 * 181.0 / 365.0 * _annual_factor(start_date, date(2026, 6, 30), 0.05),
    ]
    assert [flow.date for flow in stream] == [date(2025, 12, 31), date(2026, 6, 30)]
    assert [flow.amount for flow in stream] == pytest.approx(expected_amounts)


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

    policy = ConstantRateEscalation(start_date, rate=0.01, period="month")
    expected_amount = -1_000_000 * policy.factor(date(2025, 12, 31))
    assert stream.entries[0].amount == pytest.approx(expected_amount)


def test_construction_supports_earlier_amount_reference_date():
    start_date = date(2025, 1, 1)
    end_date = date(2026, 1, 1)
    reference_date = date(2024, 1, 1)
    stream = construction_spend_schedule(
        1_000_000,
        start_date,
        end_date,
        period="year",
        profile="flat",
        escalation=0.12,
        amount_reference_date=reference_date,
    )

    # actual/actual spans one full leap year plus 364/365 of 2025.
    expected_amount = -1_000_000 * 1.12 ** (1.0 + 364.0 / 365.0)
    assert stream.entries[0].amount == pytest.approx(expected_amount)


def test_construction_supports_escalation_policy():
    start_date = date(2025, 1, 1)
    end_date = date(2026, 1, 1)
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

    assert stream.entries[0].amount == pytest.approx(-1_000_000 * policy.factor(date(2025, 12, 31)))


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
        date(2025, 2, 1),
        date(2025, 3, 1),
        date(2025, 4, 1),
    ]


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


def test_build_components_separates_pro_rata_and_full_debt_bases():
    builder = ConstructionSpendBuilder(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            0.5,
            interest_rate=0.06,
            treatment="capitalize",
        ),
    )

    components = builder.build_components()

    assert components.total.entries == builder.build().entries
    assert components.pro_rata_debt_basis.entries
    assert components.full_debt_basis.entries
    assert all(flow.label == "Construction Spend" for flow in components.pro_rata_debt_basis)
    assert all(flow.label == "Capitalized Interest" for flow in components.full_debt_basis)
    assert (
        components.total.entries
        == CashFlowStream.from_streams(
            components.pro_rata_debt_basis,
            components.full_debt_basis,
        )
        .sort()
        .entries
    )


def test_build_components_keeps_paid_interest_outside_debt_bases():
    components = ConstructionSpendBuilder(
        1_000_000,
        date(2025, 1, 1),
        date(2026, 1, 1),
        profile="linear",
        financing=ConstructionFinancing.debt(
            0.5,
            interest_rate=0.06,
            treatment="pay",
        ),
    ).build_components()

    paid_interest = components.total.filter(pro_forma_category=ProFormaCategory.FINANCING_INTEREST)

    assert paid_interest.entries
    assert components.pro_rata_debt_basis.entries
    assert not components.full_debt_basis.entries
    assert all(flow.label == "Construction Spend" for flow in components.pro_rata_debt_basis)


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

    assert [cf.date for cf in interest_flows] == [
        date(2026, 1, 1),
        date(2027, 1, 1),
        date(2028, 1, 1),
    ]
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


# === Draw-date interest accrual ===
#
# Each case is $1M flat spend in 2030 (a non-leap year, so every actual/actual year fraction is
# days / 365), 50% debt, and 6% interest. Each draw is $500k times its spend window's share of
# construction days, and each draw accrues simple interest from its draw date. Capitalized
# interest joins the balance at each servicing-period end, so later periods compound on it.


def test_draws_on_servicing_boundaries_keep_their_interest():
    """Draws dated on a servicing-period start accrue over that whole period.

    Draws of 500k x 31/90, 28/90 and 31/90 land on Jan 1, Feb 1 and Mar 1, each the start of a
    monthly servicing period:

    - Jan: 172,222.22 x 0.06 x 31/365 = 877.63
    - Feb: (172,222.22 + 877.63 + 155,555.56) x 0.06 x 28/365 = 1,512.72
    - Mar: (328,655.41 + 1,512.72 + 172,222.22) x 0.06 x 31/365 = 2,560.13
    """
    stream = construction_spend_schedule(
        1_000_000.0,
        date(2030, 1, 1),
        date(2030, 4, 1),
        timing="begin",
        financing=ConstructionFinancing.debt(0.5, interest_rate=0.06),
    )

    interest = [-cf.amount for cf in stream.entries if cf.label == "Capitalized Interest"]
    assert interest == pytest.approx([877.63, 1_512.72, 2_560.13], abs=0.01)
    assert sum(interest) == pytest.approx(4_950.47, abs=0.01)


@pytest.mark.parametrize(
    ("end_of_month", "expected"),
    [
        # Servicing [Apr 30, May 31), [May 31, Jun 30), [Jun 30, Jul 31). Draws of 500k x 1/92,
        # 31/92, 30/92 and 30/92 land on Apr 30, May 1, Jun 1 and Jul 1.
        # - May 31: 5,434.78 x 31 days + 168,478.26 x 30 days -> 858.55
        # - Jun 30: (173,913.04 + 858.55) x 1 day + (+ 163,043.48) x 29 days -> 1,639.14
        # - Jul 31: (339,454.21 + 1,639.14) x 1 day + (+ 163,043.48) x 30 days -> 2,533.87
        (True, [858.55, 1_639.14, 2_533.87]),
        # Servicing [Apr 30, May 30), [May 30, Jun 30), [Jun 30, Jul 30), [Jul 30, Jul 31).
        # - May 30: 5,434.78 x 30 days + 168,478.26 x 29 days -> 829.96
        # - Jun 30: (173,913.04 + 829.96) x 31 days + 163,043.48 x 29 days -> 1,667.72
        # - Jul 30: (339,456.52 + 1,667.72) x 30 days + 163,043.48 x 29 days -> 2,451.27
        # - Jul 31: (504,167.72 + 2,451.27) x 1 day -> 83.01
        (False, [829.96, 1_667.72, 2_451.27, 83.01]),
    ],
)
def test_draws_inside_servicing_periods_accrue_from_their_draw_dates(end_of_month, expected):
    """Draws on the 1st accrue from the 1st when servicing is anchored on Apr 30."""
    stream = construction_spend_schedule(
        1_000_000.0,
        date(2030, 4, 30),
        date(2030, 7, 31),
        timing="begin",
        financing=ConstructionFinancing.debt(0.5, interest_rate=0.06),
        end_of_month=end_of_month,
    )

    interest = [-cf.amount for cf in stream.entries if cf.label == "Capitalized Interest"]
    assert interest == pytest.approx(expected, abs=0.01)


def test_annual_servicing_accrues_monthly_begin_draws_from_their_draw_dates():
    """Each month's draw of 500k x days / 365 on the 1st accrues to Jan 1, 2031.

    With one servicing period nothing compounds, so the total is
    sum(500k x d_m / 365 x 0.06 x (days from the 1st of month m to Jan 1) / 365) = 16,251.00.
    """
    stream = construction_spend_schedule(
        1_000_000.0,
        date(2030, 1, 1),
        date(2031, 1, 1),
        timing="begin",
        financing=ConstructionFinancing.debt(0.5, interest_rate=0.06, servicing_period="year"),
    )

    interest = [-cf.amount for cf in stream.entries if cf.label == "Capitalized Interest"]
    assert interest == pytest.approx([16_251.00], abs=0.01)


def test_annual_servicing_accrues_monthly_end_draws_from_their_draw_dates():
    """With default end timing, each month's draw lands on the month's last day.

    Each draw of 500k x d_m / 365 accrues from its month-end to Jan 1, 2031:
    sum(500k x d_m / 365 x 0.06 x (days from the last of month m to Jan 1) / 365) = 13,831.19.
    """
    stream = construction_spend_schedule(
        1_000_000.0,
        date(2030, 1, 1),
        date(2031, 1, 1),
        financing=ConstructionFinancing.debt(0.5, interest_rate=0.06, servicing_period="year"),
    )

    interest = [-cf.amount for cf in stream.entries if cf.label == "Capitalized Interest"]
    assert interest == pytest.approx([13_831.19], abs=0.01)


@given(
    start=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
    days=st.integers(min_value=1, max_value=4 * 366),
    period=st.sampled_from(["month", "quarter", "year"]),
    servicing_period=st.sampled_from(["month", "quarter", "year"]),
    timing=TIMINGS,
    day_count_convention=DAY_COUNT_CONVENTIONS,
    end_of_month=st.booleans(),
)
def test_paid_interest_does_not_depend_on_servicing_grid(
    start, days, period, servicing_period, timing, day_count_convention, end_of_month
):
    """Paid interest is simple interest on each draw from its draw date to construction end.

    Paid interest never compounds, so cutting the accrual window into servicing periods must
    not change its total. Each draw contributes ``draw x rate x yearfrac(draw date, end)``.
    """
    end = start + timedelta(days=days)
    stream = construction_spend_schedule(
        1_000_000.0,
        start,
        end,
        period=period,
        timing=timing,
        financing=ConstructionFinancing.debt(
            0.5, interest_rate=0.06, treatment="pay", servicing_period=servicing_period
        ),
        day_count_convention=day_count_convention,
        end_of_month=end_of_month,
    )

    expected = sum(
        -flow.amount * 0.5 * 0.06 * timedelta_fractional_years(flow.date, end, day_count_convention)
        for flow in stream
        if flow.label == "Construction Spend"
    )
    interest = sum(-flow.amount for flow in stream if flow.label == "Interest Payment")

    assert interest == pytest.approx(expected, rel=1e-9, abs=1e-6)
