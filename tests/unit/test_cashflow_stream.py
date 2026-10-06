# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Property tests for the CashFlowStream- and CashFlowGroup-specific behavior.

Collection mechanics inherited from BaseStream and BaseGroup are covered by
``test_stream_base.py``; these properties cover what CashFlowStream adds: construction, keyword
filtering, date and sign selection, calendar grouping, default ordering, scaling, totals, and
discounting. Edge cases of interest are pinned as explicit examples.
"""

import math
import warnings
from collections import Counter
from datetime import date, timedelta
from operator import attrgetter

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from dcaf.finance.escalation import (
    ConstantRateEscalation,
    EscalationBuilder,
    IndexSeriesEscalation,
)
from dcaf.shared.time import PeriodTruncationWarning
from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.streams import CashFlow, CashFlowGroup, CashFlowStream, Generation, GenerationStream
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
    enum_spellings,
    pooled_lists,
    tail_days,
    timing_point,
    year_fraction,
)

stream_cashflows = st.builds(
    CashFlow,
    amount=STREAM_AMOUNTS,
    date=STREAM_DATES,
    label=STREAM_LABELS,
    is_cash=st.booleans(),
    pro_forma_category=PRO_FORMA_CATEGORIES,
    tax_treatment=TAX_TREATMENTS,
)
cashflow_lists = pooled_lists(stream_cashflows)

RATES = st.floats(min_value=-0.5, max_value=1.0)
CASHFLOW_SORT_ATTRS = ["date", "amount", "label"]
KEYS = st.functions(like=lambda cf: None, returns=st.integers(0, 2), pure=True)


def _close(actual: float, expected: float, scale: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9 * abs(scale) + 1e-9)


def _amount_scale(entries: list[CashFlow]) -> float:
    return math.fsum(abs(cf.amount) for cf in entries)


def _spellings(value):
    """Draw an enum value as itself or as any user-facing spelling of it."""
    return st.one_of(st.just(value), enum_spellings(value.value))


# Cover each combination of category and cash basis, so keyword filters must AND their criteria.
_CLASSIFIED = [
    CashFlow(100.0, date(2028, 1, 1), pro_forma_category=ProFormaCategory.OPERATING_COST),
    CashFlow(
        200.0,
        date(2028, 2, 1),
        is_cash=False,
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        tax_treatment=TaxTreatment.DEDUCTIBLE,
    ),
    CashFlow(300.0, date(2028, 3, 1), pro_forma_category=ProFormaCategory.REVENUE),
    CashFlow(400.0, date(2028, 4, 1), is_cash=False, pro_forma_category=None),
]


# === from_recurring ===


def _recurring_windows(start, periods, frequency, **arguments) -> list[tuple[date, date]]:
    """Recover each generated period's ``[start, end)`` from its begin and end booking dates."""
    begin = CashFlowStream.from_recurring(
        start, periods, 1.0, frequency, timing="begin", **arguments
    )
    end = CashFlowStream.from_recurring(start, periods, 1.0, frequency, timing="end", **arguments)
    return [
        (first.date, last.date + timedelta(days=1)) for first, last in zip(begin, end, strict=True)
    ]


@given(DATES, st.integers(0, 24), FINITE_AMOUNTS, FREQUENCIES, TIMINGS)
@example(date(2026, 1, 1), 1, 1000.0, "year", "middle")  # books on July 2
@example(date(2026, 1, 31), 4, 1.0, "month", "begin")  # month-end start clamps to the 28th
@example(date(2028, 2, 29), 2, 1.0, "year", "end")  # leap-day start
@example(date(2026, 1, 1), 0, 1.0, "year", "end")  # no periods
def test_from_recurring_books_each_whole_period_at_its_timing_point(
    start, periods, amount, frequency, timing
):
    """Whole periods tile ``[start, ...)`` without gaps, each one nominal period long, and each
    books the unescalated amount on the period's timing point."""
    stream = CashFlowStream.from_recurring(start, periods, amount, frequency, timing=timing)

    windows = _recurring_windows(start, periods, frequency)
    assert len(stream) == len(windows) == periods
    assert [w_start for w_start, _ in windows] == ([start] + [end for _, end in windows])[:periods]
    low, high = PERIOD_DAYS[frequency]
    for (w_start, w_end), cf in zip(windows, stream, strict=True):
        assert low <= (w_end - w_start).days <= high
        assert cf.date == timing_point(w_start, w_end, timing)
        assert cf.amount == amount


