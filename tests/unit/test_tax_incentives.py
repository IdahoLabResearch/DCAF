# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for tax incentive functions."""

from datetime import date

import pytest
from dateutil.relativedelta import relativedelta

from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.tax.depreciation import macrs_schedule
from dcaf.finance.escalation import ConstantRateEscalation
from dcaf.streams.generation import Generation, GenerationStream
from dcaf.tax.incentives import itc, itc_adjusted_basis, ptc
from dcaf.shared.types import ProFormaCategory, TaxTreatment


def _annual_factor(start: date, end: date, rate: float) -> float:
    return (1.0 + rate) ** ((end - start).days / 365.0)


def _capex(amount: float, dt: date, label: str = "CAPEX") -> CashFlow:
    return CashFlow(
        amount=amount,
        date=dt,
        label=label,
        pro_forma_category=ProFormaCategory.CAPITAL_COST,
    )


class TestITC:
    """Tests for itc()."""

    def test_basic_itc(self):
        """Single CAPEX flow, 30% rate → credit = cost × 0.30."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        result = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))

        assert len(result.entries) == 1
        assert result.entries[0].amount == pytest.approx(3_000_000)

    def test_itc_multi_year_capex(self):
        """Construction spanning 3 years → total basis sums correctly."""
        capex = CashFlowStream(
            [
                _capex(-4_000_000, date(2027, 1, 1), "Year 1"),
                _capex(-3_000_000, date(2028, 1, 1), "Year 2"),
                _capex(-3_000_000, date(2029, 1, 1), "Year 3"),
            ]
        )
        result = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))

        assert len(result.entries) == 1
        assert result.entries[0].amount == pytest.approx(3_000_000)  # 10M × 0.30

    def test_itc_empty_stream(self):
        """Empty capex stream → empty result."""
        result = itc(CashFlowStream(), rate=0.30, placed_in_service=date(2030, 1, 1))
        assert len(result.entries) == 0

    def test_itc_zero_rate(self):
        """0% rate → empty result."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        result = itc(capex, rate=0.0, placed_in_service=date(2030, 1, 1))
        assert len(result.entries) == 0

    @pytest.mark.parametrize("rate", [float("nan"), float("inf")])
    def test_itc_rejects_non_finite_rate(self, rate: float):
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])

        with pytest.raises(ValueError, match="ITC rate must be finite"):
            itc(capex, rate=rate, placed_in_service=date(2030, 1, 1))

    def test_itc_rejects_negative_rate(self):
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])

        with pytest.raises(ValueError, match="ITC rate must be non-negative"):
            itc(capex, rate=-0.30, placed_in_service=date(2030, 1, 1))

    def test_itc_placed_in_service_date(self):
        """Credit cashflow date matches placed_in_service."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        placed = date(2030, 3, 15)
        result = itc(capex, rate=0.30, placed_in_service=placed)

        assert result.entries[0].date == placed

    def test_itc_custom_label_and_classification(self):
        """label and classification are forwarded to the resulting cashflow."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        result = itc(
            capex,
            rate=0.30,
            placed_in_service=date(2030, 1, 1),
            label="Section 48E ITC",
            pro_forma_category="other",
            tax_treatment="none",
        )

        assert result.entries[0].label == "Section 48E ITC"
        assert result.entries[0].pro_forma_category is ProFormaCategory.OTHER
        assert result.entries[0].tax_treatment is TaxTreatment.NONE

    def test_itc_is_cash_true(self):
        """ITC credit cashflow must have is_cash=True."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        result = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))

        assert result.entries[0].is_cash is True

    def test_itc_default_classification(self):
        """Default ITC classification is tax credit with no tax treatment."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        result = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))

        assert result.entries[0].pro_forma_category is ProFormaCategory.TAX_CREDIT
        assert result.entries[0].tax_treatment is TaxTreatment.NONE


