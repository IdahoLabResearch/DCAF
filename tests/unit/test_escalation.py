# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from datetime import date, timedelta

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from dcaf.finance.escalation import (
    CompositeEscalation,
    ConstantRateEscalation,
    EscalationBuilder,
    EscalationSegment,
    IndexSeriesEscalation,
)
from dcaf.shared.time import add_periods
from strategies import ANCHOR_DATES, DAY_COUNT_CONVENTIONS

END_OF_MONTH = st.booleans()
MONTHLY_FREQUENCIES = st.sampled_from(["month", "quarter"])
RATES = st.floats(min_value=-0.05, max_value=0.05)


def test_constant_rate_escalation_annual():
    policy = ConstantRateEscalation(reference_date=date(2025, 1, 1), rate=0.02)

    assert policy.factor(date(2025, 1, 1)) == pytest.approx(1.0)
    assert policy.factor(date(2026, 1, 1)) == pytest.approx(1.02)
    assert policy.factor(date(2027, 1, 1)) == pytest.approx(1.0404)


def test_constant_rate_escalation_monthly_period():
    policy = ConstantRateEscalation(reference_date=date(2025, 1, 1), rate=0.01, period="month")

    assert policy.factor(date(2025, 2, 1)) == pytest.approx(1.01)
    assert policy.factor(date(2025, 3, 1)) == pytest.approx(1.0201)


# === Constant-rate escalation on anchored schedules ===


@given(ANCHOR_DATES, st.integers(0, 240), MONTHLY_FREQUENCIES, END_OF_MONTH, RATES)
@example(date(2030, 4, 30), 1, "month", True, 0.01)  # Apr. 30 -> May 31
@example(date(2030, 4, 30), 1, "month", False, 0.01)  # Apr. 30 -> May 30
@example(date(2030, 1, 31), 2, "month", True, 0.01)  # Jan. 31 -> Feb. 28 -> Mar. 31
@example(date(2030, 6, 30), 2, "quarter", True, 0.01)  # Jun. 30 -> Dec. 31
def test_constant_rate_compounds_one_step_per_scheduled_boundary(
    reference_date, count, period, end_of_month, rate
):
    """Monthly and quarterly escalation compounds exactly one step at each boundary of the
    schedule anchored at the reference date, under either setting of the month-end rule."""
    policy = ConstantRateEscalation(
        reference_date, rate=rate, period=period, end_of_month=end_of_month
    )
    target = add_periods(reference_date, count, period, end_of_month=end_of_month)
    assert policy.factor(target) == pytest.approx((1.0 + rate) ** count)


@pytest.mark.parametrize(
    ("reference_date", "target_date", "period", "end_of_month", "periods"),
    [
        (date(2030, 4, 30), date(2030, 5, 31), "month", True, 1.0),
        # Without the month-end rule, one month after Apr. 30 is May 30, so May 31 is one day
        # into the 31-day month [May 30, Jun. 30).
        (date(2030, 4, 30), date(2030, 5, 31), "month", False, 1 + 1 / 31),
        (date(2030, 6, 30), date(2030, 12, 31), "quarter", True, 2.0),
        # Without the month-end rule, Dec. 31 is one day into the 90-day quarter
        # [Dec. 30, Mar. 30).
        (date(2030, 6, 30), date(2030, 12, 31), "quarter", False, 2 + 1 / 90),
        # A partial quarter is its share of the quarter's days: 36 of the 90 in [Jan. 1, Apr. 1).
        (date(2030, 1, 1), date(2030, 2, 6), "quarter", True, 0.4),
        (date(2030, 1, 1), date(2030, 2, 6), "quarter", False, 0.4),
    ],
)
def test_constant_rate_end_of_month_reference_values(
    reference_date, target_date, period, end_of_month, periods
):
    """Exact escalation factors from month-end and partial-period references under each setting
    of the month-end rule, worked out by hand."""
    policy = ConstantRateEscalation(
        reference_date, rate=0.01, period=period, end_of_month=end_of_month
    )
    assert policy.factor(target_date) == pytest.approx(1.01**periods)


@given(ANCHOR_DATES, st.integers(1, 2000), MONTHLY_FREQUENCIES, RATES)
@example(date(2030, 4, 30), 31, "month", 0.01)  # Apr. 30 -> May 31
def test_constant_rate_end_of_month_defaults_to_true(reference_date, days, period, rate):
    """Omitting ``end_of_month`` behaves exactly like passing ``True``."""
    target = reference_date + timedelta(days=days)
    default = ConstantRateEscalation(reference_date, rate=rate, period=period)
    explicit = ConstantRateEscalation(reference_date, rate=rate, period=period, end_of_month=True)
    assert default.end_of_month is True
    assert default.factor(target) == explicit.factor(target)