@given(DATES, st.integers(0, 4), st.floats(min_value=0.01, max_value=0.99), FREQUENCIES)
@example(date(2026, 1, 1), 0, 0.5, "month")  # 15.5 days of January keeps 15 and warns
@example(date(2026, 2, 1), 0, 0.5, "month")  # exactly 14 days of February does not warn
@example(date(2026, 1, 1), 2, 0.5, "day")  # half a day is dropped entirely
def test_from_recurring_fractional_tail_prorates_complete_days(start, whole, fraction, frequency):
    """A fractional period count appends the complete days of the requested share of the next
    period, at a strictly partial amount, warning exactly when a partial day is dropped."""
    periods = whole + fraction
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        stream = CashFlowStream.from_recurring(start, periods, 1.0, frequency)
        windows = _recurring_windows(start, periods, frequency)

    tail_start = windows[whole - 1][1] if whole else start
    days = tail_days(tail_start, periods - whole, frequency)
    assert len(stream) == whole + (days > 0)
    assert all(cf.amount == 1.0 for cf in stream[:whole])
    if days:
        assert windows[-1] == (tail_start, tail_start + timedelta(days=days))
        assert 0.0 < stream[-1].amount < 1.0
    requested = (periods - whole) * (add_period(tail_start, frequency) - tail_start).days
    truncated = abs(requested - round(requested)) > 1e-12
    assert any(issubclass(w.category, PeriodTruncationWarning) for w in caught) is truncated


# Rates are bounded per compounding period so a decade of escalation stays finite.
constant_rate_terms = st.sampled_from(
    [("year", 0.2), ("quarter", 0.05), ("month", 0.02), ("day", 0.0005)]
).flatmap(lambda term: st.tuples(st.just(term[0]), st.floats(-term[1], term[1])))
recurring_policies = st.one_of(
    st.builds(
        lambda term, reference, dcc: ConstantRateEscalation(reference, term[1], term[0], dcc),
        constant_rate_terms,
        DATES,
        DAY_COUNT_CONVENTIONS,
    ),
    # Index points start before any date drawn, so every booking date is evaluable.
    st.builds(
        lambda first, later, reference: IndexSeriesEscalation(
            reference, ((date(1900, 1, 1), first), *sorted(later))
        ),
        st.floats(min_value=0.5, max_value=2.0),
        st.lists(
            st.tuples(DATES, st.floats(min_value=0.5, max_value=2.0)),
            max_size=4,
            unique_by=lambda point: point[0],
        ),
        DATES,
    ),
)


@given(DATES, st.integers(1, 12), FINITE_AMOUNTS, FREQUENCIES, TIMINGS, recurring_policies)
def test_from_recurring_escalates_to_each_booking_date(
    start, periods, amount, frequency, timing, policy
):
    """Each amount is escalated to its own booking date, not to its period start or by
    recurrence count, while booking dates are unaffected by escalation."""
    flat = CashFlowStream.from_recurring(start, periods, amount, frequency, timing=timing)

    escalated = CashFlowStream.from_recurring(
        start, periods, amount, frequency, timing=timing, escalation_policy=policy
    )

    assert [cf.date for cf in escalated] == [cf.date for cf in flat]
    for cf in escalated:
        expected = amount * policy.factor(cf.date)
        assert _close(cf.amount, expected, expected)


@given(
    DATES,
    st.integers(1, 12),
    FINITE_AMOUNTS,
    FREQUENCIES,
    constant_rate_terms,
    st.none() | DATES,
    DAY_COUNT_CONVENTIONS,
)
@example(date(2026, 7, 1), 2, 100.0, "month", ("year", 0.12), date(2026, 1, 1), "actual/actual")
def test_from_recurring_escalation_keywords_match_constant_rate_policy(
    start, periods, amount, frequency, term, reference, dcc
):
    """Escalation keywords build a constant-rate policy known on the reference date, which
    defaults to ``start``."""
    period, rate = term
    policy = ConstantRateEscalation(start if reference is None else reference, rate, period, dcc)

    by_keywords = CashFlowStream.from_recurring(
        start,
        periods,
        amount,
        frequency,
        escalation=rate,
        escalation_period=period,
        amount_reference_date=reference,
        day_count_convention=dcc,
    )

    assert (
        by_keywords.entries
        == CashFlowStream.from_recurring(
            start, periods, amount, frequency, escalation_policy=policy, day_count_convention=dcc
        ).entries
    )