class TestPTC:
    """Tests for ptc()."""

    def test_basic_ptc(self):
        """PTC applies only within the eligibility window."""
        generation = GenerationStream(
            [
                Generation(1000.0, date(2030, 1, 1)),
                Generation(1000.0, date(2031, 1, 1)),
                Generation(1000.0, date(2032, 1, 1)),
                Generation(1000.0, date(2033, 1, 1)),
            ]
        )
        result = ptc(generation, rate_per_mwh=27.5, years=2)

        assert result.count() == 2
        assert result.entries[0].amount == pytest.approx(27_500.0)
        assert result.entries[0].pro_forma_category is ProFormaCategory.TAX_CREDIT
        assert result.entries[0].tax_treatment is TaxTreatment.NONE

    def test_ptc_point_entries_include_partial_final_calendar_year(self):
        """An exact ten-year window includes January through June of the final year."""
        start = date(2030, 7, 1)
        generation = GenerationStream(
            [Generation(100.0, start + relativedelta(months=index)) for index in range(126)]
        )

        result = ptc(generation, rate_per_mwh=2.0, years=10)

        assert result.count() == 120
        assert result.entries[-1].date == date(2040, 6, 1)
        assert sum(flow.amount for flow in result if flow.date.year == 2030) == pytest.approx(
            1_200.0
        )
        assert sum(flow.amount for flow in result if flow.date.year == 2040) == pytest.approx(
            1_200.0
        )
        assert result.sum() == pytest.approx(24_000.0)

    def test_ptc_prorates_annual_generation_and_reconciles_known_total(self):
        """One MWh per day over ten years receives exactly ten years of PTC."""
        eligibility_start = date(2030, 7, 1)
        eligibility_end = date(2040, 7, 1)
        periods = [
            (eligibility_start, date(2031, 1, 1)),
            *[(date(year, 1, 1), date(year + 1, 1, 1)) for year in range(2031, 2041)],
        ]
        generation = GenerationStream(
            [
                Generation(
                    amount_mwh=float((period_end - period_start).days),
                    period_start=period_start,
                    period_end=period_end,
                )
                for period_start, period_end in periods
            ]
        )

        result = ptc(generation, rate_per_mwh=2.0, years=10)

        known_eligible_mwh = float((eligibility_end - eligibility_start).days)
        assert result.count() == 11
        assert result.entries[0].amount == pytest.approx(184.0 * 2.0)
        assert result.entries[-1].date == date(2040, 6, 30)
        assert result.entries[-1].amount == pytest.approx(182.0 * 2.0)
        assert result.sum() == pytest.approx(known_eligible_mwh * 2.0)

    @pytest.mark.parametrize(
        ("convention", "final_period_mwh", "expected_final_mwh", "expected_total_mwh"),
        [
            ("actual/actual", 366.0, 182.0, 366.0),
            ("actual/365-no-leap", 365.0, 181.0, 365.0),
        ],
    )
    def test_ptc_prorates_annual_generation_using_day_count_convention(
        self,
        convention,
        final_period_mwh,
        expected_final_mwh,
        expected_total_mwh,
    ):
        generation = GenerationStream(
            [
                Generation(
                    184.0,
                    period_start=date(2039, 7, 1),
                    period_end=date(2040, 1, 1),
                ),
                Generation(
                    final_period_mwh,
                    period_start=date(2040, 1, 1),
                    period_end=date(2041, 1, 1),
                ),
            ]
        )

        result = ptc(
            generation,
            rate_per_mwh=1.0,
            years=1,
            day_count_convention=convention,
        )

        assert result.entries[-1].amount == pytest.approx(expected_final_mwh)
        assert result.sum() == pytest.approx(expected_total_mwh)

    def test_ptc_interval_eligibility_is_independent_of_booking_timing(self):
        periods = [
            (date(2039, 7, 1), date(2040, 1, 1)),
            (date(2040, 1, 1), date(2041, 1, 1)),
        ]
        generation = GenerationStream(
            [
                Generation(
                    float((period_end - period_start).days),
                    period_start=period_start,
                    period_end=period_end,
                )
                for period_start, period_end in periods
            ]
        )
        begin_credits = ptc(generation, rate_per_mwh=1.0, years=1, timing="begin")
        end_credits = ptc(generation, rate_per_mwh=1.0, years=1, timing="end")

        assert [flow.amount for flow in begin_credits] == pytest.approx(
            [flow.amount for flow in end_credits]
        )
        assert begin_credits.sum() == pytest.approx(366.0)

    def test_ptc_leap_day_anniversary_is_exclusive_february_28(self):
        generation = GenerationStream(
            [
                Generation(100.0, date(2024, 2, 29)),
                Generation(100.0, date(2025, 2, 27)),
                Generation(100.0, date(2025, 2, 28)),
            ]
        )

        result = ptc(generation, rate_per_mwh=1.0, years=1)

        assert [flow.date for flow in result] == [date(2024, 2, 29), date(2025, 2, 27)]

    def test_ptc_escalation(self):
        """PTC rate escalates."""
        generation = GenerationStream(
            [
                Generation(1000.0, date(2030, 1, 1)),
                Generation(1000.0, date(2031, 1, 1)),
            ]
        )
        result = ptc(generation, rate_per_mwh=10.0, years=5, escalation=0.02)

        assert result.entries[0].amount == pytest.approx(10_000.0)
        assert result.entries[1].amount == pytest.approx(10_200.0)

    def test_ptc_supports_earlier_amount_reference_date(self):
        """PTC rates can be escalated from an earlier known-value date."""
        reference_date = date(2030, 1, 1)
        generation = GenerationStream(
            [
                Generation(1000.0, date(2030, 7, 1)),
                Generation(1000.0, date(2030, 8, 1)),
            ]
        )
        result = ptc(
            generation,
            rate_per_mwh=10.0,
            years=5,
            escalation=0.12,
            amount_reference_date=reference_date,
        )
        expected_dates = [date(2030, 7, 1), date(2030, 8, 1)]
        expected_amounts = [
            1000.0 * 10.0 * _annual_factor(reference_date, flow_date, 0.12)
            for flow_date in expected_dates
        ]

        for i, flow in enumerate(result.entries):
            assert flow.date == expected_dates[i]
            assert flow.amount == pytest.approx(expected_amounts[i])

    def test_ptc_rejects_mixed_simple_and_policy_inputs(self):
        generation = GenerationStream([Generation(1000.0, date(2030, 1, 1))])
        policy = ConstantRateEscalation(reference_date=date(2030, 1, 1), rate=0.02)

        with pytest.raises(ValueError, match="cannot be combined"):
            ptc(
                generation,
                rate_per_mwh=10.0,
                years=5,
                escalation=0.02,
                escalation_policy=policy,
            )

    def test_ptc_empty(self):
        """Empty generation produces empty PTC stream."""
        assert ptc(GenerationStream(), rate_per_mwh=27.5, years=10).count() == 0

    @pytest.mark.parametrize("years", [0, -1])
    def test_ptc_rejects_non_positive_years(self, years: int):
        generation = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

        with pytest.raises(ValueError, match="PTC years must be positive"):
            ptc(generation, rate_per_mwh=27.5, years=years)

    @pytest.mark.parametrize("rate_per_mwh", [float("nan"), float("inf")])
    def test_ptc_rejects_non_finite_rate(self, rate_per_mwh: float):
        generation = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

        with pytest.raises(ValueError, match="PTC rate_per_mwh must be finite"):
            ptc(generation, rate_per_mwh=rate_per_mwh, years=10)

    def test_ptc_rejects_negative_rate(self):
        generation = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

        with pytest.raises(ValueError, match="PTC rate_per_mwh must be non-negative"):
            ptc(generation, rate_per_mwh=-1.0, years=10)

    def test_ptc_allows_zero_rate(self):
        generation = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

        result = ptc(generation, rate_per_mwh=0.0, years=10)

        assert result.count() == 1
        assert result.entries[0].amount == pytest.approx(0.0)


