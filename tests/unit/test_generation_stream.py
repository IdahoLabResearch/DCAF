# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Property tests for the GenerationStream- and GenerationGroup-specific behavior.

Collection mechanics inherited from BaseStream and BaseGroup are covered by
``test_stream_base.py``; these properties cover what GenerationStream adds: settlement across
calendar periods, construction from capacity and outages, date-range filtering, calendar
splitting and grouping, default ordering, scaling, totals, discounting, and conversion to
cashflows. Edge cases of interest are pinned as explicit examples.
"""

import math
import warnings
from datetime import date, timedelta
from operator import attrgetter

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from dcaf.finance.escalation import ConstantRateEscalation, IndexSeriesEscalation
from dcaf.shared.time import PeriodTruncationWarning
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow, CashFlowStream, Generation, GenerationGroup, GenerationStream
from dcaf.streams.generation import _calendar_pieces
from strategies import (
    DATES,
    DAY_COUNT_CONVENTIONS,
    FINITE_AMOUNTS,
    FREQUENCIES,
    LABELS,
    NON_FINITE,
    PERIOD_DAYS,
    PRO_FORMA_CATEGORIES,
    STREAM_AMOUNTS,
    STREAM_DATES,
    STREAM_LABELS,
    TAX_TREATMENTS,
    TIMINGS,
    add_period,
    calendar_period_key,
    calendar_windows,
    counted_days,
    enum_spellings,
    intervals,
    pooled_lists,
    tail_days,
    timing_point,
)

# === Generation strategies ===

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


# === GenerationStream strategies ===

stream_point_generations = st.builds(
    Generation, amount_mwh=STREAM_AMOUNTS, date=STREAM_DATES, label=STREAM_LABELS
)
stream_period_generations = st.tuples(STREAM_DATES, st.integers(1, 800)).flatmap(
    lambda bounds: st.builds(
        Generation,
        amount_mwh=STREAM_AMOUNTS,
        label=STREAM_LABELS,
        period_start=st.just(bounds[0]),
        period_end=st.just(bounds[0] + timedelta(days=bounds[1])),
    )
)
stream_generations = st.one_of(stream_point_generations, stream_period_generations)
generation_lists = pooled_lists(stream_generations)

CAPACITIES = st.floats(min_value=0.0, max_value=2000.0)
CAPACITY_FACTORS = st.floats(min_value=0.0, max_value=1.0)
UNIT_FRACTIONS = st.floats(min_value=0.0, max_value=1.0)
PRICES = st.floats(min_value=0.0, max_value=1e4)
RATES = st.floats(min_value=-0.5, max_value=1.0)
ESCALATIONS = st.floats(min_value=-0.2, max_value=0.2)
CONVERSIONS = st.sampled_from(["to_revenue", "to_cost"])
GENERATION_SORT_ATTRS = ["period_start", "period_end", "amount_mwh", "label"]


def _amount_scale(entries: list[Generation]) -> float:
    return math.fsum(abs(g.amount_mwh) for g in entries)


def _expected_capacity_mwh(capacity_mw, capacity_factor, start, end, dcc) -> float:
    return capacity_mw * capacity_factor * 24.0 * counted_days(start, end, dcc)


# === GenerationStream construction ===


@given(CAPACITIES, CAPACITY_FACTORS, DATES, st.integers(0, 24), FREQUENCIES, DAY_COUNT_CONVENTIONS)
@example(1.0, 1.0, date(2024, 1, 1), 1, "year", "actual/365-no-leap")  # leap year without Feb 29
@example(1.0, 1.0, date(2024, 1, 1), 1, "year", "actual/actual")  # leap year with Feb 29
@example(100.0, 0.9, date(2030, 1, 31), 3, "month", "actual/actual")  # month-end start clamps
@example(100.0, 0.9, date(2030, 1, 1), 0, "year", "actual/actual")  # no periods
def test_from_capacity_tiles_whole_periods_with_capacity_energy(
    capacity_mw, capacity_factor, start, periods, frequency, dcc
):
    """Whole periods tile ``[start, ...)`` without gaps, each one nominal period long, and each
    entry holds capacity x factor x the hours the day-count convention puts on the clock."""
    stream = GenerationStream.from_capacity(
        capacity_mw, capacity_factor, start, periods, frequency, "unit", dcc
    )

    assert len(stream) == periods
    assert [g.period_start for g in stream] == ([start] + [g.period_end for g in stream])[:periods]
    low, high = PERIOD_DAYS[frequency]
    for g in stream:
        assert low <= (g.period_end - g.period_start).days <= high
        assert g.date is None and g.label == "unit"
        expected = _expected_capacity_mwh(
            capacity_mw, capacity_factor, g.period_start, g.period_end, dcc
        )
        assert _close(g.amount_mwh, expected, expected)


@given(DATES, st.integers(0, 4), st.floats(min_value=0.01, max_value=0.99), FREQUENCIES)
@example(date(2030, 1, 1), 0, 0.5, "month")  # 15.5 days of January keeps 15 and warns
@example(date(2030, 2, 1), 0, 0.5, "month")  # exactly 14 days of February does not warn
@example(date(2030, 1, 1), 2, 0.5, "day")  # half a day is dropped entirely
def test_from_capacity_fractional_tail_keeps_complete_days(start, whole, fraction, frequency):
    """A fractional period count appends the complete days of the requested share of the next
    period, warning exactly when a partial day is dropped."""
    periods = whole + fraction
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        stream = GenerationStream.from_capacity(1.0, 1.0, start, periods, frequency)

    full = stream[:whole]
    tail_start = full[-1].period_end if full else start
    days = tail_days(tail_start, periods - whole, frequency)
    assert len(stream) == whole + (days > 0)
    if days:
        assert (stream[-1].period_start, stream[-1].period_end) == (
            tail_start,
            tail_start + timedelta(days=days),
        )
    requested = (periods - whole) * (add_period(tail_start, frequency) - tail_start).days
    truncated = abs(requested - round(requested)) > 1e-12
    assert any(issubclass(w.category, PeriodTruncationWarning) for w in caught) is truncated


NEGATIVE = st.floats(max_value=-1e-9)
ABOVE_ONE = st.floats(min_value=1.0 + 1e-9)
invalid_capacity_inputs = st.one_of(
    st.one_of(NON_FINITE, NEGATIVE).map(lambda value: {"capacity_mw": value}),
    st.one_of(NON_FINITE, NEGATIVE, ABOVE_ONE).map(lambda value: {"capacity_factor": value}),
)


@given(invalid_capacity_inputs)
@example({"capacity_mw": -1.0})
@example({"capacity_factor": 1.1})
@example({"capacity_factor": -0.1})
def test_from_capacity_rejects_invalid_capacity(invalid):
    """Capacity must be finite and non-negative; the capacity factor must lie in [0, 1]."""
    arguments = {"capacity_mw": 1.0, "capacity_factor": 0.5} | invalid

    with pytest.raises(ValueError, match=next(iter(invalid))):
        GenerationStream.from_capacity(start=date(2030, 1, 1), periods=1, **arguments)


@given(CAPACITIES, CAPACITY_FACTORS, UNIT_FRACTIONS, intervals(), DAY_COUNT_CONVENTIONS, LABELS)
@example(1.0, 1.0, 1.0, (date(2024, 2, 28), date(2024, 3, 1)), "actual/365-no-leap", "x")
@example(1.0, 1.0, 0.0, (date(2030, 1, 1), date(2030, 1, 2)), "actual/actual", "x")
def test_from_outage_is_one_negative_entry_over_the_outage(
    capacity_mw, capacity_factor, reduction, interval, dcc, label
):
    """An outage is a single entry over exactly ``[start, end)`` holding the negated energy the
    reduced capacity would have produced."""
    start, end = interval

    outage = GenerationStream.from_outage(
        capacity_mw=capacity_mw,
        capacity_factor=capacity_factor,
        start=start,
        end=end,
        capacity_reduction=reduction,
        label=label,
        day_count_convention=dcc,
    )

    [g] = outage
    assert (g.date, g.period_start, g.period_end, g.label) == (None, start, end, label)
    lost = reduction * _expected_capacity_mwh(capacity_mw, capacity_factor, start, end, dcc)
    assert _close(g.amount_mwh, -lost, lost)


# Each invalid outage input overrides one valid argument; "days" sets ``end`` that many days
# after ``start``, so zero or fewer days is an empty or reversed interval.
invalid_outage_inputs = st.one_of(
    st.integers(-400, 0).map(lambda days: {"days": days}),
    st.one_of(NON_FINITE, NEGATIVE, ABOVE_ONE).map(lambda value: {"capacity_reduction": value}),
    st.one_of(NON_FINITE, NEGATIVE).flatmap(
        lambda value: st.sampled_from([{"capacity_mw": value}, {"capacity_factor": value}])
    ),
)


@given(DATES, invalid_outage_inputs)
@example(date(2030, 1, 2), {"days": 0})  # empty interval
@example(date(2030, 1, 1), {"capacity_reduction": 1.1})
def test_from_outage_rejects_invalid_inputs(start, invalid):
    """An empty or reversed interval, a reduction outside [0, 1], and a negative or non-finite
    capacity or factor are rejected."""
    arguments = {"capacity_mw": 1.0, "capacity_factor": 0.5, "days": 1} | invalid
    end = start + timedelta(days=arguments.pop("days"))

    with pytest.raises(ValueError):
        GenerationStream.from_outage(start=start, end=end, **arguments)


@given(
    CAPACITIES,
    CAPACITY_FACTORS,
    UNIT_FRACTIONS,
    DATES,
    st.integers(1, 6),
    FREQUENCIES,
    DAY_COUNT_CONVENTIONS,
    st.data(),
)
def test_outage_removes_exactly_the_generation_it_overlaps(
    capacity_mw, capacity_factor, reduction, start, periods, frequency, dcc, data
):
    """Adding an outage inside a capacity schedule lowers the total by exactly the reduced
    energy over the outage's own interval."""
    generation = GenerationStream.from_capacity(
        capacity_mw, capacity_factor, start, periods, frequency, day_count_convention=dcc
    )
    end = generation[-1].period_end
    outage_start = start + timedelta(days=data.draw(st.integers(0, (end - start).days - 1)))
    outage_end = outage_start + timedelta(days=data.draw(st.integers(1, (end - outage_start).days)))
    outage = GenerationStream.from_outage(
        capacity_mw=capacity_mw,
        capacity_factor=capacity_factor,
        start=outage_start,
        end=outage_end,
        capacity_reduction=reduction,
        day_count_convention=dcc,
    )

    full = _expected_capacity_mwh(capacity_mw, capacity_factor, start, end, dcc)
    lost = reduction * _expected_capacity_mwh(
        capacity_mw, capacity_factor, outage_start, outage_end, dcc
    )
    assert _close(generation.extend(outage).sum(), full - lost, full)