@given(
    DATES,
    st.integers(0, 12),
    FREQUENCIES,
    LABELS,
    st.booleans(),
    PRO_FORMA_CATEGORIES,
    TAX_TREATMENTS,
    st.data(),
)
def test_from_recurring_applies_metadata_to_every_flow(
    start, periods, frequency, label, is_cash, category, treatment, data
):
    """Every generated flow carries the requested label, cash basis, and classification, given as
    enums or as any user-facing spelling."""
    stream = CashFlowStream.from_recurring(
        start,
        periods,
        1.0,
        frequency,
        label=label,
        is_cash=is_cash,
        pro_forma_category=None if category is None else data.draw(_spellings(category)),
        tax_treatment=data.draw(_spellings(treatment)),
    )

    for cf in stream:
        assert (cf.label, cf.is_cash, cf.pro_forma_category, cf.tax_treatment) == (
            label,
            is_cash,
            category,
            treatment,
        )


@given(DATES, st.integers(1, 12), FINITE_AMOUNTS)
@example(date(2026, 1, 1), 4, 1000.0)
def test_from_recurring_defaults_to_annual_cash_flows_at_period_end(start, periods, amount):
    """By default, flows are annual, booked on each period's last day, unescalated, cash, and
    classified as other with no tax treatment."""
    stream = CashFlowStream.from_recurring(start, periods, amount)

    assert stream.entries == [
        CashFlow(
            amount,
            timing_point(w_start, w_end, "end"),
            label="Recurring Payment",
            pro_forma_category=ProFormaCategory.OTHER,
            tax_treatment=TaxTreatment.NONE,
        )
        for w_start, w_end in _recurring_windows(start, periods, "year")
    ]


invalid_recurring_inputs = st.one_of(
    st.text()
    .filter(lambda text: text not in {"day", "month", "quarter", "year"})
    .map(lambda text: ({"frequency": text}, AssertionError)),
    st.sampled_from(
        [
            {"escalation": 0.02},
            {"escalation_period": "month"},
            {"amount_reference_date": date(2026, 1, 1)},
        ]
    ).map(
        lambda simple: (
            simple | {"escalation_policy": ConstantRateEscalation(date(2026, 1, 1), 0.02)},
            ValueError,
        )
    ),
    st.floats(-0.2, 0.2).map(
        lambda rate: (
            {"escalation_policy": EscalationBuilder(date(2026, 1, 1)).constant_rate(rate)},
            TypeError,
        )
    ),
)


@given(invalid_recurring_inputs)
@example(({"frequency": "weekly"}, AssertionError))
def test_from_recurring_rejects_invalid_schedules(invalid):
    """Unknown frequencies, a policy combined with simple escalation keywords, and an unbuilt
    escalation builder are rejected."""
    arguments, error = invalid

    with pytest.raises(error):
        CashFlowStream.from_recurring(start=date(2026, 1, 1), periods=2, amount=100.0, **arguments)


# === from_streams ===

# A from_streams source paired with the entries it contributes, including a bare entry.
cashflow_sources = st.one_of(
    cashflow_lists.map(lambda entries: (CashFlowStream(entries), entries)),
    cashflow_lists.map(lambda entries: (list(entries), entries)),
    cashflow_lists.map(lambda entries: (iter(entries), entries)),
    stream_cashflows.map(lambda entry: (entry, [entry])),
)


@given(st.lists(cashflow_sources, max_size=4))
def test_from_streams_treats_a_bare_cashflow_as_one_entry(drawn):
    """Bare CashFlow arguments join streams and iterables, contributing themselves once."""
    combined = CashFlowStream.from_streams(*(source for source, _ in drawn))

    assert type(combined) is CashFlowStream
    assert combined.entries == [entry for _, entries in drawn for entry in entries]


