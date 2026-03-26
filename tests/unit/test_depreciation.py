"""Tests for variable declining balance depreciation."""

from datetime import date

import pytest

from dcaf import ProFormaCategory, TaxTreatment, vdb, vdb_schedule


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
        assert entry.pro_forma_category is ProFormaCategory.DEPRECIATION
        assert entry.tax_treatment is TaxTreatment.DEDUCTIBLE

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


def test_vdb_schedule_default_label():
    """Default label should not have interpolated indices."""
    stream = vdb_schedule(
        cost_basis=1000,
        salvage_value=200,
        placed_in_service=date(2027, 1, 1),
        life=20,
    )
    # Check that label does not change between periods
    assert stream.entries[0].label == stream.entries[1].label


def test_vdb_schedule_label_with_interpolated_index():
    """Index placeholder in custom label should be interpolated."""
    stream = vdb_schedule(
        cost_basis=1000,
        salvage_value=200,
        placed_in_service=date(2027, 1, 1),
        life=20,
        frequency="quarter",
        label="vdb depreciation period {n}"
    )
    assert stream.entries[0].label == "vdb depreciation period 1"
    assert stream.entries[19].label == "vdb depreciation period 20"


def test_vdb_schedule_half_year_convention_can_add_terminal_catch_up():
    """Convention-aware schedules can add a residual terminal period."""
    stream = vdb_schedule(
        cost_basis=1000.0,
        salvage_value=0.0,
        placed_in_service=date(2026, 1, 1),
        life=5,
        convention="half-year",
        terminal_catch_up=True,
    )

    assert stream.count() == 6
    assert stream.entries[0].date == date(2027, 1, 1)
    assert -stream.entries[0].amount == pytest.approx(200.0)
    assert -stream.entries[-1].amount == pytest.approx(54.0)
    assert sum(entry.amount for entry in stream.entries) == pytest.approx(-1000.0)


def test_vdb_schedule_mid_quarter_convention_uses_explicit_date_grid():
    """Convention-aware schedules should align entries to the supplied date grid."""
    schedule_dates = (
        date(2029, 12, 31),
        date(2030, 12, 31),
        date(2031, 12, 31),
        date(2032, 12, 31),
        date(2033, 12, 31),
        date(2034, 12, 31),
        date(2035, 12, 31),
    )
    stream = vdb_schedule(
        cost_basis=1000.0,
        salvage_value=0.0,
        placed_in_service=date(2030, 6, 30),
        life=5,
        convention="mid-quarter",
        schedule_dates=schedule_dates,
        terminal_catch_up=True,
        label="VDB Depreciation Period {n}"
    )

    assert [entry.date for entry in stream.entries] == list(schedule_dates[1:])
    assert stream.entries[0].label == "VDB Depreciation Period 1"
    assert -stream.entries[0].amount == pytest.approx(150.0)


def test_vdb_schedule_best_of_convention_requires_valuation_inputs():
    """Candidate selection needs valuation inputs for the NPV comparison."""
    with pytest.raises(
        ValueError,
        match="valuation_rate and valuation_date are required",
    ):
        vdb_schedule(
            cost_basis=1000.0,
            salvage_value=0.0,
            placed_in_service=date(2026, 1, 1),
            life=5,
            convention="best-of-half-year-mid-quarter",
        )


def test_vdb_schedule_best_of_convention_matches_workbook_shape():
    """Best-of mode should select the workbook-like mid-quarter candidate for the fixture case."""
    schedule_dates = tuple(date(year, 12, 31) for year in range(2030, 2047))
    stream = vdb_schedule(
        cost_basis=877824.3662585187,
        salvage_value=0.0,
        placed_in_service=date(2030, 12, 31),
        life=15,
        factor=1.5,
        convention="best-of-half-year-mid-quarter",
        schedule_dates=schedule_dates,
        valuation_rate=0.10,
        valuation_date=date(2030, 12, 31),
        terminal_catch_up=True,
    )

    expected_amounts = [
        -76809.63204762038,
        -80101.47342108983,
        -72091.32607898084,
        -64882.19347108276,
        -58393.97412397449,
        -52554.576711577036,
    ]

    assert stream.entries[0].date == date(2031, 12, 31)
    assert [entry.amount for entry in stream.entries[:6]] == pytest.approx(expected_amounts)
    assert sum(entry.amount for entry in stream.entries) == pytest.approx(-877824.3662585187)


def test_vdb_schedule_best_of_convention_prefers_mid_quarter_candidate():
    """The selector should pick the higher-value convention candidate, not always half-year."""
    schedule_dates = tuple(date(year, 12, 31) for year in range(2030, 2047))
    half_year = vdb_schedule(
        cost_basis=877824.3662585187,
        salvage_value=0.0,
        placed_in_service=date(2030, 12, 31),
        life=15,
        factor=1.5,
        convention="half-year",
        schedule_dates=schedule_dates,
        terminal_catch_up=True,
    )
    best_of = vdb_schedule(
        cost_basis=877824.3662585187,
        salvage_value=0.0,
        placed_in_service=date(2030, 12, 31),
        life=15,
        factor=1.5,
        convention="best-of-half-year-mid-quarter",
        schedule_dates=schedule_dates,
        valuation_rate=0.10,
        valuation_date=date(2030, 12, 31),
        terminal_catch_up=True,
    )

    assert [entry.amount for entry in best_of.entries[:3]] != pytest.approx(
        [entry.amount for entry in half_year.entries[:3]]
    )
    assert [entry.amount for entry in best_of.entries[:3]] == pytest.approx(
        [-76809.63204762038, -80101.47342108983, -72091.32607898084]
    )
