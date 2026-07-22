# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for dcaf.metrics standalone functions."""

from datetime import date

import pytest
from hypothesis import example, given, settings, strategies as st

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

        values = [(flow.amount, flow.date) for flow in stream.entries if flow.is_cash]
        standalone = npv(values, rate=0.1, valuation_date=date(2026, 1, 31))

        assert standalone == pytest.approx(stream_npv, abs=1e-8)

    def test_empty_values(self):
        """Empty iterable returns 0.0."""
        assert npv([], rate=0.10, valuation_date=date(2025, 1, 1)) == 0.0

    def test_allows_finite_negative_rate_above_minus_one(self):
        """A finite rate above -1 remains in the real-valued NPV domain."""
        values = [(50.0, date(2026, 1, 1))]

        assert npv(values, rate=-0.5, valuation_date=date(2025, 1, 1)) == pytest.approx(100.0)

    @pytest.mark.parametrize("rate", [-1.0, -1.1])
    def test_rejects_rate_at_or_below_minus_one(self, rate):
        """Rates at or below -1 are singular or complex for fractional periods."""
        values = [(100.0, date(2026, 7, 1))]

        with pytest.raises(ValueError, match="rate must be greater than -1.0"):
            npv(values, rate=rate, valuation_date=date(2026, 1, 1))

    @pytest.mark.parametrize("rate", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_non_finite_rate(self, rate):
        """NPV requires a finite discount rate."""
        values = [(100.0, date(2026, 7, 1))]

        with pytest.raises(ValueError, match="rate must be finite"):
            npv(values, rate=rate, valuation_date=date(2026, 1, 1))

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
        stream = CashFlowStream(
            [
                CashFlow(-1000.0, date(2025, 1, 1)),
                CashFlow(1100.0, date(2026, 1, 1)),
            ]
        )
        assert irr(stream) == pytest.approx(0.1, abs=1e-8)

    def test_multi_cashflow(self):
        """IRR of a 3-cashflow project matches quadratic formula solution."""
        stream = CashFlowStream(
            [
                CashFlow(-10_000.0, date(2025, 1, 1)),
                CashFlow(5_000.0, date(2026, 1, 1)),
                CashFlow(7_000.0, date(2027, 1, 1)),
            ]
        )
        result = irr(stream)
        assert result == pytest.approx(0.12321245982864881, abs=1e-8)

    @given(
        target_rate=st.floats(min_value=-0.75, max_value=1.0, allow_subnormal=False),
        principal=st.floats(min_value=1.0, max_value=1e9, allow_subnormal=False),
        allocation_weights=st.lists(
            st.floats(min_value=0.01, max_value=1.0, allow_subnormal=False),
            min_size=1,
            max_size=8,
        ),
    )
    @example(
        target_rate=0.875,
        principal=1.0,
        allocation_weights=[0.125, 0.375, 0.0625, 0.25, 1.0, 0.75, 0.75],
    )
    @settings(max_examples=10_000)
    def test_recovers_manufactured_unique_root(
        self,
        target_rate: float,
        principal: float,
        allocation_weights: list[float],
    ):
        """IRR recovers an independently manufactured unique root."""
        total_weight = sum(allocation_weights)
        returns = [
            CashFlow(
                principal * weight / total_weight * (1.0 + target_rate) ** year,
                date(2025 + year, 1, 1),
            )
            for year, weight in enumerate(allocation_weights, start=1)
        ]
        stream = CashFlowStream([CashFlow(-principal, date(2025, 1, 1)), *returns])

        # At the target rate, each return's PV is its allocated share of the principal.
        # The single sign change also makes that manufactured root unique.
        assert irr(stream, tol=1e-10) == pytest.approx(target_rate, rel=1e-7, abs=1e-7)

    def test_npv_is_zero_at_irr(self):
        """NPV at the IRR should be approximately zero."""
        stream = CashFlowStream(
            [
                CashFlow(-50_000.0, date(2025, 1, 1)),
                CashFlow(15_000.0, date(2026, 1, 1)),
                CashFlow(20_000.0, date(2027, 1, 1)),
                CashFlow(25_000.0, date(2028, 1, 1)),
            ]
        )
        rate = irr(stream)
        assert stream.npv(rate, date(2025, 1, 1)) == pytest.approx(0.0, abs=1e-6)

    def test_no_inflows_raises(self):
        stream = CashFlowStream(
            [
                CashFlow(-1000.0, date(2025, 1, 1)),
                CashFlow(-500.0, date(2026, 1, 1)),
            ]
        )
        with pytest.raises(ValueError, match="inflow"):
            irr(stream)

    def test_no_outflows_raises(self):
        stream = CashFlowStream(
            [
                CashFlow(1000.0, date(2025, 1, 1)),
                CashFlow(500.0, date(2026, 1, 1)),
            ]
        )
        with pytest.raises(ValueError, match="outflow"):
            irr(stream)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            irr(CashFlowStream([]))

    def test_excludes_non_cash(self):
        """Non-cash flows are excluded; all-inflow cash view raises."""
        stream = CashFlowStream(
            [
                CashFlow(-5_000.0, date(2025, 1, 1), is_cash=False),
                CashFlow(1_000.0, date(2026, 1, 1)),
                CashFlow(1_500.0, date(2027, 1, 1)),
            ]
        )
        with pytest.raises(ValueError):
            irr(stream)

    def test_matches_stream_method(self):
        """Standalone irr matches CashFlowStream.irr."""
        stream = CashFlowStream(
            [
                CashFlow(-10_000.0, date(2025, 1, 1)),
                CashFlow(5_000.0, date(2026, 1, 1)),
                CashFlow(7_000.0, date(2027, 1, 1)),
            ]
        )
        assert irr(stream) == stream.irr()


# ---------------------------------------------------------------------------
# lcoe
# ---------------------------------------------------------------------------


class TestLcoe:
    def test_empty_basis_returns_none(self):
        """Empty basis stream yields None."""
        basis = CashFlowStream([])
        components = CashFlowGroup(
            {
                "cost": CashFlowStream(
                    [
                        CashFlow(
                            -1000.0,
                            date(2025, 1, 1),
                            pro_forma_category=ProFormaCategory.CAPITAL_COST,
                        ),
                    ]
                )
            }
        )
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
        basis = CashFlowStream(
            [
                CashFlow(1.0, date(2025, 1, 1), is_cash=True, tax_treatment=TaxTreatment.TAXABLE),
            ]
        )
        components = CashFlowGroup({})
        result = lcoe(
            basis_stream=basis,
            component_streams=components,
            tax_rate=None,
            discount_rate=0.08,
            valuation_date=date(2025, 1, 1),
        )
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_lcoe_solution_zeros_simple_no_tax_objective(self):
        """The root solver returns a price that zeros a simple no-tax objective."""
        vdate = date(2025, 1, 1)
        rate = 0.08

        cost = CashFlow(
            -10_000.0,
            vdate,
            pro_forma_category=ProFormaCategory.CAPITAL_COST,
            tax_treatment=TaxTreatment.NONE,
        )
        basis_entries = [
            CashFlow(
                100.0,
                date(2025 + i, 7, 1),
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

    def test_lcoe_matches_discounted_cost_over_unit_revenue_basis_without_taxes(self):
        """No-tax LCOE is the hand-derived ratio of discounted costs to revenue basis."""
        valuation_date = date(2025, 1, 1)
        year_1 = date(2026, 1, 1)
        year_2 = date(2027, 1, 1)
        discount_rate = 0.08
        basis = CashFlowStream(
            [
                CashFlow(1_000.0, year_1, tax_treatment=TaxTreatment.TAXABLE),
                CashFlow(1_000.0, year_2, tax_treatment=TaxTreatment.TAXABLE),
            ]
        )
        components = CashFlowGroup(
            {
                "capex": CashFlowStream(
                    [
                        CashFlow(
                            -1_500.0,
                            valuation_date,
                            pro_forma_category=ProFormaCategory.CAPITAL_COST,
                        )
                    ]
                ),
                "opex": CashFlowStream(
                    [
                        CashFlow(
                            -200.0,
                            year_1,
                            pro_forma_category=ProFormaCategory.OPERATING_COST,
                        ),
                        CashFlow(
                            -200.0,
                            year_2,
                            pro_forma_category=ProFormaCategory.OPERATING_COST,
                        ),
                    ]
                ),
            }
        )

        discounted_costs = 1_500.0 + 200.0 / 1.08 + 200.0 / 1.08**2
        discounted_unit_revenue = 1_000.0 / 1.08 + 1_000.0 / 1.08**2
        expected_lcoe = discounted_costs / discounted_unit_revenue

        assert lcoe(
            basis_stream=basis,
            component_streams=components,
            tax_rate=None,
            discount_rate=discount_rate,
            valuation_date=valuation_date,
        ) == pytest.approx(expected_lcoe)

    def test_lcoe_matches_hand_case_with_taxes_credit_and_depreciation_shield(self):
        """Tax-aware LCOE includes after-tax OPEX, credits, and non-cash depreciation."""
        # All cash flows are logged on the same flow_date to remove discounting effects
        flow_date = date(2025, 1, 1)
        basis = CashFlowStream([CashFlow(1_000.0, flow_date, tax_treatment=TaxTreatment.TAXABLE)])
        components = CashFlowGroup(
            {
                "capex": CashFlowStream(
                    [
                        CashFlow(
                            -1_000.0,
                            flow_date,
                            pro_forma_category=ProFormaCategory.CAPITAL_COST,
                        )
                    ]
                ),
                "opex": CashFlowStream(
                    [
                        CashFlow(
                            -100.0,
                            flow_date,
                            pro_forma_category=ProFormaCategory.OPERATING_COST,
                            tax_treatment=TaxTreatment.DEDUCTIBLE,
                        )
                    ]
                ),
                "depreciation": CashFlowStream(
                    [
                        CashFlow(
                            -500.0,
                            flow_date,
                            is_cash=False,
                            pro_forma_category=ProFormaCategory.DEPRECIATION,
                            tax_treatment=TaxTreatment.DEDUCTIBLE,
                        )
                    ]
                ),
                "credit": CashFlowStream(
                    [
                        CashFlow(
                            100.0,
                            flow_date,
                            pro_forma_category=ProFormaCategory.TAX_CREDIT,
                        )
                    ]
                ),
            }
        )

        # At $1.10/MWh and a 20% tax rate, taxable income is $500 and tax is a $100 outflow:
        # -1000 capex - 100 opex + 100 credit + 1100 revenue - 100 tax = 0.
        expected_lcoe = 1.10

        assert lcoe(
            basis_stream=basis,
            component_streams=components,
            tax_rate=0.20,
            discount_rate=0.0,
            valuation_date=flow_date,
        ) == pytest.approx(expected_lcoe)

    def test_lcoe_assumes_symmetric_tax_refunds_for_negative_taxable_income(self):
        """LCOE intentionally assumes ``allow_refund=True`` when taxable income is negative."""
        # All cash flows are logged on the same flow_date to remove discounting effects
        flow_date = date(2025, 1, 1)
        basis = CashFlowStream([CashFlow(1_000.0, flow_date, tax_treatment=TaxTreatment.TAXABLE)])
        components = CashFlowGroup(
            {
                "capex": CashFlowStream(
                    [
                        CashFlow(
                            -1_000.0,
                            flow_date,
                            pro_forma_category=ProFormaCategory.CAPITAL_COST,
                        )
                    ]
                ),
                "depreciation": CashFlowStream(
                    [
                        CashFlow(
                            -850.0,
                            flow_date,
                            is_cash=False,
                            pro_forma_category=ProFormaCategory.DEPRECIATION,
                            tax_treatment=TaxTreatment.DEDUCTIBLE,
                        )
                    ]
                ),
                "credit": CashFlowStream(
                    [
                        CashFlow(
                            300.0,
                            flow_date,
                            pro_forma_category=ProFormaCategory.TAX_CREDIT,
                        )
                    ]
                ),
            }
        )

        # Assumption: the LCOE objective treats tax losses symmetrically as refunds.
        # At $0.6625/MWh, taxable income is revenue-depreciation=-$187.50 and the
        # -187.5*tax_rate=$37.50 refund balances cash:
        # -1000 capex + 300 credit + 662.50 revenue + 37.50 refund = 0.
        expected_lcoe = 0.6625

        assert lcoe(
            basis_stream=basis,
            component_streams=components,
            tax_rate=0.20,
            discount_rate=0.0,
            valuation_date=flow_date,
        ) == pytest.approx(expected_lcoe)

    def test_lcoe_replaces_existing_revenue_and_tax_liability(self):
        """Existing revenue and tax results are replaced before solving LCOE.

        The baseline contains only $1,000 of CAPEX. The comparison adds
        intentionally huge existing market-revenue and project-tax cashflows.
        LCOE must discard both, inject ``price * 1,000`` of taxable revenue from
        the unit-price basis, and recompute tax at 20%. Both component groups
        therefore solve ``-1,000 + (1 - 0.20) * 1,000 * price = 0``, producing
        the same independently derived LCOE of $1.25/MWh.
        """
        flow_date = date(2025, 1, 1)
        basis = CashFlowStream([CashFlow(1_000.0, flow_date, tax_treatment=TaxTreatment.TAXABLE)])
        capex = CashFlowStream(
            [
                CashFlow(
                    -1_000.0,
                    flow_date,
                    pro_forma_category=ProFormaCategory.CAPITAL_COST,
                )
            ]
        )
        baseline = CashFlowGroup({"capex": capex})
        with_existing_results = CashFlowGroup(
            {
                "capex": capex,
                "market_revenue": CashFlowStream(
                    [
                        CashFlow(
                            1_000_000.0,
                            flow_date,
                            pro_forma_category=ProFormaCategory.REVENUE,
                            tax_treatment=TaxTreatment.TAXABLE,
                        )
                    ]
                ),
                "project:tax_liability": CashFlowStream(
                    [
                        CashFlow(
                            -200_000.0,
                            flow_date,
                            pro_forma_category=ProFormaCategory.TAX,
                        )
                    ]
                ),
            }
        )
        expected_lcoe = 1_000.0 / ((1.0 - 0.20) * 1_000.0)

        baseline_lcoe = lcoe(
            basis_stream=basis,
            component_streams=baseline,
            tax_rate=0.20,
            discount_rate=0.0,
            valuation_date=flow_date,
        )
        lcoe_with_existing_results = lcoe(
            basis_stream=basis,
            component_streams=with_existing_results,
            tax_rate=0.20,
            discount_rate=0.0,
            valuation_date=flow_date,
        )

        assert baseline_lcoe == pytest.approx(expected_lcoe)
        assert lcoe_with_existing_results == pytest.approx(expected_lcoe)