@given(cashflow_lists, st.lists(st.builds(Generation, STREAM_AMOUNTS, STREAM_DATES), max_size=3))
def test_from_streams_rejects_generation_streams(entries, generation):
    """A GenerationStream cannot be combined into a CashFlowStream, even when empty."""
    with pytest.raises(TypeError, match="Cannot combine CashFlowStream with GenerationStream"):
        CashFlowStream.from_streams(CashFlowStream(entries), GenerationStream(generation))


# === filtering ===


@st.composite
def filter_keywords(draw: st.DrawFn) -> tuple[dict, dict]:
    """Draw keyword filter arguments, in any spelling, with the field values they select."""
    chosen = draw(
        st.sets(st.sampled_from(["pro_forma_category", "tax_treatment", "is_cash"]), min_size=1)
    )
    arguments, selected = {}, {}
    if "pro_forma_category" in chosen:
        category = draw(PRO_FORMA_CATEGORIES)
        selected["pro_forma_category"] = category
        arguments["pro_forma_category"] = None if category is None else draw(_spellings(category))
    if "tax_treatment" in chosen:
        treatment = draw(TAX_TREATMENTS)
        selected["tax_treatment"] = treatment
        arguments["tax_treatment"] = draw(_spellings(treatment))
    if "is_cash" in chosen:
        selected["is_cash"] = arguments["is_cash"] = draw(st.booleans())
    return arguments, selected


@given(cashflow_lists, filter_keywords())
@example(_CLASSIFIED, ({"is_cash": False}, {"is_cash": False}))  # False is not "omitted"
@example(_CLASSIFIED, ({"pro_forma_category": None}, {"pro_forma_category": None}))
@example(  # criteria AND together
    _CLASSIFIED,
    (
        {"pro_forma_category": "Operating Cost", "is_cash": True},
        {"pro_forma_category": ProFormaCategory.OPERATING_COST, "is_cash": True},
    ),
)
def test_keyword_filter_keeps_flows_matching_every_criterion(entries, keywords):
    """Keyword criteria, given as enums or any user-facing spelling, keep in order exactly the
    flows matching all of them; a category of None selects uncategorized flows."""
    arguments, selected = keywords

    kept = CashFlowStream(entries).filter(**arguments)

    assert kept.entries == [
        cf for cf in entries if all(getattr(cf, name) == value for name, value in selected.items())
    ]


@given(cashflow_lists, st.functions(like=lambda cf: True, returns=st.booleans(), pure=True))
def test_predicate_filter_keeps_matching_flows_in_order(entries, predicate):
    """A predicate keeps exactly the flows it accepts, in order."""
    assert CashFlowStream(entries).filter(predicate).entries == [
        cf for cf in entries if predicate(cf)
    ]


@given(cashflow_lists, filter_keywords())
def test_filter_requires_exactly_one_of_predicate_or_keywords(entries, keywords):
    """A predicate cannot be combined with keywords, and one of them is required; is_cash=None
    counts as omitted."""
    stream = CashFlowStream(entries)

    with pytest.raises(ValueError, match="Cannot combine a callable predicate"):
        stream.filter(lambda cf: True, **keywords[0])
    with pytest.raises(ValueError, match="Provide either a callable predicate or keyword"):
        stream.filter()
    with pytest.raises(ValueError, match="Provide either a callable predicate or keyword"):
        stream.filter(is_cash=None)


@given(cashflow_lists)
@example([CashFlow(0.0, date(2028, 1, 1)), CashFlow(-0.0, date(2028, 1, 2))])
def test_sign_and_cash_selections_partition_by_field(entries):
    """inflows and outflows keep strictly positive and strictly negative amounts, so signed zeros
    are in neither; cash_only keeps the cash-basis flows."""
    stream = CashFlowStream(entries)

    assert stream.inflows().entries == [cf for cf in entries if cf.amount > 0]
    assert stream.outflows().entries == [cf for cf in entries if cf.amount < 0]
    assert stream.cash_only().entries == [cf for cf in entries if cf.is_cash]
    assert stream.cash_only().entries == stream.filter(is_cash=True).entries


