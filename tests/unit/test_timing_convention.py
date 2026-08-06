# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for the timing convention feature.

Verifies that cashflows are dated according to the timing convention
(end-of-period or begin-of-period), capped by phase boundaries, when created
through the high-level EnergyProject builder. Physical generation retains only
its period bounds.
"""

from datetime import date, timedelta

import pytest

from dcaf import GenerationPrice
from dcaf.shared.time import ScheduleTruncationWarning, event_date
from dcaf.project.builder import EnergyProject
from dcaf.finance.amortization import AmortizationSchedule


# ---------------------------------------------------------------------------
# event_date utility
# ---------------------------------------------------------------------------


class TestEventDate:
    """Unit tests for the event_date utility function."""

    def test_end_timing_returns_calendar_period_end(self):
        dt = date(2025, 3, 15)
        assert event_date(dt, "year", "end") == date(2025, 12, 31)
        assert event_date(dt, "month", "end") == date(2025, 3, 31)
        assert event_date(dt, "quarter", "end") == date(2025, 3, 31)
        assert event_date(dt, "day", "end") == date(2025, 3, 15)

    def test_end_timing_capped_by_phase_end(self):
        dt = date(2027, 2, 1)
        phase_end = date(2027, 5, 17)
        assert event_date(dt, "year", "end", phase_end=phase_end) == date(2027, 5, 17)

    def test_end_timing_no_cap_when_period_end_before_phase_end(self):
        dt = date(2025, 2, 1)
        phase_end = date(2027, 5, 17)
        assert event_date(dt, "year", "end", phase_end=phase_end) == date(2025, 12, 31)

    def test_end_timing_none_phase_end_returns_calendar_end(self):
        dt = date(2025, 6, 15)
        assert event_date(dt, "year", "end", phase_end=None) == date(2025, 12, 31)

    def test_begin_timing_returns_calendar_period_start(self):
        dt = date(2026, 5, 15)
        assert event_date(dt, "year", "begin") == date(2026, 1, 1)
        assert event_date(dt, "month", "begin") == date(2026, 5, 1)
        assert event_date(dt, "quarter", "begin") == date(2026, 4, 1)
        assert event_date(dt, "day", "begin") == date(2026, 5, 15)

    def test_begin_timing_floored_by_phase_start(self):
        dt = date(2025, 2, 1)
        phase_start = date(2025, 2, 1)
        # period_start("year") = 2025-01-01, but phase_start = 2025-02-01
        assert event_date(dt, "year", "begin", phase_start=phase_start) == date(2025, 2, 1)

    def test_begin_timing_uses_calendar_start_when_after_phase_start(self):
        dt = date(2026, 2, 1)
        phase_start = date(2025, 2, 1)
        # period_start("year") = 2026-01-01, which is after phase_start
        assert event_date(dt, "year", "begin", phase_start=phase_start) == date(2026, 1, 1)

    def test_begin_timing_none_phase_start_returns_calendar_start(self):
        dt = date(2025, 6, 15)
        assert event_date(dt, "year", "begin", phase_start=None) == date(2025, 1, 1)

    def test_middle_timing_returns_midpoint_of_calendar_period(self):
        dt = date(2025, 3, 15)
        # Year: midpoint of Jan 1 – Dec 31 = Jul 2 (182 days / 2 = 91 days from Jan 1)
        assert event_date(dt, "year", "middle") == date(2025, 1, 1) + timedelta(days=182)
        # Month: midpoint of Mar 1 – Mar 31 = Mar 16 (30 days / 2 = 15 days from Mar 1)
        assert event_date(dt, "month", "middle") == date(2025, 3, 16)

    def test_middle_timing_capped_by_phase_boundaries(self):
        dt = date(2027, 2, 1)
        phase_start = date(2027, 2, 1)
        phase_end = date(2027, 5, 17)
        # Effective range: 2027-02-01 to 2027-05-17 = 105 days, mid = 52 days
        result = event_date(dt, "year", "middle", phase_start=phase_start, phase_end=phase_end)
        expected = date(2027, 2, 1) + timedelta(days=52)
        assert result == expected

    def test_middle_timing_partial_first_period(self):
        # First period of operations starting mid-year
        dt = date(2025, 6, 15)
        phase_start = date(2025, 6, 15)
        # Calendar year: Jan 1 – Dec 31, but floored to Jun 15
        # Effective: Jun 15 – Dec 31 = 199 days, mid = 99 days
        result = event_date(dt, "year", "middle", phase_start=phase_start)
        expected = date(2025, 6, 15) + timedelta(days=99)
        assert result == expected

    def test_middle_timing_partial_last_period(self):
        dt = date(2027, 1, 1)
        phase_end = date(2027, 3, 10)
        # Calendar year: Jan 1 – Dec 31, but capped to Mar 10
        # Effective: Jan 1 – Mar 10 = 68 days, mid = 34 days
        result = event_date(dt, "year", "middle", phase_end=phase_end)
        expected = date(2027, 1, 1) + timedelta(days=34)
        assert result == expected

    def test_day_frequency_returns_dt_for_all_conventions(self):
        dt = date(2025, 7, 4)
        assert event_date(dt, "day", "end") == dt
        assert event_date(dt, "day", "begin") == dt
        assert event_date(dt, "day", "middle") == dt


# ---------------------------------------------------------------------------
# Builder: construction phase (end-of-period timing)
# ---------------------------------------------------------------------------


class TestConstructionTimingEndOfPeriod:
    """The user's primary example: annual CAPEX during construction."""

    def test_annual_capex_end_timing(self):
        """Construction is active from 2025-02-01 through 2027-05-17, annually.

        Its exclusive end is 2027-05-18. Expected booking dates are
        2025-12-31, 2026-12-31, and the last included day, 2027-05-17.
        """
        project = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2027, 5, 18),
                operations_end=date(2031, 1, 1),
            )
            .construction(
                overnight_cost=3_000_000,
                spend_profile="flat",
                construction_start=date(2025, 2, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        )
        analysis = project.analyze()
        capex_stream = analysis.cashflow_components["construction"]
        capex_dates = [cf.date for cf in capex_stream.entries]
        assert capex_dates == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 5, 17),
        ]

    def test_midyear_construction_uses_calendar_year_periods(self):
        analysis = (
            EnergyProject()
            .construction(
                overnight_cost=1_000_000,
                cod_date=date(2030, 4, 16),
                spend_profile="flat",
                construction_start=date(2025, 7, 1),
                construction_end=date(2030, 4, 16),
                period="year",
            )
            .analyze()
        )

        assert [flow.date for flow in analysis.cashflow_components["construction"]] == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 12, 31),
            date(2028, 12, 31),
            date(2029, 12, 31),
            date(2030, 4, 15),
        ]

    def test_construction_timing_overrides_project_timing(self):
        analysis = (
            EnergyProject(timing="end")
            .construction(
                overnight_cost=1_000_000,
                cod_date=date(2027, 4, 16),
                spend_profile="flat",
                construction_start=date(2025, 7, 1),
                construction_end=date(2027, 4, 16),
                period="year",
                timing="begin",
            )
            .analyze()
        )

        assert [flow.date for flow in analysis.cashflow_components["construction"]] == [
            date(2025, 7, 1),
            date(2026, 1, 1),
            date(2027, 1, 1),
        ]

    def test_construction_inherits_project_timing(self):
        analysis = (
            EnergyProject(timing="begin")
            .construction(
                overnight_cost=1_000_000,
                cod_date=date(2027, 4, 16),
                spend_profile="flat",
                construction_start=date(2025, 7, 1),
                construction_end=date(2027, 4, 16),
                period="year",
            )
            .analyze()
        )

        assert [flow.date for flow in analysis.cashflow_components["construction"]] == [
            date(2025, 7, 1),
            date(2026, 1, 1),
            date(2027, 1, 1),
        ]


