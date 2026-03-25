"""Tests for fixed_opex()."""

from datetime import date

import pytest

from dcaf import ProFormaCategory, TaxTreatment
from dcaf.escalation import IndexSeriesEscalation
from dcaf.opex import fixed_opex


def _annual_factor(start: date, end: date, rate: float) -> float:
    return (1.0 + rate) ** ((end - start).days / 365.0)


def test_basic_call():
    """fixed_opex returns the correct number of negative flows at the base amount."""
    stream = fixed_opex(amount=100_000, start=date(2025, 1, 1), periods=5)
    assert len(stream.entries) == 5
    assert all(f.amount < 0 for f in stream.entries)
    assert stream.entries[0].amount == -100_000


def test_positive_amount_produces_negative_flows():
    """A positive amount is negated so OPEX flows represent cash outflows."""
    stream = fixed_opex(amount=50_000, start=date(2025, 1, 1), periods=3)
    assert stream.entries[0].amount == -50_000


def test_negative_amount_produces_negative_flows():
    """A pre-negated amount is kept negative rather than double-negated."""
    stream = fixed_opex(amount=-50_000, start=date(2025, 1, 1), periods=3)
    assert stream.entries[0].amount == -50_000


def test_escalation_compounds_correctly():
    """Annual escalation compounds multiplicatively across successive periods."""
    stream = fixed_opex(amount=100_000, start=date(2025, 1, 1), periods=3, escalation=0.02)
    assert stream.entries[0].amount == pytest.approx(-100_000)
    assert stream.entries[1].amount == pytest.approx(-102_000)
    assert stream.entries[2].amount == pytest.approx(-104_040)


def test_annual_escalation_is_date_based_for_monthly_opex():
    """Bare escalation remains annual even when OPEX recurs monthly."""
    start = date(2025, 1, 1)
    stream = fixed_opex(
        amount=12_000,
        start=start,
        periods=3,
        frequency="month",
        escalation=0.12,
    )
    expected_dates = [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)]
    expected_amounts = [-12_000 * _annual_factor(start, flow_date, 0.12) for flow_date in expected_dates]
    for i, flow in enumerate(stream.entries):
        assert flow.date == expected_dates[i]
        assert flow.amount == pytest.approx(expected_amounts[i])


def test_new_escalation_kwargs_are_forwarded():
    """fixed_opex forwards explicit escalation kwargs to recurring generation."""
    stream = fixed_opex(
        amount=10_000,
        start=date(2025, 3, 1),
        periods=3,
        frequency="month",
        escalation=0.01,
        escalation_period="month",
        amount_reference_date=date(2025, 1, 1),
    )
    expected_amounts = [
        -10_000 * (1.01**2),
        -10_000 * (1.01**3),
        -10_000 * (1.01**4),
    ]
    for i, flow in enumerate(stream.entries):
        assert flow.amount == pytest.approx(expected_amounts[i])


def test_escalation_policy_is_forwarded():
    policy = IndexSeriesEscalation(
        reference_date=date(2025, 1, 1),
        points=(
            (date(2025, 1, 1), 100.0),
            (date(2025, 2, 1), 102.0),
            (date(2025, 3, 1), 104.04),
        ),
    )
    stream = fixed_opex(
        amount=10_000,
        start=date(2025, 1, 15),
        periods=3,
        frequency="month",
        escalation_policy=policy,
    )

    assert [flow.amount for flow in stream.entries] == pytest.approx([-10_000.0, -10_200.0, -10_404.0])


def test_non_default_frequency():
    """Quarterly frequency spaces flows three months apart."""
    stream = fixed_opex(amount=10_000, start=date(2025, 1, 1), periods=4, frequency="quarter")
    assert len(stream.entries) == 4
    assert stream.entries[1].date == date(2025, 4, 1)


def test_custom_label_with_template():
    """Label template '{n}' is replaced with the 1-based period index."""
    stream = fixed_opex(amount=1000, start=date(2025, 1, 1), periods=2, label="Maintenance {n}")
    assert stream.entries[0].label == "Maintenance 1"
    assert stream.entries[1].label == "Maintenance 2"


def test_custom_classification_applied_to_all_flows():
    """Caller-supplied classification overrides the defaults on every flow."""
    stream = fixed_opex(
        amount=1000,
        start=date(2025, 1, 1),
        periods=3,
        pro_forma_category="other",
        tax_treatment="none",
    )
    assert all(f.pro_forma_category is ProFormaCategory.OTHER for f in stream.entries)
    assert all(f.tax_treatment is TaxTreatment.NONE for f in stream.entries)


def test_default_classification_is_opex_deductible():
    """Default classification is operating cost plus deductible tax treatment."""
    stream = fixed_opex(amount=1000, start=date(2025, 1, 1), periods=1)
    assert stream.entries[0].pro_forma_category is ProFormaCategory.OPERATING_COST
    assert stream.entries[0].tax_treatment is TaxTreatment.DEDUCTIBLE