BOUNDS = st.none() | STREAM_DATES
_ON_BOUNDS = [CashFlow(1.0, date(2028, 1, 1)), CashFlow(2.0, date(2028, 2, 1))]


@given(cashflow_lists, BOUNDS, BOUNDS)
@example(_ON_BOUNDS, date(2028, 1, 1), date(2028, 2, 1))  # start included, end excluded
@example(_ON_BOUNDS, date(2028, 1, 1), date(2028, 1, 2))  # a one-day interval
def test_date_range_keeps_flows_in_the_half_open_interval(entries, start, end):
    """date_range keeps, in order, the flows dated in ``[start, end)``, either bound optional;
    splitting at any date puts every flow on exactly one side."""
    assume(start is None or end is None or start < end)
    stream = CashFlowStream(entries)

    assert stream.date_range(start, end).entries == [
        cf
        for cf in entries
        if (start is None or start <= cf.date) and (end is None or cf.date < end)
    ]
    if start is not None:
        before, after = stream.date_range(end=start), stream.date_range(start=start)
        assert Counter(before.entries + after.entries) == Counter(entries)


@given(cashflow_lists, STREAM_DATES, st.integers(0, 400))
@example(_ON_BOUNDS, date(2028, 1, 1), 0)  # an empty interval on a flow's date
@example([], date(2028, 1, 1), 0)
def test_date_range_rejects_empty_or_reversed_interval(entries, end, days_after_end):
    """Two bounds must form a non-empty interval, whatever the stream holds."""
    start = end + timedelta(days=days_after_end)

    with pytest.raises(ValueError, match="end must be after start"):
        CashFlowStream(entries).date_range(start, end)


# === grouping ===


@given(cashflow_lists, FREQUENCIES)
@example([CashFlow(1.0, date(2028, 12, 31)), CashFlow(2.0, date(2029, 1, 1))], "year")
@example([CashFlow(1.0, date(2028, 2, 29)), CashFlow(2.0, date(2028, 3, 1))], "month")
@example([CashFlow(1.0, date(2028, 3, 31)), CashFlow(2.0, date(2028, 4, 1))], "quarter")
def test_group_by_period_keys_flows_by_calendar_period(entries, frequency):
    """Grouping by period keys each flow by the first day of its calendar period, keeping input
    order within groups and first-appearance order across them; group_by_period is the same."""
    expected: dict[date, list[CashFlow]] = {}
    for cf in entries:
        expected.setdefault(calendar_period_key(cf.date, frequency), []).append(cf)
    stream = CashFlowStream(entries)

    for grouped in (stream.group_by(period=frequency), stream.group_by_period(frequency)):
        assert type(grouped) is CashFlowGroup
        assert {key: group.entries for key, group in grouped.items()} == expected
        assert list(grouped) == list(expected)


@given(cashflow_lists)
@example(_CLASSIFIED)  # includes an uncategorized flow
def test_classification_grouping_keys_flows_by_field(entries):
    """Grouping by pro-forma category or tax treatment keys each flow by that field, with
    uncategorized flows under None rather than dropped."""
    stream = CashFlowStream(entries)

    for grouped, field in [
        (stream.group_by_pro_forma_category(), "pro_forma_category"),
        (stream.group_by_tax_treatment(), "tax_treatment"),
    ]:
        assert type(grouped) is CashFlowGroup
        by_key = stream.group_by(attrgetter(field))
        assert {key: group.entries for key, group in grouped.items()} == {
            key: group.entries for key, group in by_key.items()
        }


@given(cashflow_lists, FREQUENCIES, st.text())
@example([], "month", "week")
def test_group_by_rejects_invalid_selectors(entries, frequency, unknown):
    """Exactly one of a key function or a period is required, and the period must be known."""
    assume(unknown not in {"day", "month", "quarter", "year"})
    stream = CashFlowStream(entries)

    with pytest.raises(ValueError, match="Provide exactly one of 'fn' or 'period'"):
        stream.group_by()
    with pytest.raises(ValueError, match="Provide exactly one of 'fn' or 'period'"):
        stream.group_by(lambda cf: cf.label, period=frequency)
    if entries:
        with pytest.raises(AssertionError):
            stream.group_by(period=unknown)


