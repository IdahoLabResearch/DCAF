"""Tests for the timing convention feature.

Verifies that CashFlow and Generation events are dated according to the
timing convention (end-of-period or begin-of-period), capped by phase
boundaries, when created through the high-level EnergyProject builder.
"""

from datetime import date, timedelta

import pytest

from dcaf.shared.time import event_date
from dcaf.project.builder import EnergyProject


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
        result = event_date(
            dt, "year", "middle", phase_start=phase_start, phase_end=phase_end
        )
        expected = date(2027, 2, 1) + timedelta(days=52)
        assert result == expected

    def test_middle_timing_partial_first_period(self):
        # First period of operations starting mid-year
        dt = date(2025, 6, 15)
        phase_start = date(2025, 6, 15)
        # Calendar year: Jan 1 – Dec 31, but floored to Jun 15
        # Effective: Jun 15 – Dec 31 = 199 days, mid = 99 days
        result = event_date(
            dt, "year", "middle", phase_start=phase_start
        )
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
        """Construction 2025-02-01 to 2027-05-17, annual.

        Expected dates: 2025-12-31, 2026-12-31, 2027-05-17.
        """
        project = (
            EnergyProject("timing-test")
            .timeline(
                construction_start=date(2025, 2, 1),
                operations_start=date(2027, 5, 18),
                operations_end=date(2030, 12, 31),
                frequency="year",
            )
            .construction(
                overnight_cost=3_000_000,
                spend_profile="flat",
                period="year",
            )
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
        )
        analysis = project.analyze()
        capex_stream = analysis.cashflow_components["default:construction"]
        capex_dates = [cf.date for cf in capex_stream.entries]
        assert capex_dates == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 5, 17),
        ]


# ---------------------------------------------------------------------------
# Builder: operations phase (end-of-period timing)
# ---------------------------------------------------------------------------


class TestOperationsTimingEndOfPeriod:
    """Operations-phase events with default end-of-period timing."""

    @pytest.fixture
    def project(self):
        return (
            EnergyProject("ops-timing")
            .timeline(
                construction_start=date(2029, 1, 1),
                operations_start=date(2030, 6, 15),
                operations_end=date(2032, 3, 10),
                frequency="year",
            )
            .construction(overnight_cost=1000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
            .annual_opex_cost(1_000_000)
        )

    def test_generation_dates_end_of_period(self, project):
        analysis = project.analyze()
        gen_dates = [g.date for g in analysis.generation.entries]
        # Year-end, capped by operations_end:
        # Two full periods before ops_end (2030-06-15 + 2yr = 2032-06-15 > ops_end)
        assert gen_dates == [
            date(2030, 12, 31),
            date(2031, 12, 31),
        ]

    def test_fixed_opex_dates_end_of_period(self, project):
        analysis = project.analyze()
        opex_stream = analysis.cashflow_components["default:fixed_opex"]
        opex_dates = [cf.date for cf in opex_stream.entries]
        assert opex_dates == [
            date(2030, 12, 31),
            date(2031, 12, 31),
        ]

    def test_revenue_inherits_generation_dates(self, project):
        analysis = project.analyze()
        revenue_stream = analysis.cashflow_components["default:revenue"]
        revenue_dates = [cf.date for cf in revenue_stream.entries]
        assert revenue_dates == [
            date(2030, 12, 31),
            date(2031, 12, 31),
        ]


# ---------------------------------------------------------------------------
# Builder: begin-of-period timing
# ---------------------------------------------------------------------------


class TestBeginOfPeriodTiming:
    """Events placed at the start of the calendar period, floored by phase start."""

    def test_generation_dates_begin_timing(self):
        project = (
            EnergyProject("begin-timing")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 2, 1),
                operations_end=date(2027, 5, 17),
                frequency="year",
                timing="begin",
            )
            .construction(overnight_cost=1000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
        )
        analysis = project.analyze()
        gen_dates = [g.date for g in analysis.generation.entries]
        # begin timing: max(period_start(dt, year), ops_start)
        # dt=2025-02-01: period_start=2025-01-01, max(2025-01-01, 2025-02-01) = 2025-02-01
        # dt=2026-02-01: period_start=2026-01-01, max(2026-01-01, 2025-02-01) = 2026-01-01
        # dt=2027-02-01: period_start=2027-01-01, max(2027-01-01, 2025-02-01) = 2027-01-01
        assert gen_dates == [
            date(2025, 2, 1),
            date(2026, 1, 1),
            date(2027, 1, 1),
        ]

    def test_fixed_opex_dates_begin_timing(self):
        project = (
            EnergyProject("begin-opex")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 3, 15),
                operations_end=date(2027, 12, 31),
                frequency="year",
                timing="begin",
            )
            .construction(overnight_cost=1000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
            .annual_opex_cost(500_000)
        )
        analysis = project.analyze()
        opex_stream = analysis.cashflow_components["default:fixed_opex"]
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

    def test_generation_dates_middle_timing(self):
        project = (
            EnergyProject("middle-timing")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 1, 1),
                operations_end=date(2027, 12, 31),
                frequency="year",
                timing="middle",
            )
            .construction(overnight_cost=1000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
        )
        analysis = project.analyze()
        gen_dates = [g.date for g in analysis.generation.entries]
        # Full calendar years, midpoint = Jul 2 (182 days from Jan 1)
        assert gen_dates[0] == date(2025, 1, 1) + timedelta(days=182)
        assert gen_dates[1] == date(2026, 1, 1) + timedelta(days=182)
        assert gen_dates[2] == date(2027, 1, 1) + timedelta(days=182)

    def test_middle_timing_partial_first_and_last_period(self):
        project = (
            EnergyProject("middle-partial")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 4, 1),
                operations_end=date(2027, 9, 15),
                frequency="year",
                timing="middle",
            )
            .construction(overnight_cost=1000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
        )
        analysis = project.analyze()
        gen_dates = [g.date for g in analysis.generation.entries]
        # Period 1 (dt=2025-04-01): effective Apr 1 – Dec 31 (275 days), mid = 137
        assert gen_dates[0] == date(2025, 4, 1) + timedelta(days=137)
        # Period 2 (dt=2026-04-01): full year Jan 1 – Dec 31 (365 days), mid = 182
        assert gen_dates[1] == date(2026, 1, 1) + timedelta(days=182)
        # Period 3 (dt=2027-04-01): effective Jan 1 – Sep 15 (257 days), mid = 128
        assert gen_dates[2] == date(2027, 1, 1) + timedelta(days=128)


