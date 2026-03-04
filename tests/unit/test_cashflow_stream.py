from datetime import date
import pytest

from dcaf import CashFlow, CashFlowGroup, CashFlowStream, CashFlowTags


@pytest.fixture()
def _create_cf_stream():
    """
    Creates a CashFlowStream instance containing a diverse
    set of cashflows on which tests can be executed.
    """
    cf1 = CashFlow(
        amount=-500.0,
        date=date(2026, 1, 1),
        label="exp",
        tags=frozenset([CashFlowTags.EXPENSE, CashFlowTags.TAXABLE]),
    )
    cf2 = CashFlow(
        amount=2000.0,
        date=date(2026, 1, 31),
        label="rev",
        tags=frozenset([CashFlowTags.REVENUE, CashFlowTags.TAXABLE]),
    )
    cf3 = CashFlow(
        amount=-1000.0,
        date=date(2026, 4, 1),
        label="exp_2",
        tags=frozenset([CashFlowTags.EXPENSE]),
        is_cash=False,
    )
    cf4 = CashFlow(
        amount=100.0,
        date=date(2026, 6, 30),
        label="rev_2",
    )
    cf_stream = CashFlowStream([cf1, cf2, cf3, cf4])
    return (cf_stream, (cf1, cf2, cf3, cf4))


def test_from_recurring_defaults():
    """Tests the CashFlowStream.from_recurring method with defaults for all optional arguments."""
    cf_stream = CashFlowStream.from_recurring(start=date(2026, 1, 1), periods=4, amount=1000.0)
    assert len(cf_stream.flows) == 4
    expected_dates = [date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1), date(2029, 1, 1)]
    for i, flow in enumerate(cf_stream.flows):
        assert flow.date == expected_dates[i]  # Check that annual frequency is default
        assert flow.amount == 1000.0  # Check that escalation is zero by default
        assert len(flow.label) > 0  # Check that some default label is set
        assert flow.is_cash is True  # Check that is_cash defaults to True
        assert len(flow.tags) == 0  # Check that no tags are added by default


def test_from_recurring_bad_frequency():
    """
    Tests that the CashFlowSTream.from_recurring method
    errors when an unacceptable frequency is provided.
    """
    with pytest.raises(AssertionError):
        CashFlowStream.from_recurring(
            start=date(2026, 1, 1),
            periods=4,
            amount=1000.0,
            frequency="weekly",
        )


def test_from_recurring_1():
    """
    Tests the CashFlowStream.from_recurring method with monthly frequency,
    escalation, and custom values for label, is_cash and tags.
    """
    cf_stream = CashFlowStream.from_recurring(
        start=date(2026, 3, 5),
        periods=3,
        amount=-200.0,
        frequency="month",
        escalation=0.1,
        label="test recurring cf",
        is_cash=False,
        tags=frozenset([CashFlowTags.TAXABLE]),
    )
    expected_dates = [date(2026, 3, 5), date(2026, 4, 5), date(2026, 5, 5)]
    expected_amounts = [-200.0, -220.0, -242.0]
    for i, flow in enumerate(cf_stream.flows):
        assert flow.date == expected_dates[i]
        assert abs(flow.amount - expected_amounts[i]) < abs(1e-8 * flow.amount)
        assert flow.label == "test recurring cf"
        assert flow.is_cash is False
        assert flow.tags == frozenset([CashFlowTags.TAXABLE])


def test_from_recurring_2():
    """
    Tests the CashFlowStream.from_recurring method with quarterly frequency,
    escalation, and a label that utilizes the {n} option.
    """
    cf_stream = CashFlowStream.from_recurring(
        start=date(2030, 9, 4),
        periods=3,
        amount=10_000.0,
        frequency="quarter",
        escalation=0.2,
        label="quarter #{n}",
    )
    expected_dates = [date(2030, 9, 4), date(2030, 12, 4), date(2031, 3, 4)]
    expected_amounts = [10_000.0, 12_000.0, 14_400.0]
    for i, flow in enumerate(cf_stream.flows):
        assert flow.date == expected_dates[i]
        assert abs(flow.amount - expected_amounts[i]) < abs(1e-8 * flow.amount)
        assert flow.label == f"quarter #{i + 1}"