# === sorting, scaling, and totals ===


@given(cashflow_lists, st.booleans())
def test_default_sort_orders_by_date(entries, descending):
    """sort() orders stably by date, so same-day flows keep their input order either way."""
    expected = sorted(entries, key=attrgetter("date"), reverse=descending)

    assert CashFlowStream(entries).sort(descending=descending).entries == expected


@given(cashflow_lists, st.sampled_from(CASHFLOW_SORT_ATTRS), st.booleans())
def test_sort_by_named_attribute(entries, attr, descending):
    """sort(attr=...) orders stably by each allowed CashFlow attribute."""
    expected = sorted(entries, key=attrgetter(attr), reverse=descending)

    assert CashFlowStream(entries).sort(attr=attr, descending=descending).entries == expected


@given(cashflow_lists, st.text())
@example([], "amount_mwh")  # a Generation attribute name
@example([], "is_cash")
def test_sort_rejects_other_attributes(entries, attr):
    """Attribute names outside the allowed set are rejected, as is combining one with a key."""
    assume(attr not in CASHFLOW_SORT_ATTRS)
    stream = CashFlowStream(entries)

    with pytest.raises(AssertionError, match="Unexpected sort attribute"):
        stream.sort(attr=attr)
    with pytest.raises(ValueError, match="Cannot pass both"):
        stream.sort(lambda cf: cf.date, attr="date")


@given(cashflow_lists, st.floats(min_value=-1e3, max_value=1e3))
@example(_CLASSIFIED, 0.0)
@example(_CLASSIFIED, -1.0)
def test_scale_multiplies_only_amounts(entries, factor):
    """scale multiplies every amount by the factor and leaves every other field unchanged."""
    scaled = CashFlowStream(entries).scale(factor)

    assert type(scaled) is CashFlowStream
    assert scaled.entries == [cf.replace(amount=cf.amount * factor) for cf in entries]


@given(cashflow_lists, KEYS)
@example([], lambda cf: 0)
@example(  # a small remainder under cancellation
    [
        CashFlow(1e12, date(2028, 1, 1)),
        CashFlow(0.01, date(2028, 1, 1)),
        CashFlow(-1e12, date(2028, 1, 1)),
    ],
    lambda cf: 0,
)
def test_sum_totals_amounts_and_group_sums_partition_it(entries, key):
    """sum totals cash and non-cash amounts, is exactly 0.0 when empty, and group sums are each
    group's total, adding back up to the stream's."""
    stream = CashFlowStream(entries)
    grouped = stream.group_by(key)

    assert _close(stream.sum(), math.fsum(cf.amount for cf in entries), _amount_scale(entries))
    if not entries:
        assert stream.sum() == 0.0
    assert grouped.sum() == {k: group.sum() for k, group in grouped.items()}
    assert _close(math.fsum(grouped.sum().values()), stream.sum(), _amount_scale(entries))


@given(cashflow_lists, st.none() | KEYS)
@example([CashFlow(0.0, date(2028, 1, 1)), CashFlow(-0.0, date(2028, 1, 2))], None)  # tie
def test_min_and_max_return_the_first_extreme_flow(entries, key):
    """min and max return the first flow with the extreme key, by amount unless a key is given,
    and reject an empty stream."""
    stream = CashFlowStream(entries)
    by = attrgetter("amount") if key is None else key

    if not entries:
        with pytest.raises(ValueError, match="empty CashFlowStream"):
            stream.min(key)
        with pytest.raises(ValueError, match="empty CashFlowStream"):
            stream.max(key)
        return
    keys = [by(cf) for cf in entries]
    assert stream.min(key) is entries[keys.index(min(keys))]
    assert stream.max(key) is entries[keys.index(max(keys))]


# === discounting ===


