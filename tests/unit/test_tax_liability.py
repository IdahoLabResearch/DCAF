"""Tests for tax liability calculation functions."""

from datetime import date

import pytest

from dcaf.cashflows import CashFlow, CashFlowStream, CashFlowTags
from dcaf.tax_liability import compute_taxable_income, tax_liability


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
                    tags=frozenset({CashFlowTags.REVENUE, CashFlowTags.TAXABLE}),
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
                    tags=frozenset({CashFlowTags.EXPENSE, CashFlowTags.TAX_DEDUCTIBLE}),
                )
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 1
        assert result.flows[0].amount == 80_000  # 100,000 - 20,000
        assert result.flows[0].date == date(2025, 1, 1)  # Period start
        assert result.flows[0].is_cash is False  # Accrual concept
        assert result.flows[0].tags == frozenset()  # No tags

    def test_multiple_periods(self):
        """Test grouping by year when flows span multiple periods."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 1),
                    label="Revenue 2025",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                ),
                CashFlow(
                    amount=150_000,
                    date=date(2026, 6, 1),
                    label="Revenue 2026",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 3, 1),
                    label="Expense 2025",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                ),
                CashFlow(
                    amount=-30_000,
                    date=date(2026, 3, 1),
                    label="Expense 2026",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                ),
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 2
        # Sort by date for consistent ordering
        result = result.sort(lambda cf: cf.date)

        assert result.flows[0].amount == 80_000  # 2025: 100,000 - 20,000
        assert result.flows[0].date == date(2025, 1, 1)

        assert result.flows[1].amount == 120_000  # 2026: 150,000 - 30,000
        assert result.flows[1].date == date(2026, 1, 1)

    def test_negative_taxable_income_loss(self):
        """Test that negative taxable income (losses) are preserved."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Revenue",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                )
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-100_000,
                    date=date(2025, 3, 1),
                    label="Large Expense",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                )
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 1
        assert result.flows[0].amount == -50_000  # 50,000 - 100,000 = -50,000 (loss)
        assert result.flows[0].is_cash is False

    def test_empty_streams(self):
        """Test that empty streams return empty result."""
        revenue = CashFlowStream([])
        deductions = CashFlowStream([])

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 0

    def test_only_revenue(self):
        """Test with only revenue, no deductions."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 1),
                    label="Revenue",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                )
            ]
        )
        deductions = CashFlowStream([])

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 1
        assert result.flows[0].amount == 100_000

    def test_only_deductions(self):
        """Test with only deductions, no revenue."""
        revenue = CashFlowStream([])
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-50_000,
                    date=date(2025, 3, 1),
                    label="Expense",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                )
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 1
        assert result.flows[0].amount == -50_000  # Loss

    def test_multiple_revenue_and_deduction_sources(self):
        """Test combining multiple revenue and deduction sources in same period."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 3, 1),
                    label="Revenue Source 1",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                ),
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Revenue Source 2",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 4, 1),
                    label="OPEX",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                ),
                CashFlow(
                    amount=-10_000,
                    date=date(2025, 8, 1),
                    label="Depreciation",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE, CashFlowTags.DEPRECIATION}),
                ),
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 1
        assert result.flows[0].amount == 120_000  # 100k + 50k - 20k - 10k

    def test_custom_label(self):
        """Test custom label template."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 1, 1),
                    label="Revenue",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                )
            ]
        )
        deductions = CashFlowStream([])

        result = compute_taxable_income(revenue, deductions, label="Custom Taxable Income")

        assert result.flows[0].label == "Custom Taxable Income"

    def test_ignores_non_taxable_flows(self):
        """Test that flows without TAXABLE or TAX_DEDUCTIBLE tags are ignored."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=100_000,
                    date=date(2025, 6, 1),
                    label="Taxable Revenue",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                ),
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Non-taxable Revenue",
                    tags=frozenset({CashFlowTags.REVENUE}),  # No TAXABLE tag
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 3, 1),
                    label="Deductible Expense",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                ),
                CashFlow(
                    amount=-10_000,
                    date=date(2025, 3, 1),
                    label="Non-deductible Expense",
                    tags=frozenset({CashFlowTags.EXPENSE}),  # No TAX_DEDUCTIBLE tag
                ),
            ]
        )

        result = compute_taxable_income(revenue, deductions)

        assert len(result.flows) == 1
        # Should only count 100k taxable revenue and -20k deductible expense
        assert result.flows[0].amount == 80_000


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

        assert len(result.flows) == 1
        assert result.flows[0].amount == -21_000  # Negative = outflow
        assert result.flows[0].date == date(2025, 1, 1)
        assert result.flows[0].is_cash is True  # Cash payment
        assert CashFlowTags.EXPENSE in result.flows[0].tags

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

        assert result.flows[0].amount < 0

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
        assert len(result.flows) == 2
        result = result.sort(lambda cf: cf.date)

        assert result.flows[0].amount == -21_000  # 2025
        assert result.flows[0].date == date(2025, 1, 1)

        assert result.flows[1].amount == -15_750  # 2027
        assert result.flows[1].date == date(2027, 1, 1)

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

        assert len(result.flows) == 1
        assert result.flows[0].amount == 0.0

    def test_empty_stream(self):
        """Test that empty streams return empty result."""
        taxable_income = CashFlowStream([])

        result = tax_liability(taxable_income, tax_rate=0.21)

        assert len(result.flows) == 0

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

        assert result.flows[0].label == "Federal Tax"

    def test_custom_tags(self):
        """Test custom tags applied correctly."""
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

        custom_tags = frozenset({CashFlowTags.EXPENSE, CashFlowTags.OPEX})
        result = tax_liability(taxable_income, tax_rate=0.21, tags=custom_tags)

        assert result.flows[0].tags == custom_tags

    def test_default_tags(self):
        """Test default tags are EXPENSE."""
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

        assert result.flows[0].tags == frozenset({CashFlowTags.EXPENSE})

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

        assert all(cf.is_cash for cf in result.flows)

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

        assert result.flows[0].amount == -26_000


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
                    tags=frozenset({CashFlowTags.REVENUE, CashFlowTags.TAXABLE}),
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
                    tags=frozenset({CashFlowTags.EXPENSE, CashFlowTags.TAX_DEDUCTIBLE}),
                ),
                CashFlow(
                    amount=-20_000,
                    date=date(2025, 1, 1),
                    label="Depreciation",
                    is_cash=False,
                    tags=frozenset(
                        {
                            CashFlowTags.DEPRECIATION,
                            CashFlowTags.TAX_DEDUCTIBLE,
                        }
                    ),
                ),
            ]
        )

        # Compute taxable income: 100k - 30k - 20k = 50k
        taxable_income = compute_taxable_income(revenue, deductions)
        assert taxable_income.flows[0].amount == 50_000

        # Calculate tax: 50k * 21% = 10.5k
        taxes = tax_liability(taxable_income, tax_rate=0.21)
        assert taxes.flows[0].amount == -10_500

        # Build project cashflows (exclude depreciation since it's not cash)
        project_cashflows = CashFlowStream.from_streams(revenue, deductions, taxes)
        cash_flows = project_cashflows.cash_only()

        # Calculate NPV
        npv = cash_flows.npv(rate=0.10, valuation_date=date(2025, 1, 1))

        # Expected: 100k revenue - 30k opex - 10.5k taxes = 59.5k, discounted
        # Flows occur at different dates in 2025, so there's time value discounting
        # OPEX on 3/1, revenue on 6/1, tax on 1/1
        assert npv > 55_000  # Approximate check
        assert npv < 57_000

    def test_multi_year_with_losses(self):
        """Test multi-year scenario with both profits and losses."""
        revenue = CashFlowStream(
            [
                CashFlow(
                    amount=50_000,
                    date=date(2025, 6, 1),
                    label="Revenue 2025",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                ),
                CashFlow(
                    amount=150_000,
                    date=date(2026, 6, 1),
                    label="Revenue 2026",
                    tags=frozenset({CashFlowTags.TAXABLE}),
                ),
            ]
        )
        deductions = CashFlowStream(
            [
                CashFlow(
                    amount=-100_000,
                    date=date(2025, 3, 1),
                    label="High Expense 2025",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                ),
                CashFlow(
                    amount=-50_000,
                    date=date(2026, 3, 1),
                    label="Expense 2026",
                    tags=frozenset({CashFlowTags.TAX_DEDUCTIBLE}),
                ),
            ]
        )

        taxable_income = compute_taxable_income(revenue, deductions)
        # 2025: 50k - 100k = -50k (loss)
        # 2026: 150k - 50k = 100k (profit)

        assert len(taxable_income.flows) == 2
        taxable_income_sorted = taxable_income.sort(lambda cf: cf.date)
        assert taxable_income_sorted.flows[0].amount == -50_000  # 2025 loss
        assert taxable_income_sorted.flows[1].amount == 100_000  # 2026 profit

        taxes = tax_liability(taxable_income, tax_rate=0.21)

        # Should only have tax for 2026
        assert len(taxes.flows) == 1
        assert taxes.flows[0].amount == -21_000
        assert taxes.flows[0].date == date(2026, 1, 1)
