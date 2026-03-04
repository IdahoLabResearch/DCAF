"""Tests for Generation, GenerationStream, and GenerationGroup."""

from datetime import date

import pytest

from dcaf import Generation, GenerationStream, GenerationGroup, CashFlowTags


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


# === with_capacity ===


def test_with_capacity_appends():
    """with_capacity appends to existing entries."""
    gs = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 2, source="a")
    gs2 = gs.with_capacity(50, 0.8, date(2030, 1, 1), 3, source="b")
    assert gs2.count() == 5
    # Original unchanged
    assert gs.count() == 2


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


# === to_revenue ===


def test_to_revenue_basic():
    """Convert generation to revenue cashflows."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
    ])
    cfs = gs.to_revenue(price_per_mwh=50.0)
    assert cfs.count() == 2
    assert abs(cfs.flows[0].amount - 50_000.0) < 1e-8
    assert cfs.flows[0].is_cash is True
    assert cfs.flows[0].has_tag(CashFlowTags.REVENUE)
    assert cfs.flows[0].has_tag(CashFlowTags.TAXABLE)


def test_to_revenue_escalation():
    """Revenue price escalates annually."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
        Generation(1000.0, date(2032, 1, 1)),
    ])
    cfs = gs.to_revenue(price_per_mwh=50.0, escalation=0.10)
    assert abs(cfs.flows[0].amount - 50_000.0) < 1e-8
    assert abs(cfs.flows[1].amount - 55_000.0) < 1e-8
    assert abs(cfs.flows[2].amount - 60_500.0) < 1e-6


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
    assert abs(cfs.flows[0].amount - (-5_000.0)) < 1e-8
    assert cfs.flows[0].has_tag(CashFlowTags.EXPENSE)


def test_to_cost_escalation():
    """Cost rate escalates annually."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
    ])
    cfs = gs.to_cost(rate_per_mwh=10.0, escalation=0.05)
    assert abs(cfs.flows[0].amount - (-10_000.0)) < 1e-8
    assert abs(cfs.flows[1].amount - (-10_500.0)) < 1e-6


# === to_ptc ===


def test_to_ptc_basic():
    """PTC applies only within the eligibility window."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
        Generation(1000.0, date(2032, 1, 1)),
        Generation(1000.0, date(2033, 1, 1)),
    ])
    cfs = gs.to_ptc(rate_per_mwh=27.5, years=2)
    # Only years 2030, 2031 (< 2032)
    assert cfs.count() == 2
    assert abs(cfs.flows[0].amount - 27_500.0) < 1e-8


def test_to_ptc_escalation():
    """PTC rate escalates."""
    gs = GenerationStream([
        Generation(1000.0, date(2030, 1, 1)),
        Generation(1000.0, date(2031, 1, 1)),
    ])
    cfs = gs.to_ptc(rate_per_mwh=10.0, years=5, escalation=0.02)
    assert abs(cfs.flows[0].amount - 10_000.0) < 1e-8
    assert abs(cfs.flows[1].amount - 10_200.0) < 1e-6


def test_to_ptc_empty():
    """Empty generation produces empty PTC stream."""
    cfs = GenerationStream().to_ptc(rate_per_mwh=27.5, years=10)
    assert cfs.count() == 0


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
