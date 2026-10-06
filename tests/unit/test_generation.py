# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for Generation, GenerationStream, and GenerationGroup."""

import math
import warnings
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.shared.time import PeriodTruncationWarning
from dcaf.streams import CashFlowStream, Generation, GenerationGroup, GenerationStream
from dcaf.finance.escalation import ConstantRateEscalation, IndexSeriesEscalation
from strategies import (
    DATES,
    DAY_COUNT_CONVENTIONS,
    FINITE_AMOUNTS,
    FREQUENCIES,
    LABELS,
    NON_FINITE,
    TIMINGS,
    calendar_period_key,
    calendar_windows,
    counted_days,
    intervals,
    timing_point,
)


def _annual_factor(start: date, end: date, rate: float) -> float:
    return (1.0 + rate) ** ((end - start).days / 365.0)


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


# === GenerationStream.from_capacity ===


def test_from_capacity_annual():
    """Annual capacity generation."""
    gs = GenerationStream.from_capacity(
        capacity_mw=100,
        capacity_factor=0.92,
        start=date(2030, 1, 1),
        periods=3,
    )
    assert gs.count() == 3
    expected_mwh = 100 * 0.92 * 8760
    assert abs(gs.entries[0].amount_mwh - expected_mwh) < 1e-6
    assert gs.entries[0].date is None
    assert gs.entries[0].period_start == date(2030, 1, 1)
    assert gs.entries[0].period_end == date(2031, 1, 1)
    assert gs.entries[1].period_start == date(2031, 1, 1)
    assert gs.entries[2].period_end == date(2033, 1, 1)


def test_from_capacity_monthly():
    """Monthly capacity generation."""
    gs = GenerationStream.from_capacity(
        capacity_mw=100,
        capacity_factor=1.0,
        start=date(2030, 1, 1),
        periods=3,
        frequency="month",
    )
    assert gs.count() == 3
    expected_mwh = 100 * 1.0 * 31 * 24
    assert abs(gs.entries[0].amount_mwh - expected_mwh) < 1e-6
    assert gs.entries[1].date is None
    assert gs.entries[1].period_start == date(2030, 2, 1)
    assert gs.entries[1].period_end == date(2030, 3, 1)


def test_from_capacity_quarterly():
    """Quarterly capacity generation."""
    gs = GenerationStream.from_capacity(
        capacity_mw=50,
        capacity_factor=0.80,
        start=date(2030, 1, 1),
        periods=4,
        frequency="quarter",
    )
    assert gs.count() == 4
    expected_mwh = 50 * 0.80 * 90 * 24
    assert abs(gs.entries[0].amount_mwh - expected_mwh) < 1e-6


def test_from_capacity_day_count_convention_controls_leap_year_hours():
    """Capacity generation uses the selected convention for elapsed hours."""
    no_leap = GenerationStream.from_capacity(
        capacity_mw=1.0,
        capacity_factor=1.0,
        start=date(2024, 1, 1),
        periods=1,
        day_count_convention="actual/365-no-leap",
    )
    fixed = GenerationStream.from_capacity(
        capacity_mw=1.0,
        capacity_factor=1.0,
        start=date(2024, 1, 1),
        periods=1,
        day_count_convention="actual/365-fixed",
    )
    actual = GenerationStream.from_capacity(
        capacity_mw=1.0,
        capacity_factor=1.0,
        start=date(2024, 1, 1),
        periods=1,
        day_count_convention="actual/actual",
    )

    assert no_leap.sum() == pytest.approx(8760.0)
    assert fixed.sum() == pytest.approx(8784.0)
    assert actual.sum() == pytest.approx(8784.0)


def test_from_capacity_fractional_period_uses_complete_days_and_warns():
    with pytest.warns(PeriodTruncationWarning, match="last included date is 2030-01-15"):
        stream = GenerationStream.from_capacity(
            capacity_mw=1.0,
            capacity_factor=1.0,
            start=date(2030, 1, 1),
            periods=0.5,
            frequency="month",
        )

    assert stream.count() == 1
    assert stream.entries[0].date is None
    assert stream.entries[0].period_end == date(2030, 1, 16)
    assert stream.entries[0].amount_mwh == pytest.approx(15 * 24)