def test_from_streams():
    """
    Tests the CashFlowStream.from_streams method, providing a CashFlow, a CashFlowStream,
    a list of CashFlow objects, and a set of CashFlow objects as arguments.
    """
    cf = CashFlow(amount=100.0, date=date(2020, 1, 1))
    cf_stream_old = CashFlowStream([CashFlow(amount=-300.0, date=date(2023, 1, 1))])
    cf_list = [
        CashFlow(amount=200.0, date=date(2021, 1, 1)),
        CashFlow(amount=-100.0, date=date(2022, 1, 1)),
    ]
    cf_set = {CashFlow(amount=-200.0, date=date(2025, 1, 1))}

    cf_stream_new = CashFlowStream.from_streams(cf, cf_stream_old, cf_list, cf_set)
    expected_flows = {cf, cf_stream_old.flows[0], cf_list[0], cf_list[1], list(cf_set)[0]}
    assert set(cf_stream_new.flows) == expected_flows


def test_apply(_create_cf_stream):
    """Tests the CashFlowStream.apply method."""

    def _modify_cf(cf):
        return CashFlow(cf.amount * 2, cf.date, cf.label, cf.is_cash, cf.tags)

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.apply(_modify_cf)
    assert cf_stream_new.flows[0].amount == -1000
    assert cf_stream_new.flows[1].amount == 4000
    assert (
        cf_stream_old.flows[0].amount == -500
    )  # Verifies that the original object was not modified


def test_apply_streamwise(_create_cf_stream):
    """Tests the CashFlowStream.apply_streamwise method."""

    def _modify_stream(stream):
        num_flows = len(stream.flows)
        new_flows = [
            CashFlow(
                cf.amount,
                cf.date,
                cf.label + f"_cf_{i + 1}/{num_flows}",
                cf.is_cash,
                cf.tags,
            )
            for i, cf in enumerate(stream.flows)
        ]
        return CashFlowStream(new_flows)

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.apply_streamwise(_modify_stream)
    assert cf_stream_new.flows[0].label == "exp_cf_1/4"
    assert cf_stream_new.flows[1].label == "rev_cf_2/4"
    assert cf_stream_new.flows[2].label == "exp_2_cf_3/4"
    assert cf_stream_new.flows[3].label == "rev_2_cf_4/4"

    # Verify that the original cashflow stream was not modified
    assert cf_stream_old.flows[0].label == "exp"


def test_filter(_create_cf_stream):
    """Tests the CashFlowStream.filter method."""

    def _predicate(cf):
        return "exp" in cf.label

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.filter(_predicate)
    # Check the amounts, not the labels, just to avoid anything weird
    assert len(cf_stream_new.flows) == 2
    assert cf_stream_new.flows[0].amount == -500
    assert cf_stream_new.flows[1].amount == -1000

    # Verify that the original cashflow stream was not modified
    assert len(cf_stream_old.flows) == 4


def test_group_by(_create_cf_stream):
    """Tests the CashFlowStream.group_by method."""

    def _grouping(cf):
        return cf.date.month >= 6

    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by(_grouping)
    assert isinstance(cf_group, CashFlowGroup)
    assert len(cf_group.groups) == 2
    assert cf_group[False].flows == [flows[0], flows[1], flows[2]]
    assert cf_group[True].flows == [flows[3]]


def test_group_by_tag(_create_cf_stream):
    """Tests the CashFlowStream.group_by(tag=True) method."""
    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by(tag=True)
    assert isinstance(cf_group, CashFlowGroup)
    assert len(cf_group.groups) == 3
    assert cf_group[CashFlowTags.EXPENSE].flows == [flows[0], flows[2]]
    assert cf_group[CashFlowTags.REVENUE].flows == [flows[1]]
    assert cf_group[CashFlowTags.TAXABLE].flows == [flows[0], flows[1]]


@pytest.mark.parametrize(
    ("period, groups_by_cf_index"),
    (
        [
            "day",
            {
                date(2026, 1, 1): [0],
                date(2026, 1, 31): [1],
                date(2026, 4, 1): [2],
                date(2026, 6, 30): [3],
            },
        ],
        [
            "month",
            {
                date(2026, 1, 1): [0, 1],
                date(2026, 4, 1): [2],
                date(2026, 6, 1): [3],
            },
        ],
        [
            "quarter",
            {
                date(2026, 1, 1): [0, 1],
                date(2026, 4, 1): [2, 3],
            },
        ],
        [
            "year",
            {
                date(2026, 1, 1): [0, 1, 2, 3],
            },
        ],
    ),
)
def test_group_by_period(_create_cf_stream, period, groups_by_cf_index):
    """
    Tests the CashFlowStream.group_by(period=...) method with each allowed period.
    Also implicitly tests the _period_start helper.
    """
    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by(period=period)
    expected_groups = {}
    for date_key, cf_indices in groups_by_cf_index.items():
        expected_groups[date_key] = CashFlowStream([flows[cf_i] for cf_i in cf_indices])
    assert cf_group.groups == expected_groups