# ---------------------------------------------------------------------------
# Builder: operations phase (end-of-period timing)
# ---------------------------------------------------------------------------


class TestOperationsTimingEndOfPeriod:
    """Operations-phase events with default end-of-period timing."""

    @pytest.fixture
    def project(self):
        return (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2030, 6, 15),
                operations_end=date(2032, 3, 11),
            )
            .construction(
                overnight_cost=1000,
                spend_profile="upfront",
                construction_start=date(2029, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .fixed_opex(amount=1_000_000, frequency="year")
        )

    def test_generation_uses_physical_period_bounds(self, project):
        analysis = project.analyze()
        assert [(g.date, g.period_start, g.period_end) for g in analysis.generation] == [
            (None, date(2030, 6, 15), date(2032, 3, 11))
        ]

    def test_fixed_opex_dates_end_of_period(self, project):
        analysis = project.analyze()
        opex_stream = analysis.cashflow_components["fixed_opex"]
        opex_dates = [cf.date for cf in opex_stream.entries]
        assert opex_dates == [
            date(2030, 12, 31),
            date(2031, 12, 31),
        ]

    def test_revenue_inherits_generation_dates(self, project):
        analysis = project.analyze()
        revenue_stream = analysis.cashflow_components["revenue"]
        revenue_dates = [cf.date for cf in revenue_stream.entries]
        assert revenue_dates == [
            date(2030, 12, 31),
            date(2031, 12, 31),
            date(2032, 3, 10),
        ]


# ---------------------------------------------------------------------------
# Builder: begin-of-period timing
# ---------------------------------------------------------------------------


class TestBeginOfPeriodTiming:
    """Events placed at the start of the calendar period, floored by phase start."""

    def test_generation_revenue_dates_begin_timing(self):
        project = (
            EnergyProject(timing="begin")
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 2, 1),
                operations_end=date(2027, 5, 18),
            )
            .construction(
                overnight_cost=1000,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        )
        analysis = project.analyze()
        revenue_dates = [cf.date for cf in analysis.cashflow_components["revenue"]]
        # begin timing: max(period_start(dt, year), ops_start)
        # dt=2025-02-01: period_start=2025-01-01, max(2025-01-01, 2025-02-01) = 2025-02-01
        # dt=2026-02-01: period_start=2026-01-01, max(2026-01-01, 2025-02-01) = 2026-01-01
        # dt=2027-02-01: period_start=2027-01-01, max(2027-01-01, 2025-02-01) = 2027-01-01
        assert revenue_dates == [
            date(2025, 2, 1),
            date(2026, 1, 1),
            date(2027, 1, 1),
        ]

    def test_fixed_opex_dates_begin_timing(self):
        project = (
            EnergyProject(timing="begin")
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 3, 15),
                operations_end=date(2028, 1, 1),
            )
            .construction(
                overnight_cost=1000,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .fixed_opex(amount=500_000, frequency="year")
        )
        analysis = project.analyze()
        opex_stream = analysis.cashflow_components["fixed_opex"]
        opex_dates = [cf.date for cf in opex_stream.entries]
        # max(period_start, ops_start=2025-03-15)
        # dt=2025-03-15: period_start=2025-01-01, max=2025-03-15
        # dt=2026-03-15: period_start=2026-01-01, max=2026-01-01
        # dt=2027-03-15: period_start=2027-01-01, max=2027-01-01
        assert opex_dates == [
            date(2025, 3, 15),
            date(2026, 1, 1),
            date(2027, 1, 1),
        ]


