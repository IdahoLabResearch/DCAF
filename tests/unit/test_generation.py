"""Tests for Generation, GenerationStream, and GenerationGroup."""

from datetime import date

import pytest

from dcaf import CashFlowStream, CashFlowTags, Generation, GenerationGroup, GenerationStream
from dcaf.escalation import ConstantRateEscalation, IndexSeriesEscalation


def _annual_factor(start: date, end: date, rate: float) -> float:
    return (1.0 + rate) ** ((end - start).days / 365.0)


# === Generation dataclass ===


def test_generation_defaults():
    """Test Generation frozen dataclass defaults."""
    g = Generation(amount_mwh=100.0, date=date(2030, 1, 1))
    assert g.amount_mwh == 100.0
    assert g.source == ""
    assert g.carrier == "electricity"
    assert g.label == ""


def test_generation_immutable():
    """Generation is frozen."""
    g = Generation(amount_mwh=100.0, date=date(2030, 1, 1))
    with pytest.raises(AttributeError):
        g.amount_mwh = 200.0  # type: ignore[misc]


# === GenerationStream.from_capacity ===


def test_from_capacity_annual():
    """Annual capacity generation."""
    gs = GenerationStream.from_capacity(
        capacity_mw=100,
        capacity_factor=0.92,
        start=date(2030, 1, 1),
        periods=3,
        source="uprate",
    )
    assert gs.count() == 3
    expected_mwh = 100 * 0.92 * 8760
    assert abs(gs.entries[0].amount_mwh - expected_mwh) < 1e-6
    assert gs.entries[0].source == "uprate"
    assert gs.entries[0].carrier == "electricity"
    assert gs.entries[0].date == date(2030, 1, 1)
    assert gs.entries[1].date == date(2031, 1, 1)
    assert gs.entries[2].date == date(2032, 1, 1)


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
    expected_mwh = 100 * 1.0 * (8760 / 12)
    assert abs(gs.entries[0].amount_mwh - expected_mwh) < 1e-6
    assert gs.entries[1].date == date(2030, 2, 1)


def test_from_capacity_quarterly():
    """Quarterly capacity generation."""
    gs = GenerationStream.from_capacity(
        capacity_mw=50,
        capacity_factor=0.80,
        start=date(2030, 1, 1),
        periods=4,
        frequency="quarter",
        carrier="hydrogen",
    )
    assert gs.count() == 4
    expected_mwh = 50 * 0.80 * (8760 / 4)
    assert abs(gs.entries[0].amount_mwh - expected_mwh) < 1e-6
    assert gs.entries[0].carrier == "hydrogen"


def test_from_capacity_label_template():
    """Labels support {n} placeholder."""
    gs = GenerationStream.from_capacity(
        capacity_mw=100, capacity_factor=0.9, start=date(2030, 1, 1),
        periods=2, label="Year {n}",
    )
    assert gs.entries[0].label == "Year 1"
    assert gs.entries[1].label == "Year 2"


# === GenerationStream.from_streams ===


def test_from_streams_combines():
    """from_streams combines multiple GenerationStreams."""
    gs1 = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a")
    gs2 = GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 3, source="b")
    combined = GenerationStream.from_streams(gs1, gs2)
    assert combined.count() == 5


def test_from_streams_accepts_entries_and_iterables():
    """from_streams also accepts individual Generation entries and plain iterables."""
    g1 = Generation(100.0, date(2030, 1, 1), source="a")
    g2 = Generation(200.0, date(2031, 1, 1), source="b")
    combined = GenerationStream.from_streams(g1, [g2])
    assert combined.entries == [g1, g2]


def test_from_streams_rejects_other_stream_types():
    """from_streams rejects stream subclasses from other domains."""
    cashflow_stream = CashFlowStream.from_recurring(date(2030, 1, 1), 1, 100.0)
    with pytest.raises(TypeError, match="Cannot combine GenerationStream with CashFlowStream"):
        GenerationStream.from_streams(cashflow_stream)


# === with_capacity ===


