"""Tests for variable declining balance depreciation."""

from datetime import date

import pytest

from dcaf import CashFlowTags, vdb, vdb_schedule


def test_vdb_matches_documented_excel_example():
    """Excel's published VDB example should match exactly."""
    depreciation = vdb(
        cost=35000.0,
        salvage=7500.0,
        life=36.0,
        start_period=10.0,
        end_period=20.0,
    )
    assert depreciation == pytest.approx(8603.80, abs=0.01)


def test_vdb_matches_documented_partial_period_example():
    """Partial-period depreciation should match the documented example."""
    depreciation = vdb(
        cost=2400.0,
        salvage=300.0,
        life=10.0,
        start_period=0.0,
        end_period=0.875,
        factor=1.5,
    )
    assert depreciation == pytest.approx(315.0)


def test_vdb_schedule_switches_to_straight_line_when_higher():
    """The schedule should switch from declining balance to straight-line."""
    stream = vdb_schedule(
        cost_basis=1000.0,
        salvage_value=100.0,
        placed_in_service=date(2026, 1, 1),
        life=5,
        factor=1.25,
    )

    expected_amounts = [-250.0, -187.5, -154.1666666667, -154.1666666667, -154.1666666667]
    assert [entry.amount for entry in stream.entries] == pytest.approx(expected_amounts)
    assert sum(entry.amount for entry in stream.entries) == pytest.approx(-900.0)


def test_vdb_schedule_can_disable_straight_line_switch():
    """no_switch behavior should remain on declining balance."""
    stream = vdb_schedule(
        cost_basis=1000.0,
        salvage_value=100.0,
        placed_in_service=date(2026, 1, 1),
        life=5,
        factor=1.25,
        switch_to_straight_line=False,
    )

    expected_amounts = [-250.0, -187.5, -140.625, -105.46875, -79.1015625]
    assert [entry.amount for entry in stream.entries] == pytest.approx(expected_amounts)
    assert sum(entry.amount for entry in stream.entries) == pytest.approx(-762.6953125)


def test_vdb_schedule_matches_vdb_rollup_and_metadata():
    """Schedule periods should roll up to the same total as vdb()."""
    stream = vdb_schedule(
        cost_basis=35000.0,
        salvage_value=7500.0,
        placed_in_service=date(2026, 1, 15),
        life=36,
        frequency="month",
    )

    assert stream.count() == 36
    assert stream.entries[0].date == date(2026, 1, 15)
    assert stream.entries[1].date == date(2026, 2, 15)
    assert stream.entries[2].date == date(2026, 3, 15)
    assert all(entry.amount <= 0 for entry in stream.entries)
    assert all(not entry.is_cash for entry in stream.entries)
    for entry in stream.entries:
        assert entry.has_tag(CashFlowTags.DEPRECIATION)
        assert entry.has_tag(CashFlowTags.TAX_DEDUCTIBLE)

    schedule_total = -sum(entry.amount for entry in stream.entries[10:20])
    direct_total = vdb(
        cost=35000.0,
        salvage=7500.0,
        life=36.0,
        start_period=10.0,
        end_period=20.0,
    )
    assert schedule_total == pytest.approx(direct_total)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "cost": -1.0,
                "salvage": 0.0,
                "life": 5.0,
                "start_period": 0.0,
                "end_period": 1.0,
            },
            "cost must be non-negative",
        ),
        (
            {
                "cost": 100.0,
                "salvage": 101.0,
                "life": 5.0,
                "start_period": 0.0,
                "end_period": 1.0,
            },
            "salvage must not exceed cost",
        ),
        (
            {
                "cost": 100.0,
                "salvage": 0.0,
                "life": 0.0,
                "start_period": 0.0,
                "end_period": 1.0,
            },
            "life must be positive",
        ),
        (
            {
                "cost": 100.0,
                "salvage": 0.0,
                "life": 5.0,
                "start_period": 2.0,
                "end_period": 1.0,
            },
            "end_period must be greater than or equal to start_period",
        ),
    ],
)
def test_vdb_rejects_invalid_inputs(kwargs, message):
    """Invalid VDB inputs should raise ValueError."""
    with pytest.raises(ValueError, match=message):
        vdb(**kwargs)


def test_vdb_schedule_rejects_non_integer_life():
    """Schedule generation requires a positive integer number of periods."""
    with pytest.raises(ValueError, match="life must be a positive integer"):
        vdb_schedule(
            cost_basis=1000.0,
            salvage_value=100.0,
            placed_in_service=date(2026, 1, 1),
            life=5.0,  # type: ignore[arg-type]
        )