@given(generation_lists, CAPACITIES, CAPACITY_FACTORS, DATES, st.integers(0, 4), FREQUENCIES)
def test_with_capacity_appends_a_capacity_schedule(
    entries, capacity_mw, capacity_factor, start, periods, frequency
):
    """with_capacity is extending the stream with the matching from_capacity schedule."""
    stream = GenerationStream(entries)

    appended = stream.with_capacity(capacity_mw, capacity_factor, start, periods, frequency)

    assert appended.entries == entries + list(
        GenerationStream.from_capacity(capacity_mw, capacity_factor, start, periods, frequency)
    )


# A from_streams source paired with the entries it contributes, including a bare entry.
generation_sources = st.one_of(
    generation_lists.map(lambda entries: (GenerationStream(entries), entries)),
    generation_lists.map(lambda entries: (list(entries), entries)),
    generation_lists.map(lambda entries: (iter(entries), entries)),
    stream_generations.map(lambda entry: (entry, [entry])),
)


@given(st.lists(generation_sources, max_size=4))
def test_from_streams_treats_a_bare_entry_as_one_entry(drawn):
    """Bare Generation arguments join streams and iterables, contributing themselves once."""
    combined = GenerationStream.from_streams(*(source for source, _ in drawn))

    assert type(combined) is GenerationStream
    assert combined.entries == [entry for _, entries in drawn for entry in entries]