@given(cashflow_lists, RATES, STREAM_DATES, DAY_COUNT_CONVENTIONS)
@example(
    [CashFlow(100.0, date(2028, 2, 28)), CashFlow(100.0, date(2028, 3, 1))],
    0.1,
    date(2028, 1, 1),
    "actual/365-no-leap",
)
@example([CashFlow(100.0, date(2029, 1, 1))], 0.1, date(2028, 12, 1), "actual/actual")
def test_npv_discounts_each_cash_flow_by_its_year_fraction(entries, rate, valuation, dcc):
    """NPV is the sum over cash flows of amount / (1 + rate) ** years from the valuation date,
    signed so earlier flows compound forward; non-cash flows are ignored."""
    stream = CashFlowStream(entries)
    terms = [
        cf.amount / (1 + rate) ** year_fraction(valuation, cf.date, dcc)
        for cf in entries
        if cf.is_cash
    ]

    assert _close(stream.npv(rate, valuation, dcc), math.fsum(terms), math.fsum(map(abs, terms)))
    assert stream.npv(rate, valuation) == stream.npv(rate, valuation, "actual/actual")


@given(cashflow_lists, cashflow_lists, RATES, STREAM_DATES, DAY_COUNT_CONVENTIONS)
def test_npv_is_additive_and_ignores_order_and_non_cash_flows(first, second, rate, valuation, dcc):
    """The NPV of combined streams is the sum of their NPVs, whatever the entry order, and adding
    or removing non-cash flows changes nothing."""
    npv = lambda entries: CashFlowStream(entries).npv(rate, valuation, dcc)  # noqa: E731
    scale = (_amount_scale(first) + _amount_scale(second)) * 4.0

    assert _close(npv(first + second), npv(first) + npv(second), scale)
    assert _close(npv((first + second)[::-1]), npv(first + second), scale)
    assert npv(first) == CashFlowStream(first).cash_only().npv(rate, valuation, dcc)


@given(cashflow_lists, STREAM_DATES, DAY_COUNT_CONVENTIONS)
@example(  # a small remainder under cancellation survives exactly
    [
        CashFlow(1e12, date(2028, 1, 1)),
        CashFlow(0.01, date(2028, 1, 1)),
        CashFlow(-1e12, date(2028, 1, 1)),
    ],
    date(2028, 1, 1),
    "actual/actual",
)
@example([], date(2028, 1, 1), "actual/actual")
def test_npv_at_zero_rate_is_the_exact_cash_total(entries, valuation, dcc):
    """At a zero rate nothing is discounted, so NPV is the correctly rounded cash total."""
    assert CashFlowStream(entries).npv(0.0, valuation, dcc) == math.fsum(
        cf.amount for cf in entries if cf.is_cash
    )


@given(cashflow_lists, st.one_of(NON_FINITE, st.floats(max_value=-1.0)))
@example([], -1.0)
def test_npv_rejects_rates_outside_the_domain(entries, rate):
    """The rate must be finite and greater than -1, even for an empty stream."""
    with pytest.raises(ValueError, match="rate must be"):
        CashFlowStream(entries).npv(rate, date(2028, 1, 1))


@st.composite
def conventional_profiles(
    draw: st.DrawFn,
    min_offset_days: int = 365,
    multiple: st.SearchStrategy[float] = st.floats(min_value=0.5, max_value=3.0),
) -> list[CashFlow]:
    """Draw one investment followed by later inflows returning a multiple of it. A single sign
    change means NPV falls monotonically through exactly one root above -1."""
    start = draw(st.dates(min_value=date(1990, 1, 1), max_value=date(2080, 12, 31)))
    investment = draw(st.floats(min_value=1.0, max_value=1e9))
    offsets = draw(st.lists(st.integers(min_offset_days, 30 * 365), min_size=1, max_size=5))
    weights = draw(st.lists(st.floats(0.01, 1.0), min_size=len(offsets), max_size=len(offsets)))
    returned = investment * draw(multiple)
    return [CashFlow(-investment, start)] + [
        CashFlow(returned * weight / math.fsum(weights), start + timedelta(days=offset))
        for weight, offset in zip(weights, sorted(offsets), strict=True)
    ]


def _irr_residual(entries: list[CashFlow], rate: float) -> float:
    """NPV at the IRR as a fraction of total absolute cash flow, valued at the earliest date."""
    valuation = min(cf.date for cf in entries)
    return abs(CashFlowStream(entries).npv(rate, valuation)) / _amount_scale(entries)