# ---------------------------------------------------------------------------
# Builder: middle-of-period timing
# ---------------------------------------------------------------------------


class TestMiddleOfPeriodTiming:
    """Events placed at the midpoint of the effective period window."""

    def test_generation_revenue_dates_middle_timing(self):
        project = (
            EnergyProject(timing="middle")
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 1, 1),
                operations_end=date(2028, 1, 1),
            )
            .construction(
                overnight_cost=1000,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        )
        analysis = project.analyze()
        revenue_dates = [cf.date for cf in analysis.cashflow_components["revenue"]]
        # Full calendar years, midpoint = Jul 2 (182 days from Jan 1)
        assert revenue_dates[0] == date(2025, 1, 1) + timedelta(days=182)
        assert revenue_dates[1] == date(2026, 1, 1) + timedelta(days=182)
        assert revenue_dates[2] == date(2027, 1, 1) + timedelta(days=182)

    def test_middle_timing_partial_first_and_last_period(self):
        project = (
            EnergyProject(timing="middle")
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 4, 1),
                operations_end=date(2027, 9, 16),
            )
            .construction(
                overnight_cost=1000,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        )
        analysis = project.analyze()
        revenue_dates = [cf.date for cf in analysis.cashflow_components["revenue"]]
        # Period 1 (dt=2025-04-01): effective Apr 1 – Dec 31 (275 days), mid = 137
        assert revenue_dates[0] == date(2025, 4, 1) + timedelta(days=137)
        # Period 2 (dt=2026-04-01): full year Jan 1 – Dec 31 (365 days), mid = 182
        assert revenue_dates[1] == date(2026, 1, 1) + timedelta(days=182)
        # Period 3 (dt=2027-04-01): effective Jan 1 – Sep 15 (257 days), mid = 128
        assert revenue_dates[2] == date(2027, 1, 1) + timedelta(days=128)


