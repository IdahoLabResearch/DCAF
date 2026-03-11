"""Tests for IRA tax incentive functions (ITC)."""

from datetime import date

import pytest

from dcaf.cashflows import CashFlow, CashFlowStream, CashFlowTags
from dcaf.depreciation import macrs_schedule
from dcaf.tax_incentives import itc, itc_adjusted_basis


def _capex(amount: float, dt: date, label: str = "CAPEX") -> CashFlow:
    return CashFlow(amount=amount, date=dt, label=label, tags=frozenset({CashFlowTags.CAPEX}))


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
        capex = CashFlowStream([
            _capex(-4_000_000, date(2027, 1, 1), "Year 1"),
            _capex(-3_000_000, date(2028, 1, 1), "Year 2"),
            _capex(-3_000_000, date(2029, 1, 1), "Year 3"),
        ])
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

    def test_itc_placed_in_service_date(self):
        """Credit cashflow date matches placed_in_service."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        placed = date(2030, 3, 15)
        result = itc(capex, rate=0.30, placed_in_service=placed)

        assert result.entries[0].date == placed

    def test_itc_custom_label_and_tags(self):
        """label and tags are forwarded to the resulting cashflow."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        custom_tags = frozenset({CashFlowTags.REVENUE, CashFlowTags.CAPEX})
        result = itc(
            capex,
            rate=0.30,
            placed_in_service=date(2030, 1, 1),
            label="Section 48E ITC",
            tags=custom_tags,
        )

        assert result.entries[0].label == "Section 48E ITC"
        assert result.entries[0].tags == custom_tags

    def test_itc_is_cash_true(self):
        """ITC credit cashflow must have is_cash=True."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        result = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))

        assert result.entries[0].is_cash is True

    def test_itc_default_tags_revenue(self):
        """Default tags include REVENUE."""
        capex = CashFlowStream([_capex(-10_000_000, date(2028, 6, 1))])
        result = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))

        assert CashFlowTags.REVENUE in result.entries[0].tags


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

    def test_adjusted_basis_empty_stream(self):
        """Empty capex → 0.0."""
        assert itc_adjusted_basis(CashFlowStream(), rate=0.30) == 0.0

    def test_adjusted_basis_multi_year_capex(self):
        """Multi-year construction: total basis is summed before adjustment."""
        capex = CashFlowStream([
            _capex(-60_000_000, date(2027, 1, 1)),
            _capex(-40_000_000, date(2028, 1, 1)),
        ])
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
        capex = CashFlowStream([
            CashFlow(
                amount=-100_000_000,
                date=date(2028, 6, 1),
                label="CAPEX",
                is_cash=True,
                tags=frozenset({CashFlowTags.CAPEX}),
            )
        ])
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
