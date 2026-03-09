from dataclasses import FrozenInstanceError
from datetime import date
import pytest

from dcaf import CashFlow, CashFlowTags


@pytest.fixture()
def _create_cashflow():
    cf = CashFlow(
        500.0,
        date(2025, 1, 1),
        label="test label",
        tags=frozenset({CashFlowTags.REVENUE}),
    )
    return cf


def test_initialization(_create_cashflow):
    """
    Checks that the CashFlow class can be initialized properly,
    has the correct defaults, and is immutable.
    """
    cf = _create_cashflow

    assert cf.amount == 500.0
    assert cf.date == date(2025, 1, 1)
    assert cf.label == "test label"
    assert cf.is_cash is True
    assert cf.has_tag(CashFlowTags.REVENUE) is True
    assert cf.has_tag(CashFlowTags.EXPENSE) is False

    with pytest.raises(FrozenInstanceError):
        cf.amount = 600.0


def test_replace(_create_cashflow):
    """Tests the replace method."""
    cf = _create_cashflow
    cf = cf.replace(amount=600.0, is_cash=False)
    assert cf.amount == 600.0
    assert cf.is_cash is False
    assert cf.label == "test label"  # other attributes should be unchanged
