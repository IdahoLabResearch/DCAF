# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from datetime import date

import pytest

from dcaf.finance.escalation import (
    CompositeEscalation,
    ConstantRateEscalation,
    EscalationBuilder,
    EscalationSegment,
    IndexSeriesEscalation,
)


def test_constant_rate_escalation_annual():
    policy = ConstantRateEscalation(reference_date=date(2025, 1, 1), rate=0.02)

    assert policy.factor(date(2025, 1, 1)) == pytest.approx(1.0)
    assert policy.factor(date(2026, 1, 1)) == pytest.approx(1.02)
    assert policy.factor(date(2027, 1, 1)) == pytest.approx(1.0404)


def test_constant_rate_escalation_monthly_period():
    policy = ConstantRateEscalation(reference_date=date(2025, 1, 1), rate=0.01, period="month")

    assert policy.factor(date(2025, 2, 1)) == pytest.approx(1.01)
    assert policy.factor(date(2025, 3, 1)) == pytest.approx(1.0201)


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