class TestITCAdjustedBasis:
    """Tests for itc_adjusted_basis()."""

    def test_adjusted_basis_30pct(self):
        """$100M capex, 30% rate → basis = $100M × (1 - 0.15) = $85M."""
        capex = CashFlowStream([_capex(-100_000_000, date(2028, 6, 1))])
        assert itc_adjusted_basis(capex, rate=0.30) == pytest.approx(85_000_000)

    def test_adjusted_basis_6pct(self):
        """Base rate case: 6% ITC → basis reduction = 3%."""
        capex = CashFlowStream([_capex(-100_000_000, date(2028, 6, 1))])
        assert itc_adjusted_basis(capex, rate=0.06) == pytest.approx(97_000_000)

    def test_adjusted_basis_zero_rate(self):
        """0% rate → basis unchanged."""
        capex = CashFlowStream([_capex(-100_000_000, date(2028, 6, 1))])
        assert itc_adjusted_basis(capex, rate=0.0) == pytest.approx(100_000_000)

    @pytest.mark.parametrize("rate", [float("nan"), float("inf")])
    def test_adjusted_basis_rejects_non_finite_rate(self, rate: float):
        capex = CashFlowStream([_capex(-100_000_000, date(2028, 6, 1))])

        with pytest.raises(ValueError, match="ITC rate must be finite"):
            itc_adjusted_basis(capex, rate=rate)

    def test_adjusted_basis_rejects_negative_rate(self):
        capex = CashFlowStream([_capex(-100_000_000, date(2028, 6, 1))])

        with pytest.raises(ValueError, match="ITC rate must be non-negative"):
            itc_adjusted_basis(capex, rate=-0.30)

    def test_adjusted_basis_empty_stream(self):
        """Empty capex → 0.0."""
        assert itc_adjusted_basis(CashFlowStream(), rate=0.30) == 0.0

    def test_adjusted_basis_multi_year_capex(self):
        """Multi-year construction: total basis is summed before adjustment."""
        capex = CashFlowStream(
            [
                _capex(-60_000_000, date(2027, 1, 1)),
                _capex(-40_000_000, date(2028, 1, 1)),
            ]
        )
        # 100M × (1 - 0.15) = 85M
        assert itc_adjusted_basis(capex, rate=0.30) == pytest.approx(85_000_000)