@given(conventional_profiles(), st.floats(min_value=1e-3, max_value=1e3), cashflow_lists)
@example(  # one non-leap year apart: exactly 10%
    [CashFlow(-1000.0, date(2025, 1, 1)), CashFlow(1100.0, date(2026, 1, 1))], 1.0, []
)
@example(  # whole years apart: the root of 7000x² + 5000x - 10000
    [
        CashFlow(-10_000.0, date(2025, 1, 1)),
        CashFlow(5_000.0, date(2026, 1, 1)),
        CashFlow(7_000.0, date(2027, 1, 1)),
    ],
    1.0,
    [],
)
def test_irr_is_a_root_of_npv_that_ignores_scale_order_and_non_cash(entries, factor, noise):
    """For a conventional investment profile, the IRR zeroes NPV, and is unchanged by scaling
    every amount, by reordering entries, or by adding non-cash flows."""
    stream = CashFlowStream(entries)

    rate = stream.irr()

    assert _irr_residual(entries, rate) <= 1e-8
    assert math.isclose(stream.scale(factor).irr(), rate, rel_tol=1e-6, abs_tol=1e-9)
    assert math.isclose(CashFlowStream(entries[::-1]).irr(), rate, rel_tol=1e-6, abs_tol=1e-9)
    non_cash = [cf.replace(is_cash=False) for cf in noise]
    assert CashFlowStream(entries + non_cash).irr() == rate


@given(conventional_profiles(min_offset_days=1, multiple=st.floats(min_value=1e-6, max_value=1e6)))
@example([CashFlow(-100.0, date(2025, 1, 1)), CashFlow(1.0, date(2025, 1, 2))])  # guess at -1
def test_irr_returns_a_root_or_reports_non_convergence(entries):
    """For any conventional profile, however extreme, irr either returns a rate that zeroes NPV
    or raises ValueError; it never returns a wrong rate or fails with another error."""
    try:
        rate = CashFlowStream(entries).irr()
    except ValueError as error:
        assert "converge" in str(error)
        return

    assert _irr_residual(entries, rate) <= 1e-8


one_signed_streams = st.tuples(
    st.sampled_from([1.0, -1.0]),
    st.lists(st.tuples(st.floats(min_value=0.0, max_value=1e12), STREAM_DATES), max_size=4),
    cashflow_lists,
).map(
    lambda drawn: [CashFlow(drawn[0] * amount, day) for amount, day in drawn[1]]
    + [cf.replace(is_cash=False) for cf in drawn[2]]
)


@given(one_signed_streams)
@example([])
@example(  # the only outflow is non-cash
    [
        CashFlow(-5_000.0, date(2025, 1, 1), is_cash=False),
        CashFlow(1_000.0, date(2026, 1, 1)),
        CashFlow(1_500.0, date(2027, 1, 1)),
    ]
)
def test_irr_requires_cash_inflows_and_outflows(entries):
    """Without both a cash inflow and a cash outflow NPV has no root, whatever the non-cash
    flows, so irr raises."""
    with pytest.raises(ValueError, match="inflow|outflow"):
        CashFlowStream(entries).irr()


# === non-mutation ===


@given(cashflow_lists, FREQUENCIES)
def test_cashflow_operations_leave_the_source_unchanged(entries, frequency):
    """Every CashFlowStream-specific operation leaves the source entries unchanged."""
    stream = CashFlowStream(entries)
    snapshot = list(entries)
    operations = [
        lambda s: s.filter(lambda cf: cf.amount > 0),
        lambda s: s.filter(is_cash=True),
        lambda s: s.inflows(),
        lambda s: s.outflows(),
        lambda s: s.cash_only(),
        lambda s: s.date_range(date(2028, 1, 1), date(2028, 7, 1)),
        lambda s: s.group_by(period=frequency),
        lambda s: s.group_by_pro_forma_category(),
        lambda s: s.group_by_tax_treatment(),
        lambda s: s.sort(),
        lambda s: s.sort(attr="amount", descending=True),
        lambda s: s.scale(2.0),
        lambda s: s.npv(0.1, date(2028, 1, 1)),
    ]

    for operation in operations:
        operation(stream)
        assert stream.entries == snapshot
