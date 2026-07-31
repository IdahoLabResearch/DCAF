# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for variable declining balance depreciation."""

from datetime import date

import pytest

from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.tax import vdb, vdb_schedule
from dcaf.tax.depreciation import _placed_in_service_quarter


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
    assert stream.entries[0].date == date(2026, 1, 1)
    assert stream.entries[-1].date == date(2031, 1, 1)
    assert -stream.entries[0].amount == pytest.approx(200.0)
    assert -stream.entries[-1].amount == pytest.approx(54.0)
    assert sum(entry.amount for entry in stream.entries) == pytest.approx(-1000.0)


def test_vdb_schedule_half_year_convention_without_catch_up_has_life_entries():
    """Including the placed-in-service date must not add an extra schedule period."""
    stream = vdb_schedule(
        cost_basis=1000.0,
        salvage_value=0.0,
        placed_in_service=date(2026, 1, 1),
        life=5,
        convention="half-year",
    )

    assert [entry.date for entry in stream.entries] == [
        date(year, 1, 1) for year in range(2026, 2031)
    ]


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
    )

    assert [entry.date for entry in stream.entries] == list(schedule_dates[1:])
    # 6/30 falls in Q2: first-period length 1 - 0.375 = 0.625, so 1000 * (2/5) * 0.625.
    assert -stream.entries[0].amount == pytest.approx(250.0)


@pytest.mark.parametrize(
    "placed,expected_quarter",
    [
        # Interior dates.
        (date(2030, 2, 15), 1),
        (date(2030, 5, 15), 2),
        (date(2030, 8, 15), 3),
        (date(2030, 11, 15), 4),
        # Quarter-end boundary dates: the literal calendar quarter, with no day shift.
        # (A spurious +1 day previously rolled each of these into the next quarter,
        # and 12/31 all the way back to Q1.)
        (date(2030, 3, 31), 1),
        (date(2030, 6, 30), 2),
        (date(2030, 9, 30), 3),
        (date(2030, 12, 31), 4),
    ],
)
def test_placed_in_service_quarter_uses_literal_calendar_quarter(placed, expected_quarter):
    """The shared quarter helper reads the literal calendar quarter of the date."""
    assert _placed_in_service_quarter(placed) == expected_quarter


@pytest.mark.parametrize(
    "placed,expected_first_period",
    # life=5, factor=2.0 -> first-year DB rate = 1000 * 2/5 = 400; first period spans
    # (1 - shift) of the year, with shift = (2*quarter - 1) / 8.
    [
        (date(2030, 3, 31), 350.0),  # Q1: 400 * 0.875
        (date(2030, 6, 30), 250.0),  # Q2: 400 * 0.625
        (date(2030, 9, 30), 150.0),  # Q3: 400 * 0.375
        (date(2030, 12, 31), 50.0),  # Q4: 400 * 0.125
    ],
)
def test_vdb_mid_quarter_first_period_by_quarter(placed, expected_first_period):
    """Mid-quarter VDB front-loads less depreciation for later quarters, boundaries included."""
    stream = vdb_schedule(
        cost_basis=1000.0,
        salvage_value=0.0,
        placed_in_service=placed,
        life=5,
        convention="mid-quarter",
        schedule_dates=tuple(date(year, 12, 31) for year in range(2029, 2037)),
        terminal_catch_up=True,
    )
    assert stream.entries[0].date == date(2030, 12, 31)
    assert -stream.entries[0].amount == pytest.approx(expected_first_period)


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
    # Workbook models the asset entering service at the start of 2031 (Q1), with the
    # first deduction at year-end 2031. Under literal-date quarter semantics that is
    # the date 2031-01-01, so the preceding 2030-12-31 grid point is skipped.
    stream = vdb_schedule(
        cost_basis=877824.3662585187,
        salvage_value=0.0,
        placed_in_service=date(2031, 1, 1),
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
    # Q1 placement (start of 2031): mid-quarter front-loads more than half-year,
    # so best-of should prefer the mid-quarter candidate over half-year.
    half_year = vdb_schedule(
        cost_basis=877824.3662585187,
        salvage_value=0.0,
        placed_in_service=date(2031, 1, 1),
        life=15,
        factor=1.5,
        convention="half-year",
        schedule_dates=schedule_dates,
        terminal_catch_up=True,
    )
    best_of = vdb_schedule(
        cost_basis=877824.3662585187,
        salvage_value=0.0,
        placed_in_service=date(2031, 1, 1),
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
