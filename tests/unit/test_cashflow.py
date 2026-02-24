from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
import pytest

from dcaf import CashFlow, CashFlowTags


def testCashFlow():
    """
    Checks that the CashFlow class can be initialized properly,
    has the correct defaults, and is immutable.
    """
    test_amount = Decimal(500)
    test_date = date(2025, 1, 1)
    cf = CashFlow(
        test_amount,
        date(2025, 1, 1),
        label="test label",
        tags=frozenset({CashFlowTags.REVENUE}),
    )

    assert cf.amount == test_amount
    assert cf.date == test_date
    assert cf.label == "test label"
    assert cf.is_cash is True
    assert cf.has_tag(CashFlowTags.REVENUE) is True
    assert cf.has_tag(CashFlowTags.EXPENSE) is False

    with pytest.raises(FrozenInstanceError):
        cf.amount = Decimal(600)