@pytest.mark.parametrize("capacity_mw", [float("nan"), float("inf")])
def test_from_capacity_rejects_non_finite_capacity(capacity_mw: float):
    with pytest.raises(ValueError, match="capacity_mw must be finite"):
        GenerationStream.from_capacity(
            capacity_mw=capacity_mw,
            capacity_factor=0.5,
            start=date(2030, 1, 1),
            periods=1,
        )


def test_from_capacity_rejects_negative_capacity():
    with pytest.raises(ValueError, match="capacity_mw must be non-negative"):
        GenerationStream.from_capacity(
            capacity_mw=-1.0,
            capacity_factor=0.5,
            start=date(2030, 1, 1),
            periods=1,
        )


@pytest.mark.parametrize("capacity_factor", [float("nan"), float("inf")])
def test_from_capacity_rejects_non_finite_capacity_factor(capacity_factor: float):
    with pytest.raises(ValueError, match="capacity_factor must be finite"):
        GenerationStream.from_capacity(
            capacity_mw=1.0,
            capacity_factor=capacity_factor,
            start=date(2030, 1, 1),
            periods=1,
        )


@pytest.mark.parametrize("capacity_factor", [-0.1, 1.1])
def test_from_capacity_rejects_capacity_factor_outside_unit_interval(capacity_factor: float):
    with pytest.raises(ValueError, match="capacity_factor must be between 0 and 1"):
        GenerationStream.from_capacity(
            capacity_mw=1.0,
            capacity_factor=capacity_factor,
            start=date(2030, 1, 1),
            periods=1,
        )


def test_from_outage_creates_negative_generation():
    """Explicit outage intervals produce normal negative generation entries."""
    outage = GenerationStream.from_outage(
        capacity_mw=1000.0,
        capacity_factor=0.92,
        start=date(2030, 5, 1),
        end=date(2030, 5, 11),
        label="Refueling extension",
    )

    assert outage.count() == 1
    assert outage.entries[0].amount_mwh == pytest.approx(-(1000.0 * 0.92 * 24.0 * 10.0))
    assert outage.entries[0].date is None
    assert outage.entries[0].label == "Refueling extension"
    assert outage.entries[0].period_start == date(2030, 5, 1)
    assert outage.entries[0].period_end == date(2030, 5, 11)


def test_from_outage_supports_partial_reduction():
    """Outage helper supports partial reductions without assigning a booking date."""
    outage = GenerationStream.from_outage(
        capacity_mw=100.0,
        capacity_factor=0.5,
        capacity_reduction=0.25,
        start=date(2030, 1, 1),
        end=date(2030, 1, 5),
    )

    assert outage.entries[0].amount_mwh == pytest.approx(-(100.0 * 0.5 * 0.25 * 24.0 * 4.0))
    assert outage.entries[0].date is None
    assert outage.entries[0].period_start == date(2030, 1, 1)
    assert outage.entries[0].period_end == date(2030, 1, 5)


def test_from_outage_actual_365_excludes_feb_29():
    outage = GenerationStream.from_outage(
        capacity_mw=1.0,
        capacity_factor=1.0,
        start=date(2024, 2, 28),
        end=date(2024, 3, 1),
        day_count_convention="actual/365-no-leap",
    )
    fixed = GenerationStream.from_outage(
        capacity_mw=1.0,
        capacity_factor=1.0,
        start=date(2024, 2, 28),
        end=date(2024, 3, 1),
        day_count_convention="actual/365-fixed",
    )

    assert outage.sum() == pytest.approx(-24.0)
    assert fixed.sum() == pytest.approx(-48.0)


