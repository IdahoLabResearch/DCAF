"""Tests for dcaf.metrics standalone functions."""

from datetime import date

import pytest

from dcaf.metrics import irr, lcoe, npv
from dcaf.streams import CashFlow, CashFlowStream
from dcaf.streams.cashflows import CashFlowGroup
from dcaf.shared.types import ProFormaCategory, TaxTreatment


# ---------------------------------------------------------------------------
# npv
# ---------------------------------------------------------------------------


class TestNpv:
    def test_basic_discounting(self):
        """Discount a single future value at 10%."""
        values = [(1100.0, date(2026, 1, 1))]
        result = npv(values, rate=0.10, valuation_date=date(2025, 1, 1))
        expected = 1100.0 / 1.10  # t = 365/365 = 1.0
        assert result == pytest.approx(expected, abs=1e-8)

    def test_matches_cashflow_stream_npv(self):
        """Standalone npv matches CashFlowStream.npv for a mixed stream."""
        cf1 = CashFlow(-500.0, date(2026, 1, 1))
        cf2 = CashFlow(2000.0, date(2026, 1, 31))
        cf3 = CashFlow(-1000.0, date(2026, 4, 1), is_cash=False)
        cf4 = CashFlow(100.0, date(2026, 6, 30))
        stream = CashFlowStream([cf1, cf2, cf3, cf4])

        stream_npv = stream.npv(0.1, date(2026, 1, 31))

        values = [
            (flow.amount, flow.date)
            for flow in stream.entries
            if flow.is_cash
        ]
        standalone = npv(values, rate=0.1, valuation_date=date(2026, 1, 31))

        assert standalone == pytest.approx(stream_npv, abs=1e-8)

    def test_empty_values(self):
        """Empty iterable returns 0.0."""
        assert npv([], rate=0.10, valuation_date=date(2025, 1, 1)) == 0.0

    def test_compounding_past_values(self):
        """Values before valuation_date are compounded forward."""
        # 2025 is not a leap year: 2024-01-01 → 2025-01-01 = 366 days (2024 is leap)
        # Use non-leap years for exact t = -1.0
        values = [(1000.0, date(2025, 1, 1))]
        result = npv(values, rate=0.10, valuation_date=date(2026, 1, 1))
        # t = -365/365 = -1.0, so PV = 1000 / (1.1)^(-1) = 1000 * 1.1 = 1100
        assert result == pytest.approx(1100.0, abs=1e-8)

    def test_discounted_sum_of_generation(self):
        """npv can compute discounted generation (MWh) sums."""
        from dcaf.streams.generation import GenerationStream, Generation

        entries = [
            Generation(amount_mwh=100.0, date=date(2025, 1, 1)),
            Generation(amount_mwh=100.0, date=date(2026, 1, 1)),
        ]
        stream = GenerationStream(entries)

        # Via GenerationStream.discounted_sum
        ds = stream.discounted_sum(rate=0.08, valuation_date=date(2025, 1, 1))

        # Via standalone npv
        values = [(e.amount_mwh, e.date) for e in entries]
        standalone = npv(values, rate=0.08, valuation_date=date(2025, 1, 1))

        assert standalone == pytest.approx(ds, abs=1e-8)


# ---------------------------------------------------------------------------
# irr
# ---------------------------------------------------------------------------