def test_with_capacity_appends():
    """with_capacity appends to existing entries."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a")
    gs2 = gs.with_capacity(50, 0.8, date(2030, 1, 1), 3, source="b")
    assert gs2.count() == 5
    # Original unchanged
    assert gs.count() == 2


def test_append_generation_entry():
    """append returns a new GenerationStream with one extra entry."""
    base = GenerationStream([Generation(100.0, date(2030, 1, 1))])
    extra = Generation(200.0, date(2031, 1, 1))
    result = base.append(extra)
    assert isinstance(result, GenerationStream)
    assert result.entries == [base[0], extra]
    assert base.count() == 1


def test_extend_generation_stream():
    """extend appends entries from another stream."""
    s1 = GenerationStream([Generation(100.0, date(2030, 1, 1))])
    s2 = GenerationStream([Generation(200.0, date(2031, 1, 1))])
    result = s1.extend(s2)
    assert result.count() == 2
    assert result.entries[1] == s2[0]


def test_extend_rejects_other_stream_types():
    """extend rejects stream subclasses from other domains."""
    original = GenerationStream([Generation(100.0, date(2030, 1, 1))])
    cashflow_stream = CashFlowStream.from_recurring(date(2030, 1, 1), 1, 100.0)
    with pytest.raises(TypeError, match="Cannot combine GenerationStream with CashFlowStream"):
        original.extend(cashflow_stream)


# === filter methods ===


def test_filter_by_source():
    """Filter entries by source using filter(source=...)."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="unit_1"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 3, source="uprate"),
    )
    unit1 = gs.filter(source="unit_1")
    assert unit1.count() == 2
    assert all(e.source == "unit_1" for e in unit1.entries)


def test_filter_by_carrier():
    """Filter entries by carrier using filter(carrier=...)."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, carrier="electricity"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 2, carrier="hydrogen"),
    )
    elec = gs.filter(carrier="electricity")
    assert elec.count() == 2
    h2 = gs.filter(carrier="hydrogen")
    assert h2.count() == 2


def test_filter_generic():
    """Generic filter with a custom predicate."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 5)
    recent = gs.filter(lambda g: g.date.year >= 2033)
    assert recent.count() == 2


def test_filter_requires_criteria():
    """filter() requires either a predicate or keyword filters."""
    with pytest.raises(ValueError, match="Provide either"):
        GenerationStream().filter()


def test_filter_rejects_predicate_and_keywords():
    """filter() rejects mixing a predicate with keyword filters."""
    gs = GenerationStream([Generation(100.0, date(2030, 1, 1), source="a")])
    with pytest.raises(ValueError, match="Cannot pass both"):
        gs.filter(lambda entry: entry.amount_mwh > 0, source="a")


def test_apply_generation_stream_no_condition():
    """apply transforms all entries with no condition provided and preserves stream type."""
    gs = GenerationStream([Generation(100.0, date(2030, 1, 1), source="a")])
    result = gs.apply(
        lambda g: Generation(g.amount_mwh * 2, g.date, g.source, g.carrier, g.label)
    )
    assert isinstance(result, GenerationStream)
    assert result[0].amount_mwh == 200.0
    assert gs[0].amount_mwh == 100.0


def test_apply_generation_stream_with_condition():
    """apply transforms all entries satisfying the condition provided and preserves stream type."""
    gs = GenerationStream(
        [
            Generation(150.0, date(2030, 1, 1), source="a"),
            Generation(200.0, date(2031, 1, 1), source="b"),
        ]
    )
    result = gs.apply(
        lambda g: Generation(g.amount_mwh * 2, g.date, g.source, g.carrier, g.label),
        lambda g: g.source == "a",
    )
    assert isinstance(result, GenerationStream)
    assert result[0].amount_mwh == 300.0
    assert result[1].amount_mwh == 200.0
    assert gs[0].amount_mwh == 150.0  # Check that the initial stream is unmodified


def test_apply_streamwise_generation_stream():
    """apply_streamwise transforms the entire GenerationStream at once."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
    result = gs.apply_streamwise(lambda stream: stream[1:])
    assert isinstance(result, GenerationStream)
    assert result.count() == 2
    assert [entry.date.year for entry in result] == [2031, 2032]


def test_filter_apply_generation_stream():
    """filter_apply can both transform and drop generation entries."""
    gs = GenerationStream([
        Generation(100.0, date(2030, 1, 1)),
        Generation(0.0, date(2031, 1, 1)),
    ])
    result = gs.filter_apply(
        lambda g: Generation(g.amount_mwh * 1.5, g.date, g.source, g.carrier, g.label)
        if g.amount_mwh > 0
        else None
    )
    assert result.count() == 1
    assert result[0].amount_mwh == 150.0


def test_date_range_generation_stream():
    """date_range filters generation entries by inclusive bounds."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 4)
    result = gs.date_range(start=date(2031, 1, 1), end=date(2032, 1, 1))
    assert result.count() == 2
    assert [entry.date for entry in result] == [date(2031, 1, 1), date(2032, 1, 1)]


# === grouping ===


def test_group_by_source():
    """Group by source returns correct groups."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 3, source="b"),
    )
    groups = gs.group_by(source=True)
    assert isinstance(groups, GenerationGroup)
    assert len(groups) == 2
    assert groups["a"].count() == 2
    assert groups["b"].count() == 3


def test_group_by_carrier():
    """Group by carrier."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, carrier="elec"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 1, carrier="h2"),
    )
    groups = gs.group_by(carrier=True)
    assert len(groups) == 2


