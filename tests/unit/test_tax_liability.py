# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for tax liability calculation functions."""

from datetime import date

import pytest

from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.tax.liability import compute_taxable_income, tax_liability
from dcaf.shared.types import ProFormaCategory, TaxTreatment


class TestComputeTaxableIncome:
    """Tests for compute_taxable_income function."""

    def test_basic_computation(self):
        """Test basic taxable income calculation: revenue - deductions."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 15),
                    label="Revenue",
                    is_cash=True,
                    pro_forma_category=ProFormaCategory.REVENUE,
                    tax_treatment=TaxTreatment.TAXABLE,
                )
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 3, 1),
                    label="Expense",
                    is_cash=True,
                    pro_forma_category=ProFormaCategory.OPERATING_COST,
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                )
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 1
        assert result.entries[0].amount == 80_000  # 100,000 - 20,000
        assert result.entries[0].date == date(2025, 12, 31)  # Period end
        assert result.entries[0].is_cash is False  # Accrual concept
        assert result.entries[0].pro_forma_category is None
        assert result.entries[0].tax_treatment is TaxTreatment.NONE

    def test_multiple_periods(self):
        """Test grouping by year when flows span multiple periods."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 1),
                    label="Revenue 2025",
                    tax_treatment=TaxTreatment.TAXABLE,
                ),
                CashFlow(
                    amount=150_000,
                    date=date(2026, 6, 1),
                    label="Revenue 2026",
                    tax_treatment=TaxTreatment.TAXABLE,
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 3, 1),
                    label="Expense 2025",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
                CashFlow(
                    amount=-30_000,
                    date=date(2026, 3, 1),
                    label="Expense 2026",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 2
        # Sort by date for consistent ordering
        result = result.sort(lambda cf: cf.date)

        assert result.entries[0].amount == 80_000  # 2025: 100,000 - 20,000
        assert result.entries[0].date == date(2025, 12, 31)

        assert result.entries[1].amount == 120_000  # 2026: 150,000 - 30,000
        assert result.entries[1].date == date(2026, 12, 31)

    def test_negative_taxable_income_loss(self):
        """Test that negative taxable income (losses) are preserved."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Revenue",
                    tax_treatment=TaxTreatment.TAXABLE,
                )
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-100_000,
                    date=date(2025, 3, 1),
                    label="Large Expense",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                )
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 1
        assert result.entries[0].amount == -50_000  # 50,000 - 100,000 = -50,000 (loss)
        assert result.entries[0].is_cash is False

    def test_empty_streams(self):
        """Test that empty streams return empty result."""
        revenue = CashFlowStream([])
        deductions = CashFlowStream([])

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 0

    def test_only_revenue(self):
        """Test with only revenue, no deductions."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 1),
                    label="Revenue",
                    tax_treatment=TaxTreatment.TAXABLE,
                )
            ]
        )
        deductions = CashFlowStream([])

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 1
        assert result.entries[0].amount == 100_000

    def test_only_deductions(self):
        """Test with only deductions, no revenue."""
        revenue = CashFlowStream([])
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-50_000,
                    date=date(2025, 3, 1),
                    label="Expense",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                )
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 1
        assert result.entries[0].amount == -50_000  # Loss

    def test_multiple_revenue_and_deduction_sources(self):
        """Test combining multiple revenue and deduction sources in same period."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 3, 1),
                    label="Revenue Source 1",
                    tax_treatment=TaxTreatment.TAXABLE,
                ),
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Revenue Source 2",
                    tax_treatment=TaxTreatment.TAXABLE,
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 4, 1),
                    label="OPEX",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
                CashFlow(
                    amount=-10_000,
                    date=date(2025, 8, 1),
                    label="Depreciation",
                    pro_forma_category=ProFormaCategory.DEPRECIATION,
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 1
        assert result.entries[0].amount == 120_000  # 100k + 50k - 20k - 10k

    def test_custom_label(self):
        """Test custom label template."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Revenue",
                    tax_treatment=TaxTreatment.TAXABLE,
                )
            ]
        )
        deductions = CashFlowStream([])

        result = compute_taxable_income(revenue, deductions, label="Custom Taxable Income")

        assert result.entries[0].label == "Custom Taxable Income"

    def test_sums_all_flows_in_streams(self):
        """Test that all flows in both streams are summed (callers are trusted to pass correct streams)."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 1),
                    label="Taxable Revenue",
                    tax_treatment=TaxTreatment.TAXABLE,
                ),
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Other Revenue",
                    pro_forma_category=ProFormaCategory.REVENUE,
                    tax_treatment=TaxTreatment.NONE,
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 3, 1),
                    label="Deductible Expense",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
                CashFlow(
                    amount=-10_000,
                    date=date(2025, 3, 1),
                    label="Other Expense",
                    pro_forma_category=ProFormaCategory.OPERATING_COST,
                    tax_treatment=TaxTreatment.NONE,
                ),
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.entries) == 1
        # All flows in both streams are summed: 100k + 50k + (-20k) + (-10k) = 120k
        assert result.entries[0].amount == 120_000


class TestTaxLiability:
    """Tests for tax_liability function."""

    def test_basic_tax_calculation(self):
        """Test basic tax rate application (21% federal rate)."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        result = tax_liability(taxable_income, tax_rate=0.21)

        assert len(result.entries) == 1
        assert result.entries[0].amount == -21_000  # Negative = outflow
        assert result.entries[0].date == date(2025, 1, 1)
        assert result.entries[0].is_cash is True  # Cash payment
        assert result.entries[0].pro_forma_category is ProFormaCategory.TAX
        assert result.entries[0].tax_treatment is TaxTreatment.NONE

    def test_amounts_are_negative(self):
        """Test that tax amounts are negative (outflows)."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        result = tax_liability(taxable_income, tax_rate=0.21)

        assert result.entries[0].amount < 0

    def test_only_positive_income_generates_tax(self):
        """Test that negative taxable income (losses) don't generate tax liability."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Positive Income",
                    is_cash=False,
                ),
                CashFlow(
                    amount=-50_000,
                    date=date(2026, 1, 1),
                    label="Loss",
                    is_cash=False,
                ),
                CashFlow(
                    amount=75_000,
                    date=date(2027, 1, 1),
                    label="Positive Income",
                    is_cash=False,
                ),
            ]
        )

        result = tax_liability(taxable_income, tax_rate=0.21)

        # Should only have 2 tax flows (2025 and 2027)
        assert len(result.entries) == 2
        result = result.sort(lambda cf: cf.date)

        assert result.entries[0].amount == -21_000  # 2025
        assert result.entries[0].date == date(2025, 1, 1)

        assert result.entries[1].amount == -15_750  # 2027
        assert result.entries[1].date == date(2027, 1, 1)

    def test_zero_tax_rate(self):
        """Test that zero tax rate returns zero amounts."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        result = tax_liability(taxable_income, tax_rate=0.0)

        assert len(result.entries) == 1
        assert result.entries[0].amount == 0.0

    @pytest.mark.parametrize("tax_rate", [float("nan"), float("inf")])
    def test_rejects_non_finite_tax_rate(self, tax_rate: float):
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        with pytest.raises(ValueError, match="tax_rate must be finite"):
            tax_liability(taxable_income, tax_rate=tax_rate)

    def test_rejects_negative_tax_rate(self):
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        with pytest.raises(ValueError, match="tax_rate must be non-negative"):
            tax_liability(taxable_income, tax_rate=-0.21)

    def test_empty_stream(self):
        """Test that empty streams return empty result."""
        taxable_income = CashFlowStream([])

        result = tax_liability(taxable_income, tax_rate=0.21)

        assert len(result.entries) == 0

    def test_custom_label(self):
        """Test custom label template."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        result = tax_liability(taxable_income, tax_rate=0.21, label="Federal Tax")

        assert result.entries[0].label == "Federal Tax"

    def test_custom_classification(self):
        """Test custom classification applied correctly."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        result = tax_liability(
            taxable_income,
            tax_rate=0.21,
            pro_forma_category="other",
            tax_treatment="none",
        )

        assert result.entries[0].pro_forma_category is ProFormaCategory.OTHER
        assert result.entries[0].tax_treatment is TaxTreatment.NONE

    def test_default_classification(self):
        """Test default classification is tax with no tax treatment."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        result = tax_liability(taxable_income, tax_rate=0.21)

        assert result.entries[0].pro_forma_category is ProFormaCategory.TAX
        assert result.entries[0].tax_treatment is TaxTreatment.NONE

    def test_is_cash_true(self):
        """Test that all tax liability flows have is_cash=True."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                ),
                CashFlow(
                    amount=150_000,
                    date=date(2026, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                ),
            ]
        )

        result = tax_liability(taxable_income, tax_rate=0.21)

        assert all(cf.is_cash for cf in result.entries)

    def test_combined_tax_rate(self):
        """Test combined federal + state tax rate."""
        taxable_income = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Taxable Income",
                    is_cash=False,
                )
            ]
        )

        # 21% federal + 5% state = 26% combined
        result = tax_liability(taxable_income, tax_rate=0.26)

        assert result.entries[0].amount == -26_000


class TestIntegration:
    """Integration tests for full workflow."""

    def test_full_workflow_revenue_to_npv(self):
        """Test full workflow: revenue → deductions → taxable income → tax liability → NPV."""
        # Setup revenue
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 1),
                    label="Revenue",
                    is_cash=True,
                    pro_forma_category=ProFormaCategory.REVENUE,
                    tax_treatment=TaxTreatment.TAXABLE,
                )
            ]
        )

        # Setup deductions
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-30_000,
                    date=date(2025, 3, 1),
                    label="OPEX",
                    is_cash=True,
                    pro_forma_category=ProFormaCategory.OPERATING_COST,
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 1, 1),
                    label="Depreciation",
                    is_cash=False,
                    pro_forma_category=ProFormaCategory.DEPRECIATION,
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
            ]
        )

        # Compute taxable income: 100k - 30k - 20k = 50k
        taxable_income = compute_taxable_income(revenue, deductions)
        assert taxable_income.entries[0].amount == 50_000

        # Calculate tax: 50k * 21% = 10.5k
        taxes = tax_liability(taxable_income, tax_rate=0.21)
        assert taxes.entries[0].amount == -10_500

        # Build project cashflows (exclude depreciation since it's not cash)
        project_cashflows = CashFlowStream.from_streams(revenue, deductions, taxes)
        cash_flows = project_cashflows.cash_only()

        # Calculate NPV
        npv = cash_flows.npv(rate=0.10, valuation_date=date(2025, 1, 1))

        # Expected: 100k revenue - 30k opex - 10.5k taxes = 59.5k, discounted
        # Flows occur at different dates in 2025, so there's time value discounting
        # OPEX on 3/1, revenue on 6/1, tax on 12/31
        assert npv > 56_500  # Approximate check
        assert npv < 58_000

    def test_multi_year_with_losses(self):
        """Test multi-year scenario with both profits and losses."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Revenue 2025",
                    tax_treatment=TaxTreatment.TAXABLE,
                ),
                CashFlow(
                    amount=150_000,
                    date=date(2026, 6, 1),
                    label="Revenue 2026",
                    tax_treatment=TaxTreatment.TAXABLE,
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-100_000,
                    date=date(2025, 3, 1),
                    label="High Expense 2025",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
                CashFlow(
                    amount=-50_000,
                    date=date(2026, 3, 1),
                    label="Expense 2026",
                    tax_treatment=TaxTreatment.DEDUCTIBLE,
                ),
            ]
        )

        taxable_income = compute_taxable_income(revenue, deductions)
        # 2025: 50k - 100k = -50k (loss)
        # 2026: 150k - 50k = 100k (profit)

        assert len(taxable_income.entries) == 2
        taxable_income_sorted = taxable_income.sort(lambda cf: cf.date)
        assert taxable_income_sorted.entries[0].amount == -50_000  # 2025 loss
        assert taxable_income_sorted.entries[1].amount == 100_000  # 2026 profit

        taxes = tax_liability(taxable_income, tax_rate=0.21)

        # Should only have tax for 2026
        assert len(taxes.entries) == 1
        assert taxes.entries[0].amount == -21_000
        assert taxes.entries[0].date == date(2026, 12, 31)