@given(generation_lists, st.lists(st.builds(CashFlow, STREAM_AMOUNTS, STREAM_DATES), max_size=3))
def test_from_streams_rejects_cashflow_streams(entries, flows):
    """A CashFlowStream cannot be combined into a GenerationStream, even when empty."""
    with pytest.raises(TypeError, match="Cannot combine GenerationStream with CashFlowStream"):
        GenerationStream.from_streams(GenerationStream(entries), CashFlowStream(flows))


# === filtering ===


def _overlaps(g: Generation, start: date | None, end: date | None) -> bool:
    """Whether any day of the entry's period falls in ``[start, end)``, checked day by day."""
    days = (g.period_start + timedelta(days=i) for i in range((g.period_end - g.period_start).days))
    return any((start is None or start <= day) and (end is None or day < end) for day in days)


BOUNDS = st.none() | STREAM_DATES
_JANUARY = Generation(1.0, period_start=date(2028, 1, 1), period_end=date(2028, 2, 1))


@given(generation_lists, BOUNDS, BOUNDS)
@example([_JANUARY], date(2028, 2, 1), None)  # ends exactly at start: excluded
@example([_JANUARY], None, date(2028, 1, 1))  # starts exactly at end: excluded
@example([_JANUARY], date(2028, 1, 31), date(2028, 2, 1))  # last day only: included whole
@example([Generation(1.0, date(2028, 2, 29))], date(2028, 2, 29), date(2028, 3, 1))
def test_date_range_keeps_whole_entries_that_overlap(entries, start, end):
    """date_range keeps, unclipped and in order, every entry with a day in ``[start, end)``."""
    assume(start is None or end is None or start < end)

    selected = GenerationStream(entries).date_range(start, end)

    assert selected.entries == [g for g in entries if _overlaps(g, start, end)]