def test_group_by_period():
    """Group by year period using group_by(period=...)."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
    groups = gs.group_by(period="year")
    assert len(groups) == 3


def test_sort_generation_stream_default():
    """sort() defaults to date ascending."""
    gs = GenerationStream([
        Generation(100.0, date(2032, 1, 1)),
        Generation(100.0, date(2030, 1, 1)),
        Generation(100.0, date(2031, 1, 1)),
    ])
    result = gs.sort()
    assert [entry.date for entry in result] == [
        date(2030, 1, 1),
        date(2031, 1, 1),
        date(2032, 1, 1),
    ]


def test_sort_generation_stream_by_attr():
    """sort(attr=...) sorts by a named Generation attribute."""
    gs = GenerationStream([
        Generation(300.0, date(2030, 1, 1), source="c"),
        Generation(100.0, date(2031, 1, 1), source="a"),
        Generation(200.0, date(2032, 1, 1), source="b"),
    ])
    result = gs.sort(attr="amount_mwh", descending=True)
    assert [entry.amount_mwh for entry in result] == [300.0, 200.0, 100.0]


# === aggregation ===


def test_sum():
    """Sum of MWh across all entries."""
    gs = GenerationStream.from_capacity(100, 0.92, date(2030, 1, 1), 2)
    total = gs.sum()
    expected = 2 * 100 * 0.92 * 8760
    assert abs(total - expected) < 1e-6


def test_count():
    """Count of entries."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 5)
    assert gs.count() == 5


def test_len_dunder():
    """``len(stream)`` returns the number of generation entries."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 5)
    assert len(gs) == 5
    assert len(GenerationStream()) == 0


def test_iter_dunder():
    """Iterating a GenerationStream yields entries in order."""
    entries = [
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1100.0, date(2031, 1, 1)),
    ]
    gs = GenerationStream(entries)
    assert list(gs) == entries


def test_getitem_int_dunder():
    """Integer indexing returns a single Generation entry."""
    entries = [
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1100.0, date(2031, 1, 1)),
        Generation(1200.0, date(2032, 1, 1)),
    ]
    gs = GenerationStream(entries)
    assert gs[0] == entries[0]
    assert gs[2] == entries[2]


def test_getitem_slice_dunder():
    """Slice indexing returns a GenerationStream with the selected entries."""
    entries = [
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1100.0, date(2031, 1, 1)),
        Generation(1200.0, date(2032, 1, 1)),
    ]
    gs = GenerationStream(entries)
    result = gs[1:]
    assert isinstance(result, GenerationStream)
    assert result.entries == entries[1:]


def test_truthiness_follows_length():
    """Empty generation streams are falsy and non-empty streams are truthy."""
    assert bool(GenerationStream([Generation(1000.0, date(2030, 1, 1))])) is True
    assert bool(GenerationStream()) is False


def test_sum_empty():
    """Sum of empty stream is 0."""
    assert GenerationStream().sum() == 0.0


def test_count_empty():
    """Count of empty stream is 0."""
    assert GenerationStream().count() == 0


# === discounted_sum ===


def test_discounted_sum():
    """Discounted sum applies discount factors."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
    ])
    ds = gs.discounted_sum(rate=0.10, valuation_date=date(2030, 1, 1))
    # First entry: 1000 / (1.1)^0 = 1000
    # Second entry: 1000 / (1.1)^1 ≈ 909.09
    assert ds < 2000.0
    assert ds > 1900.0


def test_discounted_sum_zero_rate():
    """At zero rate, discounted sum equals plain sum."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3)
    assert abs(gs.discounted_sum(0.0, date(2030, 1, 1)) - gs.sum()) < 1e-6


def test_discounted_sum_uses_constant_rate_escalation_for_discounting():
    """Discounted sum matches evaluation through the shared constant-rate policy."""
    valuation_date = date(2030, 1, 1)
    gs = GenerationStream([
        Generation(1000.0, date(2029, 1, 1)),
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
    ])
    policy = ConstantRateEscalation(valuation_date, rate=0.10, day_count_convention="actual/365")

    expected = sum(entry.amount_mwh / policy.factor(entry.date) for entry in gs.entries)

    assert gs.discounted_sum(rate=0.10, valuation_date=valuation_date) == pytest.approx(expected)


# === to_revenue ===


def test_to_revenue_basic():
    """Convert generation to revenue cashflows."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
    ])
    cfs = gs.to_revenue(price_per_mwh=50.0)
    assert cfs.count() == 2
    assert abs(cfs.entries[0].amount - 50_000.0) < 1e-8
    assert cfs.entries[0].is_cash is True
    assert cfs.entries[0].has_tag(CashFlowTags.REVENUE)
    assert cfs.entries[0].has_tag(CashFlowTags.TAXABLE)