class TestIntegration:
    """Integration tests for the full ITC → MACRS workflow."""

    def test_itc_then_macrs(self):
        """Full workflow: CAPEX → itc() → itc_adjusted_basis() → macrs_schedule().

        Depreciation sum should equal adjusted basis (within floating-point tolerance)
        and be less than original CAPEX basis.
        """
        capex = CashFlowStream([_capex(-100_000_000, date(2028, 6, 1))])
        rate = 0.30
        placed = date(2030, 1, 1)

        credit = itc(capex, rate=rate, placed_in_service=placed)
        basis = itc_adjusted_basis(capex, rate=rate)
        depr = macrs_schedule(basis, placed, property_class=15)

        # Credit is positive
        assert credit.entries[0].amount == pytest.approx(30_000_000)

        # Adjusted basis < original basis
        assert basis < 100_000_000
        assert basis == pytest.approx(85_000_000)

        # MACRS rates for 15-year property sum to ~1.0, so depreciation sum ≈ basis
        # Depreciation flows are negative (expense), so take abs
        depr_total = abs(sum(cf.amount for cf in depr.entries))
        assert depr_total == pytest.approx(basis, rel=1e-3)

    def test_itc_npv(self):
        """ITC credit (positive cashflow) raises NPV relative to CAPEX alone."""
        placed = date(2030, 1, 1)
        capex = CashFlowStream(
            [
                CashFlow(
                    amount=-100_000_000,
                    date=date(2028, 6, 1),
                    label="CAPEX",
                    is_cash=True,
                    pro_forma_category=ProFormaCategory.CAPITAL_COST,
                )
            ]
        )
        credit = itc(capex, rate=0.30, placed_in_service=placed)

        project = CashFlowStream.from_streams(capex, credit)
        valuation = date(2028, 1, 1)
        npv_with_itc = project.npv(rate=0.08, valuation_date=valuation)
        npv_capex_only = capex.npv(rate=0.08, valuation_date=valuation)

        # ITC credit should increase NPV
        assert npv_with_itc > npv_capex_only
        # Credit is $30M placed in service 2030; discounted back to 2028 it's
        # worth slightly less than $30M, but still meaningfully positive delta
        assert npv_with_itc - npv_capex_only == pytest.approx(30_000_000 / (1.08**2), rel=0.05)