# ---------------------------------------------------------------------------
# Per-component timing override
# ---------------------------------------------------------------------------


class TestPerComponentTimingOverride:
    """Per-component timing overrides the project-level default."""

    def test_fixed_opex_begin_with_project_end(self):
        project = (
            EnergyProject(timing="end")
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 4, 1),
                operations_end=date(2028, 1, 1),
            )
            .construction(
                overnight_cost=1000,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .fixed_opex(amount=100_000, frequency="year", timing="begin")
        )
        analysis = project.analyze()

        revenue_dates = [cf.date for cf in analysis.cashflow_components["revenue"]]
        assert revenue_dates == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 12, 31),
        ]

        # The explicit fixed-OPEX timing rule remains independent of the project default.
        opex_stream = analysis.cashflow_components["fixed_opex"]
        opex_dates = [cf.date for cf in opex_stream.entries]
        assert opex_dates == [
            date(2025, 4, 1),
            date(2026, 1, 1),
            date(2027, 1, 1),
        ]


# ---------------------------------------------------------------------------
# Depreciation and debt remapping
# ---------------------------------------------------------------------------


class TestDepreciationRemapping:
    """Depreciation dates remapped to calendar year-ends."""

    def test_macrs_dates_remapped_to_year_end(self):
        project = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 6, 15),
                operations_end=date(2041, 1, 1),
            )
            .construction(
                overnight_cost=1_000_000,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .depreciation_macrs(property_class=5)
        )
        analysis = project.analyze()
        dep_stream = analysis.cashflow_components["depreciation"]
        dep_dates = [cf.date for cf in dep_stream.entries]
        # MACRS 5-year generates dates at placed_in_service + i years.
        # With "end" timing, remapped to min(year-end, ops_end):
        # 2025-06-15 → min(2025-12-31, 2040-12-31) = 2025-12-31
        # 2026-06-15 → 2026-12-31
        # ...
        assert dep_dates[0] == date(2025, 12, 31)
        assert dep_dates[1] == date(2026, 12, 31)

    def test_macrs_dates_after_operations_end_are_truncated_with_warning(self):
        project = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2026, 1, 1),
                operations_end=date(2028, 1, 1),
            )
            .construction(
                overnight_cost=1_000,
                spend_profile="upfront",
                construction_start=date(2025, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .depreciation_macrs(property_class=5)
        )

        with pytest.warns(
            ScheduleTruncationWarning,
            match="depreciation schedule truncated at operations_end 2028-01-01",
        ) as caught:
            analysis = project.analyze()

        assert len(caught) == 1
        dep_stream = analysis.cashflow_components["depreciation"]
        assert [(cf.date, cf.amount) for cf in dep_stream.entries] == [
            (date(2026, 12, 31), -200.0),
            (date(2027, 12, 31), -320.0),
        ]


class TestDebtBooking:
    """Debt service is allocated and booked per the project calendar.

    Construction-debt amortization schedules (built internally) get their
    payments allocated across calendar periods and booked according to the
    project timing convention. Explicit debt_schedule overrides are passed
    through as-is.
    """

    def test_construction_debt_amortization_prorates_first_and_last_periods(self):
        project = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 7, 1),
                operations_end=date(2028, 1, 1),
            )
            .construction(
                overnight_cost=1_200,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .construction_financing(
                debt_fraction=1.0,
                amortization_rate=0.0,
                amortization_term=2,
                amortization_frequency="year",
            )
        )
        analysis = project.analyze()
        principal = [
            flow
            for flow in analysis.cashflow_components["debt_service"]
            if flow.label == "Principal"
        ]

        assert [flow.date for flow in principal] == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 6, 30),
        ]
        assert [flow.amount for flow in principal] == pytest.approx(
            [-600.0 * 184.0 / 365.0, -600.0, -600.0 * 181.0 / 365.0]
        )
        assert sum(flow.amount for flow in principal) == pytest.approx(-1_200.0)

    def test_construction_debt_amortization_prorates_across_leap_day(self):
        analysis = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                operations_start=date(2023, 7, 1),
                operations_end=date(2025, 1, 1),
            )
            .construction(
                overnight_cost=3_660,
                spend_profile="upfront",
                construction_start=date(2023, 1, 1),
                period="year",
            )
            .construction_financing(
                debt_fraction=1.0,
                amortization_rate=0.10,
                amortization_term=1,
                amortization_frequency="year",
            )
            .analyze()
        )
        debt_service = analysis.cashflow_components["debt_service"]
        interest = [flow for flow in debt_service if flow.label == "Interest"]
        principal = [flow for flow in debt_service if flow.label == "Principal"]

        assert [flow.date for flow in principal] == [date(2023, 12, 31), date(2024, 6, 30)]
        assert [flow.amount for flow in principal] == pytest.approx([-1_840.0, -1_820.0])
        assert [flow.amount for flow in interest] == pytest.approx([-184.0, -182.0])

    def test_construction_debt_amortization_preserves_month_end_anchor(self):
        analysis = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                operations_start=date(2025, 1, 1),
                operations_end=date(2025, 6, 1),
            )
            .construction(
                overnight_cost=300,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
            )
            .construction_financing(
                debt_fraction=1.0,
                amortization_rate=0.0,
                amortization_term=3,
                amortization_frequency="month",
                amortization_start=date(2025, 1, 31),
            )
            .analyze()
        )
        principal = [
            flow
            for flow in analysis.cashflow_components["debt_service"]
            if flow.label == "Principal"
        ]

        assert [flow.date for flow in principal] == [
            date(2025, 1, 31),
            date(2025, 2, 28),
            date(2025, 3, 31),
            date(2025, 4, 29),
        ]
        assert [flow.amount for flow in principal] == pytest.approx(
            [
                -100.0 / 28.0,
                -100.0 * (27.0 / 28.0 + 1.0 / 31.0),
                -100.0 * (30.0 / 31.0 + 1.0 / 30.0),
                -100.0 * 29.0 / 30.0,
            ]
        )
        assert sum(flow.amount for flow in principal) == pytest.approx(-300.0)

    def test_construction_debt_after_operations_end_is_truncated_with_warning(self):
        project = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2026, 1, 1),
                operations_end=date(2028, 1, 1),
            )
            .construction(
                overnight_cost=1_000,
                spend_profile="upfront",
                construction_start=date(2025, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
            .construction_financing(
                debt_fraction=1.0,
                amortization_rate=0.05,
                amortization_term=10,
                amortization_frequency="year",
            )
        )

        with pytest.warns(
            ScheduleTruncationWarning,
            match="debt_service schedule truncated at operations_end 2028-01-01",
        ) as caught:
            analysis = project.analyze()

        assert len(caught) == 1
        debt_stream = analysis.cashflow_components["debt_service"]
        assert sorted({cf.date for cf in debt_stream.entries}) == [
            date(2026, 12, 31),
            date(2027, 12, 31),
        ]
        assert debt_stream.count() == 4

    def test_explicit_debt_schedule_after_operations_end_is_truncated_with_warning(self):
        schedule = AmortizationSchedule.build(
            principal=1_000.0,
            annual_rate=0.05,
            term=3,
            start_date=date(2026, 1, 1),
            frequency="year",
        )
        project = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2026, 1, 1),
                operations_end=date(2028, 1, 1),
            )
            .debt_schedule(schedule=schedule)
        )

        with pytest.warns(
            ScheduleTruncationWarning,
            match="debt_service schedule truncated at operations_end 2028-01-01",
        ) as caught:
            analysis = project.analyze()

        assert len(caught) == 1
        debt_stream = analysis.cashflow_components["debt_service"]
        assert sorted({cf.date for cf in debt_stream.entries}) == [
            date(2026, 1, 1),
            date(2027, 1, 1),
        ]
        assert debt_stream.count() == 4