def test_to_revenue_escalation():
    """Revenue price escalates annually."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
        Generation(1000.0, date(2032, 1, 1)),
    ])
    cfs = gs.to_revenue(price_per_mwh=50.0, escalation=0.10)
    assert abs(cfs.entries[0].amount - 50_000.0) < 1e-8
    assert abs(cfs.entries[1].amount - 55_000.0) < 1e-8
    assert abs(cfs.entries[2].amount - 60_500.0) < 1e-6


def test_to_revenue_escalation_uses_entry_dates():
    """Revenue escalation uses exact entry dates rather than integer year steps."""
    reference_date = date(2030, 6, 1)
    gs = GenerationStream([
        Generation(1000.0, reference_date),
        Generation(1000.0, date(2031, 1, 1)),
        Generation(1000.0, date(2031, 6, 1)),
    ])
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
    gs = GenerationStream([
        Generation(1000.0, date(2030, 7, 1)),
        Generation(1000.0, date(2031, 7, 1)),
    ])
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
    assert cfs.entries[0].has_tag(CashFlowTags.EXPENSE)


def test_to_cost_escalation():
    """Cost rate escalates annually."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
    ])
    cfs = gs.to_cost(rate_per_mwh=10.0, escalation=0.05)
    assert abs(cfs.entries[0].amount - (-10_000.0)) < 1e-8
    assert abs(cfs.entries[1].amount - (-10_500.0)) < 1e-6


def test_to_cost_supports_explicit_nonannual_escalation_period():
    """Cost escalation period can be specified independently of entry cadence."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2030, 2, 1)),
        Generation(1000.0, date(2030, 3, 1)),
    ])
    cfs = gs.to_cost(rate_per_mwh=10.0, escalation=0.02, escalation_period="month")
    expected_amounts = [-10_000.0, -10_200.0, -10_404.0]
    for i, flow in enumerate(cfs.entries):
        assert flow.amount == pytest.approx(expected_amounts[i])


def test_to_cost_supports_index_series_escalation_policy():
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 15)),
        Generation(1000.0, date(2030, 2, 15)),
        Generation(1000.0, date(2030, 3, 15)),
    ])
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


def test_generation_group_aggregate():
    """Aggregate works on GenerationGroup."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 2, source="b"),
    )
    groups = gs.group_by(source=True)
    sums = groups.aggregate(lambda s: s.sum())
    assert "a" in sums
    assert "b" in sums
    assert sums["a"] > sums["b"]


def test_generation_group_sum():
    """Sum convenience method on GenerationGroup."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 1, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 1, source="b"),
    )
    groups = gs.group_by(source=True)
    sums = groups.sum()
    assert abs(sums["a"] - 100 * 0.9 * 8760) < 1e-6


def test_generation_group_count():
    """Count convenience method on GenerationGroup."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 3, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 2, source="b"),
    )
    groups = gs.group_by(source=True)
    counts = groups.count()
    assert counts["a"] == 3
    assert counts["b"] == 2


def test_generation_group_getitem():
    """Bracket access works."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="x"),
    )
    groups = gs.group_by(source=True)
    assert groups["x"].count() == 2


def test_generation_group_len():
    """len() returns number of groups."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 1, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 1, source="b"),
    )
    assert len(gs.group_by(source=True)) == 2


def test_generation_group_apply_to_groups():
    """apply_to_groups transforms selected grouped streams."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 1, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 1, source="b"),
    )
    grouped = gs.group_by(source=True)
    result = grouped.apply_to_groups(
        lambda s: s.apply(
            lambda g: Generation(g.amount_mwh * 2, g.date, g.source, g.carrier, g.label)
        ),
        keys="a",
    )
    assert result["a"].sum() == grouped["a"].sum() * 2
    assert result["b"].sum() == grouped["b"].sum()


def test_generation_group_filter_groups():
    """filter_groups keeps only groups matching the predicate."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 1, source="b"),
    )
    grouped = gs.group_by(source=True)
    result = grouped.filter_groups(lambda key, stream: stream.count() > 1)
    assert list(result.keys()) == ["a"]


def test_generation_group_ungroup():
    """ungroup flattens grouped generation streams back to one stream."""
    gs = GenerationStream.from_streams(
        GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a"),
        GenerationStream.from_capacity(50, 0.8, date(2030, 1, 1), 1, source="b"),
    )
    grouped = gs.group_by(source=True)
    result = grouped.ungroup()
    assert isinstance(result, GenerationStream)
    assert result.count() == gs.count()