@given(ANCHOR_DATES, st.integers(-2000, 2000), RATES, DAY_COUNT_CONVENTIONS)
@example(date(2029, 2, 28), 365, 0.02, "actual/actual")  # a month-end reference
def test_constant_rate_yearly_and_daily_escalation_ignore_end_of_month(
    reference_date, days, rate, convention
):
    """Yearly escalation is measured by the day-count convention and daily escalation by whole
    days, so the month-end rule never changes either factor."""
    target = reference_date + timedelta(days=days)
    for period in ("year", "day"):
        with_eom, without_eom = (
            ConstantRateEscalation(
                reference_date,
                rate=rate,
                period=period,
                day_count_convention=convention,
                end_of_month=end_of_month,
            )
            for end_of_month in (True, False)
        )
        assert with_eom.factor(target) == without_eom.factor(target)


@given(ANCHOR_DATES, st.integers(1, 2000), MONTHLY_FREQUENCIES, END_OF_MONTH, RATES)
@example(date(2030, 4, 30), 31, "month", False, 0.01)  # Apr. 30 -> May 31 off month-ends
def test_builder_constant_rate_passes_end_of_month_through(
    reference_date, days, period, end_of_month, rate
):
    """A constant-rate segment added through the builder escalates exactly like a directly
    constructed policy with the same month-end rule."""
    target = reference_date + timedelta(days=days)
    built = (
        EscalationBuilder(reference_date)
        .constant_rate(rate, period=period, end_of_month=end_of_month)
        .build()
    )
    direct = ConstantRateEscalation(
        reference_date, rate=rate, period=period, end_of_month=end_of_month
    )
    assert built == direct
    assert built.factor(target) == direct.factor(target)


def test_builder_constant_rate_end_of_month_defaults_to_true():
    """A builder constant-rate segment uses the month-end rule unless told otherwise."""
    built = EscalationBuilder(date(2030, 4, 30)).constant_rate(0.01, period="month").build()
    assert isinstance(built, ConstantRateEscalation)
    assert built.end_of_month is True


def test_index_series_escalation_uses_step_interpolation():
    policy = IndexSeriesEscalation(
        reference_date=date(2020, 1, 1),
        points=(
            (date(2020, 1, 1), 100.0),
            (date(2021, 1, 1), 103.0),
            (date(2022, 1, 1), 106.09),
        ),
    )

    assert policy.factor(date(2020, 6, 1)) == pytest.approx(1.0)
    assert policy.factor(date(2021, 6, 1)) == pytest.approx(1.03)
    assert policy.factor(date(2022, 1, 1)) == pytest.approx(1.0609)


def test_index_series_reference_date_must_be_evaluable():
    with pytest.raises(ValueError, match="before the first index point"):
        IndexSeriesEscalation(
            reference_date=date(2019, 1, 1),
            points=((date(2020, 1, 1), 100.0),),
        )


def test_composite_escalation_chains_segments():
    composite = CompositeEscalation(
        reference_date=date(2020, 1, 1),
        segments=(
            EscalationSegment(
                start_date=date(2020, 1, 1),
                policy=IndexSeriesEscalation(
                    reference_date=date(2020, 1, 1),
                    points=(
                        (date(2020, 1, 1), 100.0),
                        (date(2021, 1, 1), 103.0),
                        (date(2022, 1, 1), 106.09),
                    ),
                ),
            ),
            EscalationSegment(
                start_date=date(2022, 1, 1),
                policy=ConstantRateEscalation(
                    reference_date=date(2022, 1, 1),
                    rate=0.03,
                ),
            ),
        ),
    )

    assert composite.factor(date(2021, 1, 1)) == pytest.approx(1.03)
    assert composite.factor(date(2022, 1, 1)) == pytest.approx(1.0609)
    assert composite.factor(date(2024, 1, 1)) == pytest.approx(1.0609 * 1.03 * 1.03)


