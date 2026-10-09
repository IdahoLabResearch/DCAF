# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Property tests for the Generation physical-quantity primitive."""

import warnings
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dcaf.streams import Generation
from strategies import (
    DATES,
    FINITE_AMOUNTS,
    LABELS,
    NON_FINITE,
    intervals,
)

# === Generation dataclass ===

point_generations = st.builds(Generation, amount_mwh=FINITE_AMOUNTS, date=DATES, label=LABELS)
period_generations = intervals().flatmap(
    lambda interval: st.builds(
        Generation,
        amount_mwh=FINITE_AMOUNTS,
        label=LABELS,
        period_start=st.just(interval[0]),
        period_end=st.just(interval[1]),
    )
)
generations = st.one_of(point_generations, period_generations)
GENERATION_FIELDS = ["amount_mwh", "date", "label", "period_start", "period_end"]
DATETIMES = st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2110, 12, 31))


def _fields(generation: Generation) -> dict[str, object]:
    return {name: getattr(generation, name) for name in GENERATION_FIELDS}


@given(FINITE_AMOUNTS, DATES, LABELS)
def test_point_date_normalizes_to_one_day_period(amount, day, label):
    """Generation works at the daily resolution at the most fine-grained. A Generation
    object with a point-date `day` is equivalent a Generation object with the
    half-open interval [day, day + 1)
    """
    g = Generation(amount, day, label=label)

    assert _fields(g) == {
        "amount_mwh": amount,
        "date": day,
        "label": label,
        "period_start": day,
        "period_end": day + timedelta(days=1),
    }


@given(FINITE_AMOUNTS, intervals(), LABELS)
def test_period_bounds_round_trip(amount, interval, label):
    """Generation objects with `period_start`/`period_end` bounds round-trip successfully"""
    start, end = interval
    g = Generation(amount, label=label, period_start=start, period_end=end)

    assert _fields(g) == {
        "amount_mwh": amount,
        "date": None,
        "label": label,
        "period_start": start,
        "period_end": end,
    }


@given(FINITE_AMOUNTS)
def test_requires_date_or_period_bounds(amount):
    """Must provide one of `date` OR (`period_start` AND `period_end`)"""
    with pytest.raises(ValueError, match="date or period_start and period_end must be provided"):
        Generation(amount)


@given(FINITE_AMOUNTS, DATES, st.integers(min_value=0, max_value=1500))
def test_rejects_empty_or_reversed_period(amount, end, days_after_end):
    """`period_end` must come strictly after `period_start`"""
    with pytest.raises(ValueError, match="period_end must be after period_start"):
        Generation(amount, period_start=end + timedelta(days=days_after_end), period_end=end)


@given(FINITE_AMOUNTS, DATES, st.sampled_from(["period_start", "period_end"]))
def test_rejects_one_sided_period_bounds(amount, day, bound):
    """Must provide both `period_start` AND `period_end`"""
    with pytest.raises(ValueError, match="must be provided together"):
        Generation(amount, **{bound: day})


@given(FINITE_AMOUNTS, intervals(), st.sampled_from([("period_start",), ("period_end",), ()]))
def test_rejects_date_with_period_bounds(amount, interval, dropped):
    """Cannot provide both `date` and `period_start`/`period_end`. If `date` is provided,\
    neither `period_start` nor `period_end` may be given.
    """
    bounds = {"period_start": interval[0], "period_end": interval[1]}
    for bound in dropped:
        del bounds[bound]

    with pytest.raises(ValueError, match="date cannot be provided together"):
        Generation(amount, interval[0], **bounds)


@given(generations, st.sampled_from(GENERATION_FIELDS))
def test_is_immutable(g, name):
    """Generation is an immutable object"""
    with pytest.raises(FrozenInstanceError):
        setattr(g, name, getattr(g, name))


@given(generations, st.data())
def test_replace_sets_only_the_named_field_without_warning(g, data):
    """Tests replacing value in Generation, with handling for warnings around `date` and `period_start`/`period_end`"""
    name, value = data.draw(
        st.one_of(
            st.tuples(st.just("amount_mwh"), FINITE_AMOUNTS),
            st.tuples(st.just("label"), LABELS),
        )
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        replaced = g.replace(**{name: value})

    assert _fields(replaced) == {**_fields(g), name: value}


@given(generations, DATES)
def test_replace_date_renormalizes_period_and_warns(g, day):
    """Replacing `date` warns that doing so overwrites values in `period_start`/`period_end`"""
    with pytest.warns(UserWarning, match="overwrites period_start and period_end"):
        replaced = g.replace(date=day)

    assert _fields(replaced) == {
        **_fields(g),
        "date": day,
        "period_start": day,
        "period_end": day + timedelta(days=1),
    }


@given(point_generations, intervals())
def test_replace_bounds_on_point_entry_drops_date_and_warns(g, interval):
    """Replacing `period_start`/`period_end` warns that `date` will be ignored"""
    start, end = interval

    with pytest.warns(UserWarning, match="legacy date to be ignored"):
        replaced = g.replace(period_start=start, period_end=end)

    assert _fields(replaced) == {
        **_fields(g),
        "date": None,
        "period_start": start,
        "period_end": end,
    }


@given(NON_FINITE, DATES)
def test_rejects_non_finite_amount(amount, day):
    """Generation amount must be finite"""
    with pytest.raises(ValueError, match="amount_mwh must be finite"):
        Generation(amount, day)


@given(generations, NON_FINITE)
def test_replace_rejects_non_finite_amount(g, amount):
    """Generation amount must be finite"""
    with pytest.raises(ValueError, match="amount_mwh must be finite"):
        g.replace(amount_mwh=amount)


@given(
    FINITE_AMOUNTS, intervals(), st.sampled_from(["date", "period_start", "period_end"]), st.data()
)
def test_rejects_non_plain_dates(amount, interval, name, data):
    """Generation must use plain datetime.date, not datetime.datetime"""
    kwargs = {} if name == "date" else {"period_start": interval[0], "period_end": interval[1]}
    kwargs[name] = data.draw(st.one_of(DATETIMES, st.text(), st.integers()))

    with pytest.raises(TypeError, match=f"{name} must be a datetime.date"):
        Generation(amount, **kwargs)


@given(generations, st.sampled_from(["date", "period_start", "period_end"]), DATETIMES)
def test_replace_rejects_datetimes(g, name, value):
    """Generation must use plain datetime.date, not datetime.datetime"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(TypeError, match=f"{name} must be a datetime.date"):
            g.replace(**{name: value})