@given(generation_lists, STREAM_DATES, st.integers(0, 400))
@example([_JANUARY], date(2028, 1, 15), 0)  # an empty interval would otherwise keep January
@example([], date(2028, 1, 1), 0)
def test_date_range_rejects_empty_or_reversed_interval(entries, end, days_after_end):
    """Two bounds must form a non-empty interval, whatever the stream holds."""
    start = end + timedelta(days=days_after_end)

    with pytest.raises(ValueError, match="end must be after start"):
        GenerationStream(entries).date_range(start, end)


@given(generation_lists, st.functions(like=lambda g: True, returns=st.booleans(), pure=True))
def test_filter_keeps_matching_entries_in_order(entries, predicate):
    """filter keeps exactly the entries the predicate accepts, in order."""
    assert GenerationStream(entries).filter(predicate).entries == [
        g for g in entries if predicate(g)
    ]


# === calendar pieces ===

NESTED_FREQUENCIES = st.sampled_from(
    [("day", coarse) for coarse in ("month", "quarter", "year")]
    + [("month", "quarter"), ("month", "year"), ("quarter", "year")]
)


def _pieces(g: Generation, frequency: str, dcc: str) -> list[Generation]:
    return _calendar_pieces(g, frequency=frequency, day_count_convention=dcc)


@given(stream_generations, FREQUENCIES, DAY_COUNT_CONVENTIONS)
def test_calendar_pieces_tile_the_source_at_calendar_boundaries(g, frequency, dcc):
    """Pieces cover the source period contiguously and in order, each lies in one calendar
    period, and every cut between pieces falls on a calendar period start."""
    pieces = _pieces(g, frequency, dcc)

    assert pieces[0].period_start == g.period_start
    assert pieces[-1].period_end == g.period_end
    for before, after in zip(pieces, pieces[1:]):
        assert before.period_end == after.period_start
        assert calendar_period_key(after.period_start, frequency) == after.period_start
    for p in pieces:
        last_day = p.period_end - timedelta(days=1)
        assert calendar_period_key(p.period_start, frequency) == calendar_period_key(
            last_day, frequency
        )


@given(stream_generations, FREQUENCIES, DAY_COUNT_CONVENTIONS)
@example(Generation(1.0, date(2028, 12, 31)), "year", "actual/actual")  # point entry
def test_calendar_pieces_leave_single_period_entries_unchanged(g, frequency, dcc):
    """An entry inside one calendar period is returned as the same object, keeping its legacy
    date. A split entry becomes period-bounded pieces that keep the source label."""
    last_day = g.period_end - timedelta(days=1)
    within_one_period = calendar_period_key(g.period_start, frequency) == calendar_period_key(
        last_day, frequency
    )

    pieces = _pieces(g, frequency, dcc)

    if within_one_period:
        assert len(pieces) == 1
        assert pieces[0] is g
    else:
        assert len(pieces) > 1
        assert all(p.date is None and p.label == g.label for p in pieces)


@given(stream_generations, FREQUENCIES, DAY_COUNT_CONVENTIONS)
def test_calendar_pieces_are_idempotent(g, frequency, dcc):
    """Splitting a piece again at the same frequency returns it unchanged."""
    for p in _pieces(g, frequency, dcc):
        again = _pieces(p, frequency, dcc)
        assert len(again) == 1
        assert again[0] is p


@given(stream_generations, NESTED_FREQUENCIES, DAY_COUNT_CONVENTIONS)
def test_calendar_pieces_refine_through_nested_frequencies(g, frequencies, dcc):
    """Splitting by a coarse frequency and then a finer nested one matches splitting by the
    finer frequency directly."""
    fine, coarse = frequencies
    direct = _pieces(g, fine, dcc)

    refined = [p for piece in _pieces(g, coarse, dcc) for p in _pieces(piece, fine, dcc)]

    assert [(p.period_start, p.period_end) for p in refined] == [
        (p.period_start, p.period_end) for p in direct
    ]
    for got, want in zip(refined, direct, strict=True):
        assert _close(got.amount_mwh, want.amount_mwh, g.amount_mwh)