def test_from_outage_rejects_invalid_inputs():
    """Outage helper validates dates and capacity reduction."""
    with pytest.raises(ValueError, match="end must be after"):
        GenerationStream.from_outage(
            capacity_mw=100.0,
            capacity_factor=0.9,
            start=date(2030, 1, 2),
            end=date(2030, 1, 2),
        )

    with pytest.raises(ValueError, match="capacity_reduction"):
        GenerationStream.from_outage(
            capacity_mw=100.0,
            capacity_factor=0.9,
            capacity_reduction=1.1,
            start=date(2030, 1, 1),
            end=date(2030, 1, 2),
        )


# === GenerationStream.from_streams ===


def test_from_streams_preserves_order_and_duplicates():
    """from_streams concatenates mixed input forms without removing duplicates."""
    first = Generation(100.0, date(2030, 1, 1), label="first")
    duplicate = Generation(200.0, date(2031, 1, 1), label="duplicate")
    middle = Generation(300.0, date(2032, 1, 1), label="middle")
    last = Generation(400.0, date(2033, 1, 1), label="last")

    result = GenerationStream.from_streams(
        [first, duplicate],
        GenerationStream([duplicate]),
        GenerationStream([middle]),
        last,
    )

    assert result.entries == [first, duplicate, duplicate, middle, last]


def test_from_streams_rejects_other_stream_types():
    """from_streams rejects stream subclasses from other domains."""
    cashflow_stream = CashFlowStream.from_recurring(date(2030, 1, 1), 1, 100.0)
    with pytest.raises(TypeError, match="Cannot combine GenerationStream with CashFlowStream"):
        GenerationStream.from_streams(cashflow_stream)


# === with_capacity ===


def test_with_capacity_appends():
    """with_capacity appends to existing entries."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2)
    gs2 = gs.with_capacity(50, 0.8, date(2032, 1, 1), 3)
    assert gs2.count() == 5
    # Original unchanged
    assert gs.count() == 2


# === filter methods ===


def test_filter_generic():
    """Generic filter with a custom predicate."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 5)
    recent = gs.filter(lambda g: g.period_start.year >= 2033)
    assert recent.count() == 2