# ---------------------------------------------------------------------------
# Per-component timing override
# ---------------------------------------------------------------------------


class TestPerComponentTimingOverride:
    """Per-component timing overrides the project-level default."""

    def test_generation_begin_with_project_end(self):
        project = (
            EnergyProject("override-test")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 4, 1),
                operations_end=date(2027, 12, 31),
                frequency="year",
                timing="end",
            )
            .construction(overnight_cost=1000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9, timing="begin")
            .market(sell_price_per_unit=50.0)
            .annual_opex_cost(100_000)
        )
        analysis = project.analyze()

        # Generation uses "begin" (override)
        gen_dates = [g.date for g in analysis.generation.entries]
        assert gen_dates == [
            date(2025, 4, 1),   # max(2025-01-01, 2025-04-01) = 2025-04-01
            date(2026, 1, 1),   # max(2026-01-01, 2025-04-01) = 2026-01-01
            date(2027, 1, 1),   # max(2027-01-01, 2025-04-01) = 2027-01-01
        ]

        # OPEX uses "end" (project default)
        opex_stream = analysis.cashflow_components["default:fixed_opex"]
        opex_dates = [cf.date for cf in opex_stream.entries]
        assert opex_dates == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 12, 31),
        ]


# ---------------------------------------------------------------------------
# Depreciation and debt remapping
# ---------------------------------------------------------------------------


class TestDepreciationRemapping:
    """Depreciation dates remapped to calendar year-ends."""

    def test_macrs_dates_remapped_to_year_end(self):
        project = (
            EnergyProject("dep-remap")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 6, 15),
                operations_end=date(2040, 12, 31),
                frequency="year",
            )
            .construction(overnight_cost=1_000_000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
            .macrs_depreciation(5)
        )
        analysis = project.analyze()
        dep_stream = analysis.cashflow_components["default:depreciation"]
        dep_dates = [cf.date for cf in dep_stream.entries]
        # MACRS 5-year generates dates at placed_in_service + i years.
        # With "end" timing, remapped to min(year-end, ops_end):
        # 2025-06-15 → min(2025-12-31, 2040-12-31) = 2025-12-31
        # 2026-06-15 → 2026-12-31
        # ...
        assert dep_dates[0] == date(2025, 12, 31)
        assert dep_dates[1] == date(2026, 12, 31)


class TestDebtRemapping:
    """Debt service dates remapped per timing convention."""

    def test_monthly_debt_dates_remapped_to_month_ends(self):
        project = (
            EnergyProject("debt-remap")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 3, 15),
                operations_end=date(2030, 12, 31),
                frequency="year",
            )
            .construction(overnight_cost=1_000_000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9)
            .market(sell_price_per_unit=50.0)
            .debt(annual_rate=0.05, term=6, frequency="month", principal=500_000)
        )
        analysis = project.analyze()
        debt_stream = analysis.cashflow_components["default:debt_service"]
        # Each payment has interest + principal entries (2 per period).
        unique_dates = sorted(set(cf.date for cf in debt_stream.entries))
        # First payment at 2025-03-15, remapped to min(month-end, ops_end):
        # 2025-03-15 → min(2025-03-31, 2030-12-31) = 2025-03-31
        assert unique_dates[0] == date(2025, 3, 31)
        # Second payment at 2025-04-15 → 2025-04-30
        assert unique_dates[1] == date(2025, 4, 30)


# ---------------------------------------------------------------------------
# No operations_end set (explicit periods, no phase capping)
# ---------------------------------------------------------------------------


class TestNoOperationsEnd:
    """When operations_end is not set, no phase capping occurs."""

    def test_explicit_periods_use_calendar_end_uncapped(self):
        project = (
            EnergyProject("no-ops-end")
            .timeline(
                construction_start=date(2024, 1, 1),
                operations_start=date(2025, 6, 1),
                frequency="year",
            )
            .construction(overnight_cost=1000, spend_profile="upfront", period="year")
            .generation(capacity_mw=100, capacity_factor=0.9, periods=3)
            .market(sell_price_per_unit=50.0)
        )
        analysis = project.analyze()
        gen_dates = [g.date for g in analysis.generation.entries]
        # No phase_end → uncapped calendar year-end
        assert gen_dates == [
            date(2025, 12, 31),
            date(2026, 12, 31),
            date(2027, 12, 31),
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
        # phase_end = 2025-04-20 - 1 day = 2025-04-19
        # 4 periods: (Jan 15-Feb 15), (Feb 15-Mar 15), (Mar 15-Apr 15), (Apr 15-Apr 20)
        assert dates == [
            date(2025, 1, 31),
            date(2025, 2, 28),
            date(2025, 3, 31),
            date(2025, 4, 19),
        ]