@given(stream_generations, FREQUENCIES, DAY_COUNT_CONVENTIONS)
@example(  # Feb 29 is its own zero-day piece under no-leap and must not lose energy
    Generation(10.0, period_start=date(2028, 2, 28), period_end=date(2028, 3, 2)),
    "day",
    "actual/365-no-leap",
)
def test_calendar_pieces_conserve_and_share_amount_by_counted_days(g, frequency, dcc):
    """Piece amounts sum to the source amount, and any two pieces' amounts are in the ratio of
    their counted days. Together these fix each piece's sign to the source's."""
    pieces = _pieces(g, frequency, dcc)
    days = [counted_days(p.period_start, p.period_end, dcc) for p in pieces]

    assert _close(math.fsum(p.amount_mwh for p in pieces), g.amount_mwh, g.amount_mwh)
    reference = max(range(len(pieces)), key=days.__getitem__)
    assume(days[reference] > 0)
    for p, d in zip(pieces, days, strict=True):
        assert _close(
            p.amount_mwh * days[reference],
            pieces[reference].amount_mwh * d,
            g.amount_mwh * days[reference],
        )


@given(stream_generations, st.floats(min_value=-100.0, max_value=100.0), FREQUENCIES)
def test_calendar_pieces_scale_with_the_source_amount(g, factor, frequency):
    """Scaling the source amount scales every piece by the same factor."""
    pieces = _pieces(g, frequency, "actual/actual")

    scaled = _pieces(g.replace(amount_mwh=g.amount_mwh * factor), frequency, "actual/actual")

    for got, base in zip(scaled, pieces, strict=True):
        assert _close(got.amount_mwh, base.amount_mwh * factor, g.amount_mwh * factor)


@pytest.mark.parametrize(
    ("g", "frequency", "dcc", "expected"),
    [
        pytest.param(
            Generation(731.0, period_start=date(2030, 7, 1), period_end=date(2032, 7, 1)),
            "year",
            "actual/actual",
            [
                (date(2030, 7, 1), date(2031, 1, 1), 184.0),
                (date(2031, 1, 1), date(2032, 1, 1), 365.0),
                (date(2032, 1, 1), date(2032, 7, 1), 182.0),
            ],
            id="multi-year",
        ),
        pytest.param(
            Generation(3.0, period_start=date(2028, 2, 28), period_end=date(2028, 3, 2)),
            "month",
            "actual/365-no-leap",
            [
                (date(2028, 2, 28), date(2028, 3, 1), 1.5),
                (date(2028, 3, 1), date(2028, 3, 2), 1.5),
            ],
            id="leap-day-uncounted",
        ),
        pytest.param(
            Generation(31.0, period_start=date(2028, 3, 15), period_end=date(2028, 4, 15)),
            "quarter",
            "actual/actual",
            [
                (date(2028, 3, 15), date(2028, 4, 1), 17.0),
                (date(2028, 4, 1), date(2028, 4, 15), 14.0),
            ],
            id="quarter-crossing",
        ),
    ],
)
def test_calendar_pieces_examples(g, frequency, dcc, expected):
    """Worked splits pinning concrete bounds and amounts."""
    pieces = _pieces(g, frequency, dcc)

    assert [(p.period_start, p.period_end) for p in pieces] == [(s, e) for s, e, _ in expected]
    assert [p.amount_mwh for p in pieces] == pytest.approx([a for _, _, a in expected])


# === grouping ===


@given(generation_lists, FREQUENCIES, DAY_COUNT_CONVENTIONS)
def test_group_by_period_groups_calendar_pieces_by_period_start(entries, frequency, dcc):
    """Grouping by period splits each entry into its calendar pieces and groups them under
    each period's first day, keeping source order within and across groups."""
    expected: dict[date, list[Generation]] = {}
    for g in entries:
        for piece in _pieces(g, frequency, dcc):
            key = calendar_period_key(piece.period_start, frequency)
            expected.setdefault(key, []).append(piece)

    grouped = GenerationStream(entries).group_by(period=frequency, day_count_convention=dcc)

    assert type(grouped) is GenerationGroup
    assert list(grouped) == list(expected)
    for key, pieces in expected.items():
        assert type(grouped[key]) is GenerationStream
        assert grouped[key].entries == pieces


@given(generation_lists, SETTLEMENT_CONVENTIONS)
def test_period_group_totals_match_settled_period_totals(entries, conventions):
    """Grouping by period conserves the stream total and gives each calendar period the same
    total that settlement books in it, whatever the booking timing."""
    frequency, dcc = conventions["frequency"], conventions["day_count_convention"]
    stream = GenerationStream(entries)
    settled: dict[date, float] = {}
    for day, amount in _settle(stream, conventions):
        key = calendar_period_key(day, frequency)
        settled[key] = settled.get(key, 0.0) + amount

    totals = stream.group_by(period=frequency, day_count_convention=dcc).sum()

    scale = _amount_scale(entries)
    assert _close(math.fsum(totals.values()), stream.sum(), scale)
    assert totals.keys() == settled.keys()
    for key, total in totals.items():
        assert _close(total, settled[key], scale)