class TestOperationsHorizonTruncation:
    """Explicit operating period counts are truncated at operations_end."""

    def test_explicit_period_generation_and_opex_truncate_at_operations_end(self):
        project = (
            EnergyProject()
            .generation(
                capacity_mw=1.0,
                capacity_factor=1.0,
                operations_start=date(2026, 1, 1),
                operations_end=date(2027, 7, 1),
                start=date(2026, 1, 1),
                periods=3,
            )
            .fixed_opex(
                amount=365.0,
                start=date(2026, 1, 1),
                periods=3,
                frequency="year",
            )
        )

        with pytest.warns(ScheduleTruncationWarning) as caught:
            analysis = project.analyze()

        messages = [str(item.message) for item in caught]
        assert len(messages) == 2
        assert any("generation schedule requested through 2029-01-01" in msg for msg in messages)
        assert any("fixed_opex schedule requested through 2029-01-01" in msg for msg in messages)
        assert [
            (g.date, g.period_start, g.period_end, g.amount_mwh)
            for g in analysis.generation.entries
        ] == [
            (None, date(2026, 1, 1), date(2027, 7, 1), 13_104.0),
        ]
        assert [
            (cf.date, cf.amount) for cf in analysis.cashflow_components["fixed_opex"].entries
        ] == [
            (date(2026, 12, 31), -365.0),
            (date(2027, 6, 30), -181.0),
        ]


