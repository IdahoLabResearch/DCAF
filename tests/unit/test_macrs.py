# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for MACRS depreciation and public reference-table accessors."""

from datetime import date

import pytest

from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.tax import get_macrs_mid_quarter_rates, get_macrs_rates, macrs_schedule


MACRS_RATES = get_macrs_rates()
MACRS_MID_QUARTER_RATES = get_macrs_mid_quarter_rates()


# === MACRS_RATES ===


def test_macrs_rates_keys():
    """All six IRS property classes are present."""
    assert set(MACRS_RATES.keys()) == {3, 5, 7, 10, 15, 20}


def test_macrs_rates_are_read_only():
    """Public MACRS half-year tables are exposed as a read-only mapping."""
    with pytest.raises(TypeError):
        MACRS_RATES[3] = (1.0,)  # type: ignore[index]


@pytest.mark.parametrize("prop_class", [3, 5, 7, 10, 15, 20])
def test_macrs_rates_sum_to_one(prop_class):
    """Each property class's rates should sum to approximately 1.0."""
    assert abs(sum(MACRS_RATES[prop_class]) - 1.0) < 1e-3


@pytest.mark.parametrize("prop_class", [3, 5, 7, 10, 15, 20])
def test_macrs_rates_length(prop_class):
    """Each property class has property_class + 1 rates (half-year convention)."""
    assert len(MACRS_RATES[prop_class]) == prop_class + 1


# === from_macrs ===


def test_from_macrs_5year():
    """5-year MACRS produces 6 flows (5+1 for half-year)."""
    stream = macrs_schedule(
        cost_basis=100.0,
        placed_in_service=date(2026, 1, 1),
        property_class=5,
    )
    assert stream.count() == 6
    assert all(f.amount < 0 for f in stream.entries)
    assert abs(sum(f.amount for f in stream.entries) + 100.0) < 0.1
    assert all(not f.is_cash for f in stream.entries)


def test_from_macrs_classification():
    """Default classification is depreciation plus deductible tax treatment."""
    stream = macrs_schedule(
        cost_basis=1000.0,
        placed_in_service=date(2026, 1, 1),
        property_class=3,
    )
    for f in stream.entries:
        assert f.pro_forma_category is ProFormaCategory.DEPRECIATION
        assert f.tax_treatment is TaxTreatment.DEDUCTIBLE


def test_from_macrs_dates():
    """MACRS flows are placed at annual intervals from placed_in_service."""
    stream = macrs_schedule(
        cost_basis=100.0,
        placed_in_service=date(2030, 6, 15),
        property_class=3,
    )
    assert stream.entries[0].date == date(2030, 6, 15)
    assert stream.entries[1].date == date(2031, 6, 15)
    assert stream.entries[2].date == date(2032, 6, 15)
    assert stream.entries[3].date == date(2033, 6, 15)


def test_macrs_schedule_label_default():
    """Default label should not have interpolated indices."""
    stream = macrs_schedule(
        cost_basis=1000,
        placed_in_service=date(2027, 1, 1),
        property_class=3,
    )
    # Check that label does not change between periods
    assert stream.entries[0].label == stream.entries[1].label


def test_macrs_schedule_index_in_label():
    """Index in custom label should be interpolated."""
    stream = macrs_schedule(
        cost_basis=1000,
        placed_in_service=date(2027, 1, 1),
        property_class=3,
        label="macrs depreciation period {n}",
    )
    assert stream.entries[0].label == "macrs depreciation period 1"
    assert stream.entries[1].label == "macrs depreciation period 2"


# === MACRS_MID_QUARTER_RATES ===


def test_macrs_mid_quarter_rates_keys():
    """All six IRS property classes are present, each with four quarters."""
    assert set(MACRS_MID_QUARTER_RATES.keys()) == {3, 5, 7, 10, 15, 20}
    for prop_class in MACRS_MID_QUARTER_RATES:
        assert set(MACRS_MID_QUARTER_RATES[prop_class].keys()) == {1, 2, 3, 4}