@given(generation_lists, st.functions(like=lambda g: None, returns=st.integers(0, 2), pure=True))
def test_group_by_key_keeps_entries_whole(entries, key):
    """Grouping by a key function never splits entries, unlike grouping by period."""
    grouped = GenerationStream(entries).group_by(key)

    assert type(grouped) is GenerationGroup
    assert grouped.ungroup().count() == len(entries)


@given(generation_lists, FREQUENCIES)
def test_group_by_requires_exactly_one_of_key_or_period(entries, frequency):
    """A key function and a period cannot be combined, and one of them is required."""
    stream = GenerationStream(entries)
    with pytest.raises(ValueError, match="Cannot pass both"):
        stream.group_by(lambda g: g.label, period=frequency)
    with pytest.raises(ValueError, match="requires a key function or 'period'"):
        stream.group_by()


# === sorting, scaling, and totals ===

_LONG = Generation(2.0, label="long", period_start=date(2028, 1, 1), period_end=date(2028, 3, 1))
_SHORT = Generation(1.0, label="short", period_start=date(2028, 1, 1), period_end=date(2028, 2, 1))


@given(generation_lists, st.booleans())
@example([_LONG, _SHORT, Generation(5.0, date(2028, 1, 1))], False)  # shared starts, ends differ
def test_default_sort_orders_by_period_bounds(entries, descending):
    """sort() and sort(attr="date") order stably by period start, then period end, rather than
    by the legacy ``date`` field, which period entries leave unset."""
    expected = sorted(entries, key=lambda g: (g.period_start, g.period_end), reverse=descending)
    stream = GenerationStream(entries)

    assert stream.sort(descending=descending).entries == expected
    assert stream.sort(attr="date", descending=descending).entries == expected


@given(generation_lists, st.sampled_from(GENERATION_SORT_ATTRS), st.booleans())
def test_sort_by_named_attribute(entries, attr, descending):
    """sort(attr=...) orders stably by each allowed Generation attribute."""
    expected = sorted(entries, key=attrgetter(attr), reverse=descending)

    assert GenerationStream(entries).sort(attr=attr, descending=descending).entries == expected


@given(generation_lists, st.text())
@example([], "amount")  # a CashFlow attribute name
def test_sort_rejects_other_attributes(entries, attr):
    """Attribute names outside the allowed set are rejected, as is combining one with a key."""
    assume(attr not in ["date", *GENERATION_SORT_ATTRS])
    stream = GenerationStream(entries)

    with pytest.raises(AssertionError, match="Unexpected sort attribute"):
        stream.sort(attr=attr)
    with pytest.raises(ValueError, match="Cannot pass both"):
        stream.sort(lambda g: g.label, attr="label")


@given(generation_lists, st.floats(min_value=-1e3, max_value=1e3))
@example([Generation(5.0, date(2028, 2, 29))], -1.0)  # a point entry keeps its date
@example([Generation(-0.0, date(2028, 1, 1)), _LONG], 0.0)
def test_scale_multiplies_only_amounts(entries, factor):
    """scale multiplies every amount by the factor and leaves every other field unchanged."""
    scaled = GenerationStream(entries).scale(factor)

    assert type(scaled) is GenerationStream
    assert [(g.date, g.label, g.period_start, g.period_end) for g in scaled] == [
        (g.date, g.label, g.period_start, g.period_end) for g in entries
    ]
    assert [g.amount_mwh for g in scaled] == [g.amount_mwh * factor for g in entries]


@given(generation_lists, st.functions(like=lambda g: None, returns=st.integers(0, 2), pure=True))
@example([], lambda g: 0)
@example([Generation(1e12, date(2028, 1, 1)), Generation(-1e12, date(2028, 1, 2))], lambda g: 0)
def test_sum_totals_amounts_and_group_sums_partition_it(entries, key):
    """sum nets signed amounts, is exactly 0.0 when empty, and group sums are each group's total,
    adding back up to the stream's."""
    stream = GenerationStream(entries)
    grouped = stream.group_by(key)

    assert _close(stream.sum(), math.fsum(g.amount_mwh for g in entries), _amount_scale(entries))
    if not entries:
        assert stream.sum() == 0.0
    assert grouped.sum() == {k: group.sum() for k, group in grouped.items()}
    assert _close(math.fsum(grouped.sum().values()), stream.sum(), _amount_scale(entries))


# === discounting and conversion to cashflows ===