# ---------------------------------------------------------------------------
# No operations_end set (explicit periods, no phase capping)
# ---------------------------------------------------------------------------


class TestNoOperationsEnd:
    """When operations_end is not set, no phase capping occurs."""

    def test_explicit_periods_use_calendar_end_uncapped(self):
        project = (
            EnergyProject()
            .generation(
                capacity_mw=100,
                capacity_factor=0.9,
                operations_start=date(2025, 6, 1),
                periods=3,
            )
            .construction(
                overnight_cost=1000,
                spend_profile="upfront",
                construction_start=date(2024, 1, 1),
                period="year",
            )
            .generation_revenue(price_policy=GenerationPrice.fixed(50.0))
        )
        analysis = project.analyze()
        revenue_dates = [cf.date for cf in analysis.cashflow_components["revenue"]]
        assert revenue_dates == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 12, 31),
            date(2028, 5, 31),
        ]


# ---------------------------------------------------------------------------
# Construction standalone function
# ---------------------------------------------------------------------------


class TestConstructionStandaloneTimingFix:
    """construction_spend_schedule uses calendar period-ends for booking dates."""

    def test_annual_construction_calendar_year_ends(self):
        from dcaf.finance.construction import construction_spend_schedule

        stream = construction_spend_schedule(
            total_cost=3_000_000,
            start_date=date(2025, 2, 1),
            end_date=date(2027, 5, 18),
            period="year",
            profile="flat",
        )
        spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
        dates = [cf.date for cf in spend_flows]
        # phase_end = 2027-05-18 - 1 day = 2027-05-17
        assert dates == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 5, 17),
        ]

    def test_monthly_construction_month_ends(self):
        from dcaf.finance.construction import construction_spend_schedule

        stream = construction_spend_schedule(
            total_cost=100_000,
            start_date=date(2025, 1, 15),
            end_date=date(2025, 4, 20),
            period="month",
            profile="flat",
        )
        spend_flows = [cf for cf in stream.entries if cf.label == "Construction Spend"]
        dates = [cf.date for cf in spend_flows]
        # The exclusive April 20 end makes April 19 the final booking date.
        assert dates == [
            date(2025, 1, 31),
            date(2025, 2, 28),
            date(2025, 3, 31),
            date(2025, 4, 19),
        ]

    def test_explicit_begin_timing_books_calendar_window_starts(self):
        from dcaf.finance.construction import construction_spend_schedule

        stream = construction_spend_schedule(
            total_cost=100_000,
            start_date=date(2025, 1, 15),
            end_date=date(2025, 4, 20),
            period="month",
            profile="flat",
            timing="begin",
        )

        assert [flow.date for flow in stream if flow.label == "Construction Spend"] == [
            date(2025, 1, 15),
            date(2025, 2, 1),
            date(2025, 3, 1),
            date(2025, 4, 1),
        ]