def test_group_by_period_bad_period(_create_cf_stream):
    """
    Tests that the CashFlowStream.group_by(period=...) method
    errors when an unacceptable period is provided.
    """
    cf_stream = _create_cf_stream[0]
    with pytest.raises(AssertionError):
        cf_stream.group_by(period="week")


def test_sort(_create_cf_stream):
    """Tests the CashFlowStream.sort method."""
    cf_stream_old, flows = _create_cf_stream
    sorted_cf_stream = cf_stream_old.sort(lambda cf: abs(cf.amount))
    assert isinstance(sorted_cf_stream, CashFlowStream)
    assert sorted_cf_stream.flows == [flows[3], flows[0], flows[2], flows[1]]

    # Verify that the original cashflow stream was not modified
    assert cf_stream_old.flows == [flows[0], flows[1], flows[2], flows[3]]


def test_sum(_create_cf_stream):
    """Tests the CashFlowStream.sum method."""
    cf_stream = _create_cf_stream[0]
    cf_sum = cf_stream.sum()
    assert cf_sum == 600.0


def test_count(_create_cf_stream):
    """Tests the CashFlowStream.count method on streams with and without cashflows."""
    # Check with multiple cashflows
    cf_stream_with_flows = _create_cf_stream[0]
    assert cf_stream_with_flows.count() == 4
    # Check no cashflows
    cf_stream_no_flows = CashFlowStream([])
    assert cf_stream_no_flows.count() == 0


def test_min_default_key(_create_cf_stream):
    """Tests the CashFlowStream.min method with the default key."""
    cf_stream, flows = _create_cf_stream
    min_cf = cf_stream.min()
    assert min_cf == flows[2]


def test_min_custom_key(_create_cf_stream):
    """Tests the CashFlowStream.min method with a custom key."""
    cf_stream, flows = _create_cf_stream
    min_cf = cf_stream.min(lambda cf: cf.date)
    assert min_cf == flows[0]


def test_min_no_flows():
    """Tests that the CashFlowStream.min method errors when the stream has no cashflows."""
    cf_stream = CashFlowStream([])
    with pytest.raises(ValueError):
        cf_stream.min()


def test_max(_create_cf_stream):
    """Tests the CashFlowStream.max method with the default key."""
    cf_stream, flows = _create_cf_stream
    max_cf = cf_stream.max()
    assert max_cf == flows[1]


def test_max_custom_key(_create_cf_stream):
    """Tests the CashFlowStream.max method with a custom key."""
    cf_stream, flows = _create_cf_stream
    max_cf = cf_stream.max(lambda cf: cf.date)
    assert max_cf == flows[3]


def test_max_no_flows():
    """Tests that the CashFlowStream.max method errors when the stream has no cashflows."""
    cf_stream = CashFlowStream([])
    with pytest.raises(ValueError):
        cf_stream.max()


def test_npv_main(_create_cf_stream):
    """Tests the CashFlowStream.npv method with a stream that has cashflows using a rate of 10%."""
    cf_stream = _create_cf_stream[0]
    npv = cf_stream.npv(0.1, date(2026, 1, 31))

    tol = 1e-8
    assert abs(npv - 1592.2266217233482) < tol

    # cf1: -500 * (1 + 0.1)^(30 / 365) = -503.932238610309
    # cf2: 2000 / (1 + 0.1)^0           = 2000
    # cf3: is_cash is false, so         = 0
    # cf4: 100 / (1 + 0.1)^(150 / 365)  = 96.158860333658
    # ------------------------------------------------------------
    # TOTAL                             = 1592.2266217233482


def test_npv_default_convention(_create_cf_stream):
    """Tests that the default day count convention is actual/365."""
    cf_stream = _create_cf_stream[0]
    npv_default = cf_stream.npv(0.1, date(2026, 1, 31))
    npv_explicit = cf_stream.npv(0.1, date(2026, 1, 31), convention="actual/365")
    assert npv_default == npv_explicit


def test_npv_no_cashflows():
    """Tests the CashFlowStream.npv method with a stream that has no cashflows."""
    cf_stream = CashFlowStream([])
    npv = cf_stream.npv(0.1, date(2100, 1, 1))
    tol = 1e-8
    assert abs(npv) < tol


# ---- filter by tag / is_cash keyword tests ----