@given(generation_lists, RATES, STREAM_DATES, SETTLEMENT_CONVENTIONS)
@example(
    [],
    0.1,
    date(2028, 1, 1),
    {"frequency": "year", "timing": "end", "day_count_convention": "actual/actual"},
)
@example(
    [Generation(10.0, date(2028, 2, 29))],
    0.1,
    date(2028, 1, 1),
    {"frequency": "month", "timing": "end", "day_count_convention": "actual/365-no-leap"},
)
def test_discounted_sum_is_the_npv_of_unit_price_revenue(entries, rate, valuation, conventions):
    """The LCOE denominator discounts generation exactly as revenue at a price of 1.0 would be
    discounted, so it cannot drift from the numerator; at a zero rate it is the total."""
    dcc = conventions["day_count_convention"]
    stream = GenerationStream(entries)
    timing = {"frequency": conventions["frequency"], "timing": conventions["timing"]}

    discounted = stream.discounted_sum(rate, valuation, dcc, **timing)

    expected = stream.to_revenue(1.0, **conventions).npv(rate, valuation, dcc)
    scale = _amount_scale(entries) * 16.0
    assert _close(discounted, expected, scale)
    assert _close(stream.discounted_sum(0.0, valuation, dcc, **timing), stream.sum(), scale)


@given(generation_lists, st.one_of(NON_FINITE, st.floats(max_value=-1.0)))
@example([], -1.0)
def test_discounted_sum_rejects_rates_outside_the_domain(entries, rate):
    """The rate must be finite and greater than -1, even for an empty stream."""
    with pytest.raises(ValueError, match="rate must be"):
        GenerationStream(entries).discounted_sum(rate, date(2028, 1, 1))


@given(
    generation_lists,
    PRICES,
    ESCALATIONS,
    FREQUENCIES,
    st.none() | STREAM_DATES,
    SETTLEMENT_CONVENTIONS,
)
def test_cost_is_negated_revenue(entries, price, escalation, period, reference, conventions):
    """At the same rate and escalation, to_cost books the exact negation of to_revenue on the
    same dates."""
    stream = GenerationStream(entries)
    arguments = {
        "escalation": escalation,
        "escalation_period": period,
        "amount_reference_date": reference,
        **conventions,
    }

    revenue = stream.to_revenue(price, **arguments)
    cost = stream.to_cost(price, **arguments)

    assert [cf.date for cf in cost] == [cf.date for cf in revenue]
    assert [cf.amount for cf in cost] == [-cf.amount for cf in revenue]


constant_rate_policies = st.builds(
    ConstantRateEscalation,
    reference_date=STREAM_DATES,
    rate=ESCALATIONS,
    period=FREQUENCIES,
    day_count_convention=DAY_COUNT_CONVENTIONS,
)
# Index points start well before any booking date, so every date is evaluable.
index_series_policies = st.builds(
    lambda first, later, reference: IndexSeriesEscalation(
        reference, ((date(2000, 1, 1), first), *sorted(later))
    ),
    st.floats(min_value=0.5, max_value=2.0),
    st.lists(
        st.tuples(STREAM_DATES, st.floats(min_value=0.5, max_value=2.0)),
        max_size=4,
        unique_by=lambda point: point[0],
    ),
    STREAM_DATES,
)


@given(
    st.lists(stream_generations, min_size=1, max_size=4),
    PRICES,
    st.one_of(constant_rate_policies, index_series_policies),
    SETTLEMENT_CONVENTIONS,
    CONVERSIONS,
)
def test_price_escalates_to_each_settlement_date(entries, price, policy, conventions, method):
    """Each cashflow is the settled MWh times the price escalated to that cashflow's own
    settlement date, signed positive for revenue and negative for cost."""
    sign = 1.0 if method == "to_revenue" else -1.0

    flows = getattr(GenerationStream(entries), method)(
        price, escalation_policy=policy, **conventions
    )

    settled = _settle(GenerationStream(entries), conventions)
    assert [cf.date for cf in flows] == [day for day, _ in settled]
    for cf, (day, mwh) in zip(flows, settled, strict=True):
        expected = sign * mwh * price * policy.factor(day)
        assert _close(cf.amount, expected, expected)


@given(
    st.lists(stream_generations, min_size=1, max_size=4),
    PRICES,
    ESCALATIONS,
    FREQUENCIES,
    STREAM_DATES,
    SETTLEMENT_CONVENTIONS,
    CONVERSIONS,
)
def test_escalation_keywords_match_constant_rate_policy(
    entries, price, escalation, period, reference, conventions, method
):
    """Escalation keywords build the constant-rate policy with the conversion's day count."""
    convert = getattr(GenerationStream(entries), method)
    policy = ConstantRateEscalation(
        reference, escalation, period, conventions["day_count_convention"]
    )

    by_keywords = convert(
        price,
        escalation=escalation,
        escalation_period=period,
        amount_reference_date=reference,
        **conventions,
    )

    assert by_keywords.entries == convert(price, escalation_policy=policy, **conventions).entries