def test_date_range_generation_stream():
    """date_range filters generation entries to the half-open [start, end) interval."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 4)
    result = gs.date_range(start=date(2031, 1, 1), end=date(2033, 1, 1))
    assert result.count() == 2
    assert [entry.period_start for entry in result] == [date(2031, 1, 1), date(2032, 1, 1)]


# === settlement ===
# Settling at a price of 1.0 reads out the settled MWh, so these properties pin how a
# generation quantity is spread over its half-open period and booked, independently of pricing.

SETTLEMENT_CONVENTIONS = st.fixed_dictionaries(
    {"frequency": FREQUENCIES, "timing": TIMINGS, "day_count_convention": DAY_COUNT_CONVENTIONS}
)


def _settle(stream: GenerationStream, conventions: dict[str, str]) -> list[tuple[date, float]]:
    return [(cf.date, cf.amount) for cf in stream.to_revenue(1.0, **conventions)]


def _close(actual: float, expected: float, scale: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9 * abs(scale) + 1e-9)


@given(st.lists(generations, min_size=1, max_size=4), SETTLEMENT_CONVENTIONS)
@example(  # A leap day has no counted days under no-leap; its generation must not vanish.
    [Generation(10.0, date(2028, 2, 29))],
    {"frequency": "month", "timing": "end", "day_count_convention": "actual/365-no-leap"},
)
def test_settlement_conserves_generation(entries, conventions):
    """Total generation amount is conserved no matter the settlement conventions"""
    stream = GenerationStream(entries)

    settled = math.fsum(amount for _, amount in _settle(stream, conventions))

    assert _close(settled, stream.sum(), sum(abs(g.amount_mwh) for g in entries))


@given(period_generations, SETTLEMENT_CONVENTIONS)
def test_settlement_spreads_amount_by_counted_days_and_books_at_timing_point(g, conventions):
    """Each settlement window gets its counted-day share, booked at the window's timing point.

    Windows are the source period clipped to calendar settlement periods, so a source that ends
    mid-period books at its own end (or begin/middle), not at the calendar period's.
    """
    dcc = conventions["day_count_convention"]
    source_days = counted_days(g.period_start, g.period_end, dcc)
    assume(source_days > 0)
    windows = calendar_windows(g.period_start, g.period_end, conventions["frequency"])

    settled = _settle(GenerationStream([g]), conventions)

    assert [day for day, _ in settled] == [
        timing_point(start, end, conventions["timing"]) for start, end in windows
    ]
    for (_, amount), (start, end) in zip(settled, windows, strict=True):
        expected = g.amount_mwh * counted_days(start, end, dcc) / source_days
        assert _close(amount, expected, g.amount_mwh)


@given(st.lists(generations, min_size=1, max_size=4), SETTLEMENT_CONVENTIONS)
def test_sources_settle_independently(entries, conventions):
    """A stream settles as the date-ordered union of its sources settled one at a time."""
    per_source = [flow for g in entries for flow in _settle(GenerationStream([g]), conventions)]

    assert _settle(GenerationStream(entries), conventions) == sorted(
        per_source, key=lambda flow: flow[0]
    )


@given(FINITE_AMOUNTS, intervals(), st.data(), SETTLEMENT_CONVENTIONS)
def test_splitting_a_source_preserves_calendar_period_totals(amount, interval, data, conventions):
    """Splitting a source into adjacent counted-day shares leaves each calendar period's total
    unchanged. Booking dates may differ: each part books within its own period."""
    start, end = interval
    assume((end - start).days > 1)
    dcc = conventions["day_count_convention"]
    assume(counted_days(start, end, dcc) > 0)
    cut = start + timedelta(days=data.draw(st.integers(1, (end - start).days - 1)))
    first = amount * counted_days(start, cut, dcc) / counted_days(start, end, dcc)
    whole = GenerationStream([Generation(amount, period_start=start, period_end=end)])
    split = GenerationStream(
        [
            Generation(first, period_start=start, period_end=cut),
            Generation(amount - first, period_start=cut, period_end=end),
        ]
    )

    def period_totals(stream: GenerationStream) -> dict[date, float]:
        totals: dict[date, float] = {}
        for day, settled in _settle(stream, conventions):
            key = calendar_period_key(day, conventions["frequency"])
            totals[key] = totals.get(key, 0.0) + settled
        return totals

    whole_totals, split_totals = period_totals(whole), period_totals(split)
    assert whole_totals.keys() == split_totals.keys()
    for key, total in whole_totals.items():
        assert _close(split_totals[key], total, amount)


@given(
    generations,
    st.floats(min_value=-100.0, max_value=100.0),
    st.floats(min_value=0.0, max_value=1e4),
    SETTLEMENT_CONVENTIONS,
)
def test_revenue_is_bilinear_in_amount_and_price(g, factor, price, conventions):
    """Revenue scales linearly with both amount and price"""
    settled = _settle(GenerationStream([g]), conventions)

    revenue = GenerationStream([g.replace(amount_mwh=g.amount_mwh * factor)]).to_revenue(
        price, **conventions
    )

    assert [cf.date for cf in revenue] == [day for day, _ in settled]
    for cf, (_, amount) in zip(revenue, settled, strict=True):
        assert _close(cf.amount, amount * factor * price, g.amount_mwh * factor * price)


@given(FINITE_AMOUNTS, DATES, SETTLEMENT_CONVENTIONS)
def test_point_entry_settles_like_one_day_period(amount, day, conventions):
    """Generation at a single date and over a one-day period are equivalent and must settle equivalently"""
    point = GenerationStream([Generation(amount, day)])
    one_day = GenerationStream(
        [Generation(amount, period_start=day, period_end=day + timedelta(days=1))]
    )

    assert _settle(point, conventions) == _settle(one_day, conventions)


# === grouping ===


def test_group_by_predicate():
    """Group by a predicate function returns correct groups."""
    gs = GenerationStream(
        [
            Generation(100.0, date(2030, 1, 1), label="a"),
            Generation(150.0, date(2031, 1, 1), label="a"),
            Generation(50.0, date(2030, 1, 1), label="b"),
            Generation(75.0, date(2031, 1, 1), label="b"),
            Generation(25.0, date(2032, 1, 1), label="b"),
        ]
    )
    groups = gs.group_by(lambda g: g.label)
    assert isinstance(groups, GenerationGroup)
    assert len(groups) == 2
    assert groups["a"].count() == 2
    assert groups["b"].count() == 3


def test_group_by_period():
    """Group by year period using group_by(period=...)."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
    groups = gs.group_by(period="year")
    assert len(groups) == 3


