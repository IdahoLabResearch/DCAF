# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Property tests for the CashFlow booked-record primitive."""

from dataclasses import FrozenInstanceError, fields

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow
from strategies import (
    DATES,
    FINITE_AMOUNTS,
    LABELS,
    NON_FINITE,
    PRO_FORMA_CATEGORIES,
    TAX_TREATMENTS,
    enum_spellings,
)

FIELD_VALUES = {
    "amount": FINITE_AMOUNTS,
    "date": DATES,
    "label": LABELS,
    "is_cash": st.booleans(),
    "pro_forma_category": PRO_FORMA_CATEGORIES,
    "tax_treatment": TAX_TREATMENTS,
}
FIELD_NAMES = [field.name for field in fields(CashFlow)]

cashflows = st.builds(CashFlow, **FIELD_VALUES)

NON_DATES = st.one_of(st.datetimes(), st.text(), st.integers(), st.none())


@given(st.fixed_dictionaries(FIELD_VALUES))
def test_construction_round_trips_fields(values):
    """CashFlow constructor preserves the values provided to it in the instantiated object"""
    cf = CashFlow(**values)

    assert {name: getattr(cf, name) for name in FIELD_NAMES} == values


@given(FINITE_AMOUNTS, DATES)
def test_defaults(amount, day):
    """CashFlow only requires an amount and a datetime.date to be instantiated,
    with all other fields having default values
    """
    cf = CashFlow(amount, day)

    assert cf.label == ""
    assert cf.is_cash is True
    assert cf.pro_forma_category is ProFormaCategory.OTHER
    assert cf.tax_treatment is TaxTreatment.NONE


@given(cashflows, st.sampled_from(FIELD_NAMES), st.data())
def test_is_immutable(cf, name, data):
    """CashFlow objects are immutable"""
    with pytest.raises(FrozenInstanceError):
        setattr(cf, name, data.draw(FIELD_VALUES[name]))


@given(cashflows, st.sampled_from(FIELD_NAMES), st.data())
def test_replace_sets_only_the_named_field(cf, name, data):
    """CashFlow.replace() must produce a new CashFlow object with all values the same except for the replaced valule"""
    value = data.draw(FIELD_VALUES[name])
    before = {other: getattr(cf, other) for other in FIELD_NAMES}

    replaced = cf.replace(**{name: value})

    assert getattr(replaced, name) == value
    assert all(getattr(replaced, other) == before[other] for other in FIELD_NAMES if other != name)
    assert {other: getattr(cf, other) for other in FIELD_NAMES} == before


@given(cashflows)
def test_replace_clears_pro_forma_category_with_none(cf):
    """`None` clears pro forma category"""
    assert cf.replace(pro_forma_category=None).pro_forma_category is None


@given(cashflows)
def test_replace_without_arguments_returns_equal_copy(cf):
    """Calling `CashFlow.replace()` without arguments returns an equal copy."""
    assert cf.replace() == cf


@given(cashflows, st.data())
def test_classification_strings_are_equivalent_to_enums(cf, data):
    """Defining the `CashFlow.pro_forma_category` with a string or an enum value produces
    equivalent `CashFlow` objects"""
    category = cf.pro_forma_category
    category_spelling = None if category is None else data.draw(enum_spellings(category.value))
    treatment_spelling = data.draw(enum_spellings(cf.tax_treatment.value))

    from_strings = CashFlow(
        cf.amount,
        cf.date,
        cf.label,
        cf.is_cash,
        pro_forma_category=category_spelling,
        tax_treatment=treatment_spelling,
    )
    replaced = cf.replace(pro_forma_category=category_spelling, tax_treatment=treatment_spelling)

    assert from_strings == cf
    assert hash(from_strings) == hash(cf)
    assert replaced == cf


@given(NON_FINITE, DATES)
def test_rejects_non_finite_amount(amount, day):
    """CashFlow amounts must be finite at construction. NaN, +inf, and -inf values should all throw errors."""
    with pytest.raises(ValueError, match="amount must be finite"):
        CashFlow(amount, day)


@given(cashflows, NON_FINITE)
def test_replace_rejects_non_finite_amount(cf, amount):
    """CashFlow amounts must be finite at replacement. NaN, +inf, and -inf values should all throw errors."""
    with pytest.raises(ValueError, match="amount must be finite"):
        cf.replace(amount=amount)


@given(FINITE_AMOUNTS, NON_DATES)
def test_rejects_non_plain_date(amount, value):
    """The date used in a CashFlow must be a datetime.date and not a datetime.datetime for construction"""
    with pytest.raises(TypeError, match="date must be a datetime.date"):
        CashFlow(amount, value)


@given(cashflows, st.datetimes())
def test_replace_rejects_datetime(cf, value):
    """The date used in a CashFlow must be a datetime.date and not a datetime.datetime in `replace()`"""
    with pytest.raises(TypeError, match="date must be a datetime.date"):
        cf.replace(date=value)