def test_filter_by_tag(_create_cf_stream):
    """Tests filter(tag=...) keyword argument."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.filter(tag=CashFlowTags.EXPENSE)
    assert result.flows == [flows[0], flows[2]]


def test_filter_by_tag_no_match(_create_cf_stream):
    """Tests filter(tag=...) when no flows have the tag."""
    cf_stream = _create_cf_stream[0]
    result = cf_stream.filter(tag=CashFlowTags.DEPRECIATION)
    assert result.flows == []


def test_filter_by_tag_empty_stream():
    """Tests filter(tag=...) on an empty stream."""
    result = CashFlowStream([]).filter(tag=CashFlowTags.REVENUE)
    assert result.flows == []


# ---- with_recurring tests ----


def test_with_recurring():
    """Tests that with_recurring appends recurring flows."""
    base = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    result = base.with_recurring(start=date(2026, 6, 1), periods=2, amount=50.0)
    assert len(result.flows) == 3
    assert result.flows[0].amount == 100.0
    assert result.flows[1].amount == 50.0
    assert result.flows[2].amount == 50.0


def test_with_recurring_chaining():
    """Tests chaining with_recurring calls."""
    result = (
        CashFlowStream([])
        .with_recurring(start=date(2026, 1, 1), periods=2, amount=10.0)
        .with_recurring(start=date(2027, 1, 1), periods=1, amount=20.0)
    )
    assert len(result.flows) == 3


def test_with_recurring_immutability():
    """Tests that with_recurring does not modify the original stream."""
    original = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    _ = original.with_recurring(start=date(2026, 6, 1), periods=3, amount=50.0)
    assert len(original.flows) == 1


# ---- append tests ----


def test_append():
    """Tests appending a single cashflow."""
    stream = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    new_flow = CashFlow(200.0, date(2026, 2, 1))
    result = stream.append(new_flow)
    assert len(result.flows) == 2
    assert result.flows[1] == new_flow


def test_append_immutability():
    """Tests that append does not modify the original stream."""
    original = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    _ = original.append(CashFlow(200.0, date(2026, 2, 1)))
    assert len(original.flows) == 1


def test_append_empty_stream():
    """Tests appending to an empty stream."""
    flow = CashFlow(100.0, date(2026, 1, 1))
    result = CashFlowStream([]).append(flow)
    assert result.flows == [flow]


# ---- extend tests ----


def test_extend_with_stream():
    """Tests extending with another CashFlowStream."""
    s1 = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    s2 = CashFlowStream([CashFlow(200.0, date(2026, 2, 1))])
    result = s1.extend(s2)
    assert len(result.flows) == 2


def test_extend_with_iterable():
    """Tests extending with a plain list of CashFlow objects."""
    s1 = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    extra = [CashFlow(200.0, date(2026, 2, 1)), CashFlow(300.0, date(2026, 3, 1))]
    result = s1.extend(extra)
    assert len(result.flows) == 3


def test_extend_immutability():
    """Tests that extend does not modify the original stream."""
    original = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    _ = original.extend(CashFlowStream([CashFlow(200.0, date(2026, 2, 1))]))
    assert len(original.flows) == 1


# ---- flat_apply tests ----


def test_flat_apply():
    """Tests flat_apply producing multiple flows per input."""
    stream = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    result = stream.flat_apply(lambda cf: [cf, CashFlow(cf.amount * 2, cf.date, "doubled")])
    assert len(result.flows) == 2
    assert result.flows[1].amount == 200.0


def test_flat_apply_filtering():
    """Tests flat_apply returning empty iterables to drop flows."""
    stream = CashFlowStream(
        [
            CashFlow(100.0, date(2026, 1, 1)),
            CashFlow(-50.0, date(2026, 2, 1)),
        ]
    )
    result = stream.flat_apply(lambda cf: [cf] if cf.amount > 0 else [])
    assert len(result.flows) == 1
    assert result.flows[0].amount == 100.0


def test_flat_apply_empty_stream():
    """Tests flat_apply on an empty stream."""
    result = CashFlowStream([]).flat_apply(lambda cf: [cf])
    assert result.flows == []


# ---- filter_apply tests ----


def test_filter_apply():
    """Tests filter_apply keeping and transforming flows."""
    stream = CashFlowStream(
        [
            CashFlow(100.0, date(2026, 1, 1)),
            CashFlow(-50.0, date(2026, 2, 1)),
        ]
    )
    result = stream.filter_apply(
        lambda cf: CashFlow(cf.amount * 2, cf.date) if cf.amount > 0 else None
    )
    assert len(result.flows) == 1
    assert result.flows[0].amount == 200.0


def test_filter_apply_all_none():
    """Tests filter_apply when all flows are dropped."""
    stream = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    result = stream.filter_apply(lambda cf: None)
    assert result.flows == []


def test_filter_apply_empty_stream():
    """Tests filter_apply on an empty stream."""
    result = CashFlowStream([]).filter_apply(lambda cf: cf)
    assert result.flows == []


# ---- inflows / outflows / cash_only tests ----


def test_inflows(_create_cf_stream):
    """Tests the inflows convenience method."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.inflows()
    assert result.flows == [flows[1], flows[3]]