def test_sort_generation_stream_default():
    """sort() defaults to physical period order and respects descending."""
    gs = GenerationStream(
        [
            Generation(100.0, date(2032, 1, 1)),
            Generation(100.0, date(2030, 1, 1)),
            Generation(100.0, date(2031, 1, 1)),
        ]
    )
    result = gs.sort()
    assert [entry.date for entry in result] == [
        date(2030, 1, 1),
        date(2031, 1, 1),
        date(2032, 1, 1),
    ]
    assert [entry.date for entry in gs.sort(descending=True)] == [
        date(2032, 1, 1),
        date(2031, 1, 1),
        date(2030, 1, 1),
    ]


def test_sort_generation_stream_date_attr_uses_complete_period_bounds():
    gs = GenerationStream(
        [
            Generation(
                200.0,
                label="long",
                period_start=date(2030, 1, 1),
                period_end=date(2030, 3, 1),
            ),
            Generation(
                100.0,
                label="short",
                period_start=date(2030, 1, 1),
                period_end=date(2030, 2, 1),
            ),
            Generation(50.0, date(2030, 4, 1), label="dated"),
        ]
    )

    assert [entry.label for entry in gs.sort()] == ["short", "long", "dated"]
    assert [entry.label for entry in gs.sort(attr="date")] == ["short", "long", "dated"]


def test_sort_generation_stream_by_attr():
    """sort(attr=...) sorts by a named Generation attribute."""
    gs = GenerationStream(
        [
            Generation(300.0, date(2030, 1, 1)),
            Generation(100.0, date(2031, 1, 1)),
            Generation(200.0, date(2032, 1, 1)),
        ]
    )
    result = gs.sort(attr="amount_mwh", descending=True)
    assert [entry.amount_mwh for entry in result] == [300.0, 200.0, 100.0]


def test_scale():
    """Scales all generation amounts."""
    gs = GenerationStream([Generation(200, date(2026, 1, 1)), Generation(300, date(2027, 1, 1))])
    entries = gs.entries
    scaled_gs = gs.scale(0.8)
    assert abs(scaled_gs.entries[0].amount_mwh - 160) < 1e-8
    assert abs(scaled_gs.entries[1].amount_mwh - 240) < 1e-8

    # Check that the original stream was not modified
    assert entries == gs.entries


# === aggregation ===


def test_sum():
    """Sum of MWh across all entries."""
    gs = GenerationStream.from_capacity(100, 0.92, date(2030, 1, 1), 2)
    total = gs.sum()
    expected = 2 * 100 * 0.92 * 8760
    assert abs(total - expected) < 1e-6


def test_sum_empty():
    """Sum of empty stream is 0."""
    assert GenerationStream().sum() == 0.0


# === discounted_sum ===


def test_discounted_sum():
    """Discounted sum applies discount factors."""
    gs = GenerationStream(
        [
            Generation(1000.0, date(2030, 1, 1)),
            Generation(1000.0, date(2031, 1, 1)),
        ]
    )
    ds = gs.discounted_sum(rate=0.10, valuation_date=date(2030, 1, 1))
    # First entry: 1000 / (1.1)^0 = 1000
    # Second entry: 1000 / (1.1)^1 ≈ 909.09
    assert ds < 2000.0
    assert ds > 1900.0