def test_macrs_mid_quarter_rates_are_read_only():
    """Public MACRS mid-quarter tables are exposed as nested read-only mappings."""
    with pytest.raises(TypeError):
        MACRS_MID_QUARTER_RATES[3][1] = (1.0,)  # type: ignore[index]


@pytest.mark.parametrize("prop_class", [3, 5, 7, 10, 15, 20])
@pytest.mark.parametrize("quarter", [1, 2, 3, 4])
def test_macrs_mid_quarter_rates_sum_to_one(prop_class, quarter):
    """Each (property_class, quarter) rate tuple sums to approximately 1.0."""
    assert abs(sum(MACRS_MID_QUARTER_RATES[prop_class][quarter]) - 1.0) < 5e-3


@pytest.mark.parametrize("prop_class", [3, 5, 7, 10, 15, 20])
@pytest.mark.parametrize("quarter", [1, 2, 3, 4])
def test_macrs_mid_quarter_rates_length(prop_class, quarter):
    """Mid-quarter rate tuples have the same length as half-year rate tuples."""
    assert len(MACRS_MID_QUARTER_RATES[prop_class][quarter]) == len(MACRS_RATES[prop_class])


# === from_macrs mid-quarter ===


@pytest.mark.parametrize(
    "month,expected_quarter",
    [(1, 1), (3, 1), (4, 2), (6, 2), (7, 3), (9, 3), (10, 4), (12, 4)],
)
def test_from_macrs_mid_quarter_derives_quarter(month, expected_quarter):
    """Quarter is correctly derived from placed_in_service month."""
    stream = macrs_schedule(
        cost_basis=1000.0,
        placed_in_service=date(2026, month, 15),
        property_class=5,
        convention="mid-quarter",
    )
    expected_rates = MACRS_MID_QUARTER_RATES[5][expected_quarter]
    assert abs(stream.entries[0].amount + 1000.0 * expected_rates[0]) < 1e-6


def test_from_macrs_mid_quarter_total_equals_basis():
    """Mid-quarter depreciation sums to the full cost basis."""
    stream = macrs_schedule(
        cost_basis=500.0,
        placed_in_service=date(2026, 11, 1),
        property_class=7,
        convention="mid-quarter",
    )
    assert abs(sum(f.amount for f in stream.entries) + 500.0) < 0.5


def test_from_macrs_mid_quarter_flow_count():
    """Mid-quarter produces the same number of flows as half-year."""
    for prop_class in (3, 5, 7, 10, 15, 20):
        half = macrs_schedule(
            cost_basis=100.0,
            placed_in_service=date(2026, 1, 1),
            property_class=prop_class,
        )
        mid = macrs_schedule(
            cost_basis=100.0,
            placed_in_service=date(2026, 1, 1),
            property_class=prop_class,
            convention="mid-quarter",
        )
        assert half.count() == mid.count()


def test_from_macrs_mid_quarter_q1_higher_first_year():
    """Q1 placement yields a larger first-year deduction than Q4 placement."""
    q1 = macrs_schedule(
        cost_basis=1000.0,
        placed_in_service=date(2026, 1, 15),
        property_class=5,
        convention="mid-quarter",
    )
    q4 = macrs_schedule(
        cost_basis=1000.0,
        placed_in_service=date(2026, 11, 15),
        property_class=5,
        convention="mid-quarter",
    )
    assert abs(q1.entries[0].amount) > abs(q4.entries[0].amount)


def test_from_macrs_half_year_unchanged():
    """Default convention still produces the same result as before."""
    stream = macrs_schedule(
        cost_basis=100.0,
        placed_in_service=date(2026, 1, 1),
        property_class=5,
    )
    assert stream.count() == 6
    assert abs(sum(f.amount for f in stream.entries) + 100.0) < 0.1