@given(
    st.lists(stream_generations, min_size=1, max_size=4),
    PRICES,
    ESCALATIONS,
    FREQUENCIES,
    SETTLEMENT_CONVENTIONS,
    CONVERSIONS,
)
@example(  # booked at year end, so the earliest booking date is not the period start
    [Generation(1.0, period_start=date(2028, 1, 1), period_end=date(2029, 1, 1))],
    10.0,
    0.1,
    "year",
    {"frequency": "year", "timing": "end", "day_count_convention": "actual/actual"},
    "to_revenue",
)
def test_escalation_reference_defaults_to_earliest_settlement_date(
    entries, price, escalation, period, conventions, method
):
    """Without an explicit reference date, the price is known on the earliest settlement date."""
    convert = getattr(GenerationStream(entries), method)
    earliest = min(day for day, _ in _settle(GenerationStream(entries), conventions))
    arguments = {"escalation": escalation, "escalation_period": period, **conventions}

    assert (
        convert(price, **arguments).entries
        == convert(price, amount_reference_date=earliest, **arguments).entries
    )


def _spellings(value):
    """Draw an enum value as itself or as any user-facing spelling of it."""
    return st.one_of(st.just(value), enum_spellings(value.value))


@given(
    generation_lists,
    PRICES,
    CONVERSIONS,
    LABELS,
    PRO_FORMA_CATEGORIES,
    TAX_TREATMENTS,
    st.data(),
    SETTLEMENT_CONVENTIONS,
)
def test_converted_cashflows_carry_the_requested_metadata(
    entries, price, method, label, category, treatment, data, conventions
):
    """Every converted cashflow is cash with the requested label and classification, given as
    enums or as any user-facing spelling; an empty stream converts to an empty stream."""
    category_input = None if category is None else data.draw(_spellings(category))

    flows = getattr(GenerationStream(entries), method)(
        price,
        label=label,
        pro_forma_category=category_input,
        tax_treatment=data.draw(_spellings(treatment)),
        **conventions,
    )

    assert type(flows) is CashFlowStream
    assert len(flows) == len(_settle(GenerationStream(entries), conventions))
    for cf in flows:
        assert (cf.is_cash, cf.label, cf.pro_forma_category, cf.tax_treatment) == (
            True,
            label,
            category,
            treatment,
        )


@given(generation_lists)
def test_conversions_default_to_revenue_and_variable_cost_classification(entries):
    """Revenue defaults to taxable revenue and cost to deductible operating cost."""
    stream = GenerationStream(entries)
    defaults = {
        "to_revenue": ("Generation Revenue", ProFormaCategory.REVENUE, TaxTreatment.TAXABLE),
        "to_cost": ("Variable Cost", ProFormaCategory.OPERATING_COST, TaxTreatment.DEDUCTIBLE),
    }

    for method, expected in defaults.items():
        for cf in getattr(stream, method)(1.0):
            assert (cf.label, cf.pro_forma_category, cf.tax_treatment) == expected


@given(generation_lists, CONVERSIONS, st.one_of(NON_FINITE, st.floats(max_value=-1e-9)))
@example([], "to_cost", -5.0)
def test_conversions_reject_negative_or_non_finite_prices(entries, method, price):
    """Prices and cost rates must be finite and non-negative, even for an empty stream."""
    with pytest.raises(ValueError, match="_per_mwh must be"):
        getattr(GenerationStream(entries), method)(price)


# === non-mutation ===


@given(generation_lists, FREQUENCIES)
def test_generation_operations_leave_the_source_unchanged(entries, frequency):
    """Every GenerationStream-specific operation leaves the source entries unchanged."""
    stream = GenerationStream(entries)
    snapshot = list(entries)
    operations = [
        lambda s: s.filter(lambda g: g.amount_mwh > 0),
        lambda s: s.date_range(date(2028, 1, 1), date(2028, 7, 1)),
        lambda s: s.group_by(period=frequency),
        lambda s: s.sort(),
        lambda s: s.sort(attr="amount_mwh", descending=True),
        lambda s: s.scale(2.0),
        lambda s: s.with_capacity(1.0, 1.0, date(2028, 1, 1), 1),
        lambda s: s.to_revenue(1.0, frequency=frequency),
        lambda s: s.to_cost(1.0, frequency=frequency),
        lambda s: s.discounted_sum(0.1, date(2028, 1, 1), frequency=frequency),
    ]

    for operation in operations:
        operation(stream)
        assert stream.entries == snapshot
