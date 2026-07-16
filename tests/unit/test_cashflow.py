# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from dataclasses import FrozenInstanceError
from datetime import date
import pytest

from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow


@pytest.fixture()
def _create_cashflow():
    cf = CashFlow(
        500.0,
        date(2025, 1, 1),
        label="test label",
        pro_forma_category=ProFormaCategory.REVENUE,
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
    assert cf.pro_forma_category is ProFormaCategory.REVENUE

    with pytest.raises(FrozenInstanceError):
        cf.amount = 600.0


def test_replace(_create_cashflow):
    """Tests the replace method."""
    cf = _create_cashflow
    cf = cf.replace(amount=600.0, is_cash=False)
    assert cf.amount == 600.0
    assert cf.is_cash is False
    assert cf.label == "test label"  # other attributes should be unchanged


def test_initialization_parses_string_classification_inputs():
    cf = CashFlow(
        500.0,
        date(2025, 1, 1),
        label="test label",
        pro_forma_category="Operating Cost",
        tax_treatment="deductible",
    )

    assert cf.pro_forma_category is ProFormaCategory.OPERATING_COST
    assert cf.tax_treatment is TaxTreatment.DEDUCTIBLE