def test_outflows(_create_cf_stream):
    """Tests the outflows convenience method."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.outflows()
    assert result.flows == [flows[0], flows[2]]


def test_cash_only(_create_cf_stream):
    """Tests the cash_only convenience method."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.cash_only()
    # cf3 has is_cash=False, rest are True
    assert result.flows == [flows[0], flows[1], flows[3]]


# ---- date_range tests ----


def test_date_range_both_bounds(_create_cf_stream):
    """Tests date_range with both start and end."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range(start=date(2026, 1, 31), end=date(2026, 4, 1))
    assert result.flows == [flows[1], flows[2]]


def test_date_range_start_only(_create_cf_stream):
    """Tests date_range with only start bound."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range(start=date(2026, 4, 1))
    assert result.flows == [flows[2], flows[3]]


def test_date_range_end_only(_create_cf_stream):
    """Tests date_range with only end bound."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range(end=date(2026, 1, 31))
    assert result.flows == [flows[0], flows[1]]


def test_date_range_no_bounds(_create_cf_stream):
    """Tests date_range with no bounds returns all flows."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range()
    assert result.flows == list(flows)


def test_date_range_empty_result(_create_cf_stream):
    """Tests date_range that matches nothing."""
    cf_stream = _create_cf_stream[0]
    result = cf_stream.date_range(start=date(2030, 1, 1))
    assert result.flows == []


# ---- sort_by tests ----


def test_sort_by_date(_create_cf_stream):
    """Tests sort_by with default attr='date'."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort_by()
    assert result.flows == [flows[0], flows[1], flows[2], flows[3]]


def test_sort_by_date_descending(_create_cf_stream):
    """Tests sort_by date descending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort_by(ascending=False)
    assert result.flows == [flows[3], flows[2], flows[1], flows[0]]


def test_sort_by_amount(_create_cf_stream):
    """Tests sort_by amount ascending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort_by(attr="amount")
    assert result.flows == [flows[2], flows[0], flows[3], flows[1]]


def test_sort_by_label(_create_cf_stream):
    """Tests sort_by label ascending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort_by(attr="label")
    assert result.flows == [flows[0], flows[2], flows[1], flows[3]]


def test_sort_by_immutability(_create_cf_stream):
    """Tests that sort_by does not modify the original stream."""
    cf_stream, flows = _create_cf_stream
    _ = cf_stream.sort_by(attr="amount")
    assert cf_stream.flows == list(flows)


def test_sort_by_empty_stream():
    """Tests sort_by on an empty stream."""
    result = CashFlowStream([]).sort_by()
    assert result.flows == []


def test_sort_by_default(_create_cf_stream):
    """Tests that sort_by default is equivalent to sort_by(attr='date', ascending=True)."""
    cf_stream = _create_cf_stream[0]
    assert cf_stream.sort_by().flows == cf_stream.sort_by(attr="date", ascending=True).flows


def test_sort_by_bad_attr(_create_cf_stream):
    """Tests that sort_by raises on an invalid attribute."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(AssertionError):
        cf_stream.sort_by(attr="nonexistent")


# ---- unified sort tests ----


def test_sort_bare_call_sorts_by_date(_create_cf_stream):
    """sort() with no arguments sorts by date ascending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort()
    assert result.flows == [flows[0], flows[1], flows[2], flows[3]]


def test_sort_attr_amount_descending(_create_cf_stream):
    """sort(attr='amount', descending=True) sorts by amount descending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort(attr="amount", descending=True)
    assert result.flows == [flows[1], flows[3], flows[0], flows[2]]


def test_sort_callable_descending(_create_cf_stream):
    """sort(fn, descending=True) uses the callable key in descending order."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort(lambda cf: cf.date, descending=True)
    assert result.flows == [flows[3], flows[2], flows[1], flows[0]]


def test_sort_fn_and_attr_raises(_create_cf_stream):
    """sort(fn, attr=...) raises ValueError."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(ValueError, match="Cannot pass both"):
        cf_stream.sort(lambda cf: cf.date, attr="date")
