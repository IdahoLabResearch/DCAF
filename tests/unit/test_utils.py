"""Tests for dcaf.core.utils."""

import pytest

from dcaf.core.utils import compound_factor


class TestCompoundFactor:
    """Tests for the compound_factor utility function."""

    def test_zero_rate(self):
        assert compound_factor(0.0, 5.0) == 1.0

    def test_zero_periods(self):
        assert compound_factor(0.10, 0.0) == 1.0

    def test_integer_periods(self):
        assert compound_factor(0.10, 1) == pytest.approx(1.10)
        assert compound_factor(0.10, 2) == pytest.approx(1.21)

    def test_fractional_periods(self):
        assert compound_factor(0.10, 0.5) == pytest.approx(1.10**0.5)

    def test_negative_rate_discounting(self):
        # A negative rate shrinks the factor below 1
        result = compound_factor(-0.05, 1)
        assert result == pytest.approx(0.95)

    def test_identity_for_escalation_and_discount(self):
        # Escalating then discounting by the same factor yields the original amount
        amount = 1000.0
        rate = 0.08
        periods = 3.5
        escalated = amount * compound_factor(rate, periods)
        recovered = escalated / compound_factor(rate, periods)
        assert recovered == pytest.approx(amount)