def test_discounted_sum_zero_rate():
    """At zero rate, discounted sum equals plain sum."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
    assert abs(gs.discounted_sum(0.0, date(2030, 1, 1)) - gs.sum()) < 1e-6


def test_discounted_sum_rejects_non_finite_rate():
    """The generation wrapper enforces the shared finite-rate requirement."""
    gs = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

    with pytest.raises(ValueError, match="rate must be finite"):
        gs.discounted_sum(float("inf"), date(2030, 1, 1))


def test_discounted_sum_uses_constant_rate_escalation_for_discounting():
    """Discounted sum matches evaluation through the shared constant-rate policy."""
    valuation_date = date(2030, 1, 1)
    gs = GenerationStream(
        [
            Generation(1000.0, date(2029, 1, 1)),
            Generation(1000.0, date(2030, 1, 1)),
            Generation(1000.0, date(2031, 1, 1)),
        ]
    )
    policy = ConstantRateEscalation(
        valuation_date,
        rate=0.10,
        day_count_convention="actual/365-no-leap",
    )

    expected = sum(entry.amount_mwh / policy.factor(entry.date) for entry in gs.entries)

    assert gs.discounted_sum(rate=0.10, valuation_date=valuation_date) == pytest.approx(expected)


# === to_revenue ===


def test_to_revenue_basic():
    """Convert generation to revenue cashflows."""
    gs = GenerationStream(
        [
            Generation(1000.0, date(2030, 1, 1)),
            Generation(1000.0, date(2031, 1, 1)),
        ]
    )
    cfs = gs.to_revenue(price_per_mwh=50.0)
    assert cfs.count() == 2
    assert abs(cfs.entries[0].amount - 50_000.0) < 1e-8
    assert cfs.entries[0].is_cash is True
    assert cfs.entries[0].pro_forma_category is ProFormaCategory.REVENUE
    assert cfs.entries[0].tax_treatment is TaxTreatment.TAXABLE


def test_to_revenue_escalation():
    """Revenue price escalates annually."""
    gs = GenerationStream(
        [
            Generation(1000.0, date(2030, 1, 1)),
            Generation(1000.0, date(2031, 1, 1)),
            Generation(1000.0, date(2032, 1, 1)),
        ]
    )
    cfs = gs.to_revenue(price_per_mwh=50.0, escalation=0.10)
    assert abs(cfs.entries[0].amount - 50_000.0) < 1e-8
    assert abs(cfs.entries[1].amount - 55_000.0) < 1e-8
    assert abs(cfs.entries[2].amount - 60_500.0) < 1e-6


def test_to_revenue_escalation_uses_entry_dates():
    """Revenue escalation uses exact entry dates rather than integer year steps."""
    reference_date = date(2030, 6, 1)
    gs = GenerationStream(
        [
            Generation(1000.0, reference_date),
            Generation(1000.0, date(2031, 1, 1)),
            Generation(1000.0, date(2031, 6, 1)),
        ]
    )
    cfs = gs.to_revenue(price_per_mwh=50.0, escalation=0.10)
    expected_dates = [reference_date, date(2031, 1, 1), date(2031, 6, 1)]
    expected_amounts = [
        1000.0 * 50.0 * _annual_factor(reference_date, flow_date, 0.10)
        for flow_date in expected_dates
    ]
    for i, flow in enumerate(cfs.entries):
        assert flow.date == expected_dates[i]
        assert flow.amount == pytest.approx(expected_amounts[i])


def test_to_revenue_supports_escalation_policy_parity_with_constant_rate():
    gs = GenerationStream(
        [
            Generation(1000.0, date(2030, 7, 1)),
            Generation(1000.0, date(2031, 7, 1)),
        ]
    )
    simple = gs.to_revenue(
        price_per_mwh=50.0,
        escalation=0.10,
        amount_reference_date=date(2030, 1, 1),
    )
    advanced = gs.to_revenue(
        price_per_mwh=50.0,
        escalation_policy=ConstantRateEscalation(date(2030, 1, 1), rate=0.10),
    )

    assert [flow.amount for flow in advanced.entries] == pytest.approx(
        [flow.amount for flow in simple.entries]
    )


def test_to_revenue_empty():
    """Empty generation produces empty cashflow stream."""
    cfs = GenerationStream().to_revenue(price_per_mwh=50.0)
    assert cfs.count() == 0


# === to_cost ===


def test_to_cost_basic():
    """Convert generation to cost cashflows (negative)."""
    gs = GenerationStream([Generation(1000.0, date(2030, 1, 1))])
    cfs = gs.to_cost(rate_per_mwh=5.0)
    assert cfs.count() == 1
    assert abs(cfs.entries[0].amount - (-5_000.0)) < 1e-8
    assert cfs.entries[0].pro_forma_category is ProFormaCategory.OPERATING_COST


@pytest.mark.parametrize("rate_per_mwh", [float("nan"), float("inf")])
def test_to_cost_rejects_non_finite_rate(rate_per_mwh: float):
    gs = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

    with pytest.raises(ValueError, match="rate_per_mwh must be finite"):
        gs.to_cost(rate_per_mwh=rate_per_mwh)


def test_to_cost_rejects_negative_rate():
    gs = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

    with pytest.raises(ValueError, match="rate_per_mwh must be non-negative"):
        gs.to_cost(rate_per_mwh=-5.0)


def test_to_cost_allows_zero_rate():
    gs = GenerationStream([Generation(1000.0, date(2030, 1, 1))])

    cfs = gs.to_cost(rate_per_mwh=0.0)

    assert cfs.count() == 1
    assert cfs.entries[0].amount == pytest.approx(0.0)


def test_to_cost_escalation():
    """Cost rate escalates annually."""
    gs = GenerationStream(
        [
            Generation(1000.0, date(2030, 1, 1)),
            Generation(1000.0, date(2031, 1, 1)),
        ]
    )
    cfs = gs.to_cost(rate_per_mwh=10.0, escalation=0.05)
    assert abs(cfs.entries[0].amount - (-10_000.0)) < 1e-8
    assert abs(cfs.entries[1].amount - (-10_500.0)) < 1e-6


def test_to_cost_supports_explicit_nonannual_escalation_period():
    """Cost escalation period can be specified independently of entry cadence."""
    gs = GenerationStream(
        [
            Generation(1000.0, date(2030, 1, 1)),
            Generation(1000.0, date(2030, 2, 1)),
            Generation(1000.0, date(2030, 3, 1)),
        ]
    )
    cfs = gs.to_cost(rate_per_mwh=10.0, escalation=0.02, escalation_period="month")
    expected_amounts = [-10_000.0, -10_200.0, -10_404.0]
    for i, flow in enumerate(cfs.entries):
        assert flow.amount == pytest.approx(expected_amounts[i])


def test_to_cost_supports_index_series_escalation_policy():
    gs = GenerationStream(
        [
            Generation(1000.0, date(2030, 1, 15)),
            Generation(1000.0, date(2030, 2, 15)),
            Generation(1000.0, date(2030, 3, 15)),
        ]
    )
    policy = IndexSeriesEscalation(
        reference_date=date(2030, 1, 1),
        points=(
            (date(2030, 1, 1), 100.0),
            (date(2030, 2, 1), 103.0),
            (date(2030, 3, 1), 106.09),
        ),
    )
    cfs = gs.to_cost(rate_per_mwh=10.0, escalation_policy=policy)

    assert [flow.amount for flow in cfs.entries] == pytest.approx([-10_000.0, -10_300.0, -10_609.0])


# === GenerationGroup ===


def _two_group_stream() -> GenerationStream:
    """Build a two-group stream keyed by ``label`` for GenerationGroup tests."""
    return GenerationStream(
        [
            Generation(100.0 * 0.9 * 8760, date(2030, 1, 1), label="a"),
            Generation(100.0 * 0.9 * 8760, date(2031, 1, 1), label="a"),
            Generation(50.0 * 0.8 * 8760, date(2030, 1, 1), label="b"),
        ]
    )


def test_generation_group_sum():
    """Sum convenience method on GenerationGroup."""
    gs = _two_group_stream()
    groups = gs.group_by(lambda g: g.label)
    sums = groups.sum()
    assert abs(sums["a"] - 2 * 100 * 0.9 * 8760) < 1e-6