class TestIrr:
    def test_simple_two_cashflow(self):
        """Invest $1000, receive $1100 after one non-leap year = 10% IRR."""
        stream = CashFlowStream([
            CashFlow(-1000.0, date(2025, 1, 1)),
            CashFlow(1100.0, date(2026, 1, 1)),
        ])
        assert irr(stream) == pytest.approx(0.1, abs=1e-8)

    def test_multi_cashflow(self):
        """IRR of a 3-cashflow project matches quadratic formula solution."""
        stream = CashFlowStream([
            CashFlow(-10_000.0, date(2025, 1, 1)),
            CashFlow(5_000.0, date(2026, 1, 1)),
            CashFlow(7_000.0, date(2027, 1, 1)),
        ])
        result = irr(stream)
        assert result == pytest.approx(0.12321245982864881, abs=1e-8)

    def test_npv_is_zero_at_irr(self):
        """NPV at the IRR should be approximately zero."""
        stream = CashFlowStream([
            CashFlow(-50_000.0, date(2025, 1, 1)),
            CashFlow(15_000.0, date(2026, 1, 1)),
            CashFlow(20_000.0, date(2027, 1, 1)),
            CashFlow(25_000.0, date(2028, 1, 1)),
        ])
        rate = irr(stream)
        assert stream.npv(rate, date(2025, 1, 1)) == pytest.approx(0.0, abs=1e-6)

    def test_no_inflows_raises(self):
        stream = CashFlowStream([
            CashFlow(-1000.0, date(2025, 1, 1)),
            CashFlow(-500.0, date(2026, 1, 1)),
        ])
        with pytest.raises(ValueError, match="inflow"):
            irr(stream)

    def test_no_outflows_raises(self):
        stream = CashFlowStream([
            CashFlow(1000.0, date(2025, 1, 1)),
            CashFlow(500.0, date(2026, 1, 1)),
        ])
        with pytest.raises(ValueError, match="outflow"):
            irr(stream)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            irr(CashFlowStream([]))

    def test_excludes_non_cash(self):
        """Non-cash flows are excluded; all-inflow cash view raises."""
        stream = CashFlowStream([
            CashFlow(-5_000.0, date(2025, 1, 1), is_cash=False),
            CashFlow(1_000.0, date(2026, 1, 1)),
            CashFlow(1_500.0, date(2027, 1, 1)),
        ])
        with pytest.raises(ValueError):
            irr(stream)

    def test_matches_stream_method(self):
        """Standalone irr matches CashFlowStream.irr."""
        stream = CashFlowStream([
            CashFlow(-10_000.0, date(2025, 1, 1)),
            CashFlow(5_000.0, date(2026, 1, 1)),
            CashFlow(7_000.0, date(2027, 1, 1)),
        ])
        assert irr(stream) == stream.irr()


# ---------------------------------------------------------------------------
# lcoe
# ---------------------------------------------------------------------------


class TestLcoe:
    def test_empty_basis_returns_none(self):
        """Empty basis stream yields None."""
        basis = CashFlowStream([])
        components = CashFlowGroup({"cost": CashFlowStream([
            CashFlow(-1000.0, date(2025, 1, 1), pro_forma_category=ProFormaCategory.CAPITAL_COST),
        ])})
        result = lcoe(
            basis_stream=basis,
            component_streams=components,
            tax_rate=None,
            discount_rate=0.08,
            valuation_date=date(2025, 1, 1),
        )
        assert result is None

    def test_zero_cost_project(self):
        """A project with no costs should have LCOE of zero."""
        basis = CashFlowStream([
            CashFlow(1.0, date(2025, 1, 1), is_cash=True,
                     tax_treatment=TaxTreatment.TAXABLE),
        ])
        components = CashFlowGroup({})
        result = lcoe(
            basis_stream=basis,
            component_streams=components,
            tax_rate=None,
            discount_rate=0.08,
            valuation_date=date(2025, 1, 1),
        )
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_lcoe_drives_npv_to_zero(self):
        """The LCOE price applied to the basis stream should yield NPV ~ 0."""
        vdate = date(2025, 1, 1)
        rate = 0.08

        cost = CashFlow(
            -10_000.0, vdate,
            pro_forma_category=ProFormaCategory.CAPITAL_COST,
            tax_treatment=TaxTreatment.NONE,
        )
        basis_entries = [
            CashFlow(
                100.0, date(2025 + i, 7, 1),
                is_cash=True,
                tax_treatment=TaxTreatment.TAXABLE,
            )
            for i in range(10)
        ]
        basis = CashFlowStream(basis_entries)
        components = CashFlowGroup({"capex": CashFlowStream([cost])})

        price = lcoe(
            basis_stream=basis,
            component_streams=components,
            tax_rate=None,
            discount_rate=rate,
            valuation_date=vdate,
        )
        assert price is not None
        assert price > 0.0

        # Verify: NPV of costs + price*basis should be ~ 0
        revenue = basis.scale(price)
        total = CashFlowStream.from_streams(CashFlowStream([cost]), revenue)
        assert total.npv(rate, vdate) == pytest.approx(0.0, abs=1e-3)