def test_composite_escalation_supports_bidirectional_evaluation():
    forward_2025 = ConstantRateEscalation(
        reference_date=date(2024, 1, 1),
        rate=0.10,
    ).factor(date(2025, 1, 1))

    composite = CompositeEscalation(
        reference_date=date(2023, 1, 1),
        segments=(
            EscalationSegment(
                start_date=date(2020, 1, 1),
                policy=IndexSeriesEscalation(
                    reference_date=date(2020, 1, 1),
                    points=(
                        (date(2020, 1, 1), 100.0),
                        (date(2021, 1, 1), 110.0),
                        (date(2022, 1, 1), 121.0),
                        (date(2023, 1, 1), 133.1),
                        (date(2024, 1, 1), 146.41),
                    ),
                ),
            ),
            EscalationSegment(
                start_date=date(2024, 1, 1),
                policy=ConstantRateEscalation(
                    reference_date=date(2024, 1, 1),
                    rate=0.10,
                ),
            ),
        ),
    )

    assert composite.factor(date(2021, 1, 1)) == pytest.approx(110.0 / 133.1)
    assert composite.factor(date(2023, 1, 1)) == pytest.approx(1.0)
    assert composite.factor(date(2025, 1, 1)) == pytest.approx((146.41 / 133.1) * forward_2025)


def test_composite_escalation_supports_backward_evaluation_across_segments():
    forward_2025 = ConstantRateEscalation(
        reference_date=date(2024, 1, 1),
        rate=0.10,
    ).factor(date(2025, 1, 1))

    composite = CompositeEscalation(
        reference_date=date(2025, 1, 1),
        segments=(
            EscalationSegment(
                start_date=date(2020, 1, 1),
                policy=IndexSeriesEscalation(
                    reference_date=date(2020, 1, 1),
                    points=(
                        (date(2020, 1, 1), 100.0),
                        (date(2021, 1, 1), 110.0),
                        (date(2022, 1, 1), 121.0),
                        (date(2023, 1, 1), 133.1),
                        (date(2024, 1, 1), 146.41),
                    ),
                ),
            ),
            EscalationSegment(
                start_date=date(2024, 1, 1),
                policy=ConstantRateEscalation(
                    reference_date=date(2024, 1, 1),
                    rate=0.10,
                ),
            ),
        ),
    )

    assert composite.factor(date(2022, 1, 1)) == pytest.approx(121.0 / (146.41 * forward_2025))


def test_composite_escalation_rejects_dates_before_first_segment_start():
    composite = CompositeEscalation(
        reference_date=date(2023, 1, 1),
        segments=(
            EscalationSegment(
                start_date=date(2020, 1, 1),
                policy=IndexSeriesEscalation(
                    reference_date=date(2020, 1, 1),
                    points=(
                        (date(2020, 1, 1), 100.0),
                        (date(2021, 1, 1), 103.0),
                        (date(2022, 1, 1), 106.09),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="before the first segment"):
        composite.factor(date(2019, 12, 31))


def test_composite_escalation_requires_reference_date_in_covered_window():
    with pytest.raises(ValueError, match="on or after the first segment start_date"):
        CompositeEscalation(
            reference_date=date(2019, 12, 31),
            segments=(
                EscalationSegment(
                    start_date=date(2020, 1, 1),
                    policy=IndexSeriesEscalation(
                        reference_date=date(2020, 1, 1),
                        points=((date(2020, 1, 1), 100.0),),
                    ),
                ),
            ),
        )


def test_builder_builds_piecewise_policy():
    policy = (
        EscalationBuilder(reference_date=date(2020, 1, 1))
        .index_series(
            (
                (date(2020, 1, 1), 100.0),
                (date(2021, 1, 1), 104.0),
                (date(2022, 1, 1), 108.16),
            )
        )
        .constant_rate(0.04, start_date=date(2022, 1, 1))
        .build()
    )

    assert policy.factor(date(2023, 1, 1)) == pytest.approx(1.0816 * 1.04)


def test_builder_wraps_single_earlier_segment_in_composite():
    policy = (
        EscalationBuilder(reference_date=date(2023, 1, 1))
        .index_series(
            (
                (date(2020, 1, 1), 100.0),
                (date(2021, 1, 1), 110.0),
                (date(2022, 1, 1), 121.0),
                (date(2023, 1, 1), 133.1),
            ),
            start_date=date(2020, 1, 1),
        )
        .build()
    )

    assert isinstance(policy, CompositeEscalation)
    assert policy.factor(date(2021, 1, 1)) == pytest.approx(110.0 / 133.1)
    assert policy.factor(date(2023, 1, 1)) == pytest.approx(1.0)


def test_builder_requires_explicit_start_date_after_first_segment():
    builder = EscalationBuilder(reference_date=date(2025, 1, 1)).constant_rate(0.02)

    with pytest.raises(ValueError, match="start_date is required"):
        builder.constant_rate(0.03)


def test_builder_rejects_first_segment_starting_after_reference_date():
    with pytest.raises(ValueError, match="on or before reference_date"):
        EscalationBuilder(reference_date=date(2025, 1, 1)).constant_rate(
            0.02,
            start_date=date(2025, 2, 1),
        )
