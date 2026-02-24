from datetime import date
from decimal import Decimal
import pytest

from dcaf import CashFlow, CashFlowGroup, CashFlowStream, CashFlowTags

@pytest.fixture()
def _create_cf_stream():
    """
    Creates a CashFlowStream instance containing a diverse
    set of cashflows on which tests can be executed.
    """
    cf1 = CashFlow(
        amount=Decimal(-500),
        date=date(2026, 1, 1),
        label="exp",
        tags=frozenset([CashFlowTags.EXPENSE, CashFlowTags.TAXABLE]),
    )
    cf2 = CashFlow(
        amount=Decimal(2000),
        date=date(2026, 1, 31),
        label="rev",
        tags=frozenset([CashFlowTags.REVENUE, CashFlowTags.TAXABLE]),
    )
    cf3 = CashFlow(
        amount=Decimal(-1000),
        date=date(2026, 4, 1),
        label="exp_2",
        tags=frozenset([CashFlowTags.EXPENSE]),
        is_cash=False,
    )
    cf4 = CashFlow(
        amount=Decimal(100),
        date=date(2026, 6, 30),
        label="rev_2",
    )
    cf_stream = CashFlowStream([cf1, cf2, cf3, cf4])
    return (cf_stream, (cf1, cf2, cf3, cf4))

def test_from_recurring_defaults():
    """Tests the CashFlowStream.from_recurring method with defaults for all optional arguments."""
    cf_stream = CashFlowStream.from_recurring(
        start = date(2026, 1, 1),
        periods = 4,
        amount = Decimal(1000)
    )
    assert len(cf_stream.flows) == 4
    expected_dates = [date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1), date(2029, 1, 1)]
    for i, flow in enumerate(cf_stream.flows):
        assert flow.date == expected_dates[i]  # Check that annual frequency is default
        assert flow.amount == Decimal(1000)  # Check that escalation is zero by default
        assert len(flow.label) > 0  # Check that some default label is set
        assert flow.is_cash is True  # Check that is_cash defaults to True
        assert len(flow.tags) == 0  # Check that no tags are added by default

def test_from_recurring_bad_frequency():
    """
    Tests that the CashFlowSTream.from_recurring method
    errors when an unacceptable frequency is provided.
    """
    with pytest.raises(ValueError):
        CashFlowStream.from_recurring(
            start = date(2026, 1, 1),
            periods = 4,
            amount = Decimal(1000),
            frequency="daily",
        )

def test_from_recurring_1():
    """
    Tests the CashFlowStream.from_recurring method with monthly frequency,
    escalation, and custom values for label, is_cash and tags.
    """
    cf_stream = CashFlowStream.from_recurring(
        start = date(2026, 3, 5),
        periods = 3,
        amount = Decimal(-200),
        frequency = "monthly",
        escalation = Decimal(0.1),
        label = "test recurring cf",
        is_cash = False,
        tags = frozenset([CashFlowTags.TAXABLE]),
    )
    expected_dates = [date(2026, 3, 5), date(2026, 4, 5), date(2026, 5, 5)]
    expected_amounts = [Decimal(-200), Decimal(-220), Decimal(-242)]
    for i, flow in enumerate(cf_stream.flows):
        assert flow.date == expected_dates[i]
        assert abs(flow.amount - expected_amounts[i]) < abs(Decimal(1e-8) * flow.amount)
        assert flow.label == "test recurring cf"
        assert flow.is_cash is False
        assert flow.tags == frozenset([CashFlowTags.TAXABLE])

def test_from_recurring_2():
    """
    Tests the CashFlowStream.from_recurring method with quarterly frequency,
    escalation, and a label that utilizes the {n} option.
    """
    cf_stream = CashFlowStream.from_recurring(
        start = date(2030, 9, 4),
        periods = 3,
        amount = Decimal(10_000),
        frequency = "quarterly",
        escalation = Decimal(0.2),
        label = "quarter #{n}",
    )
    expected_dates = [date(2030, 9, 4), date(2030, 12, 4), date(2031, 3, 4)]
    expected_amounts = [Decimal(10_000), Decimal(12_000), Decimal(14_400)]
    for i, flow in enumerate(cf_stream.flows):
        assert flow.date == expected_dates[i]
        assert abs(flow.amount - expected_amounts[i]) < abs(Decimal(1e-8) * flow.amount)
        assert flow.label == f"quarter #{i+1}"

def test_from_streams():
    """
    Tests the CashFlowStream.from_streams method, providing a CashFlow, a CashFlowStream,
    a list of CashFlow objects, and a set of CashFlow objects as arguments.
    """
    cf = CashFlow(amount=Decimal(100), date=date(2020, 1, 1))
    cf_stream_old = CashFlowStream(
        [CashFlow(amount=Decimal(-300), date=date(2023, 1, 1))]
    )
    cf_list = [
        CashFlow(amount=Decimal(200), date=date(2021, 1, 1)),
        CashFlow(amount=Decimal(-100), date=date(2022, 1, 1)),
    ]
    cf_set = {CashFlow(amount=Decimal(-200), date=date(2025, 1, 1))}

    cf_stream_new = CashFlowStream.from_streams(cf, cf_stream_old, cf_list, cf_set)
    expected_flows = {cf, cf_stream_old.flows[0], cf_list[0], cf_list[1], list(cf_set)[0]}
    assert set(cf_stream_new.flows) == expected_flows

def test_apply(_create_cf_stream):
    """Tests the CashFlowStream.apply method."""
    def _modify_cf(cf):
        return CashFlow(cf.amount*2, cf.date, cf.label, cf.is_cash, cf.tags)

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.apply(_modify_cf)
    assert cf_stream_new.flows[0].amount == -1000
    assert cf_stream_new.flows[1].amount == 4000
    assert cf_stream_old.flows[0].amount == -500  # Verifies that the original object was not modified

def test_apply_streamwise(_create_cf_stream):
    """Tests the CashFlowStream.apply_streamwise method."""
    def _modify_stream(stream):
        num_flows = len(stream.flows)
        new_flows = [
            CashFlow(
                cf.amount, cf.date, cf.label + f"_cf_{i+1}/{num_flows}", cf.is_cash, cf.tags,
            ) for i, cf in enumerate(stream.flows)
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
    """Tests the CashFlowStream.group_by_tag method."""
    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by_tag()
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
    Tests the CashFlowStream.group_by_period method with each allowed period.
    Also implicitly tests the _get_period_start method.
    """
    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by_period(period)
    expected_groups = {}
    for date_key, cf_indices in groups_by_cf_index.items():
        expected_groups[date_key] = CashFlowStream([flows[cf_i] for cf_i in cf_indices])
    assert cf_group.groups == expected_groups

def test_group_by_period_bad_period(_create_cf_stream):
    """
    Tests that the CashFlowStream.group_by_period method
    errors when an unacceptable period is provided.
    """
    cf_stream = _create_cf_stream[0]
    with pytest.raises(ValueError):
        cf_stream.group_by_period("week")

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
    assert cf_sum == Decimal(600)

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

    tol = Decimal(1e-8)
    assert abs(npv - Decimal(1592.2319017407383)) < tol

    # cf1: -500 * (1 + 0.1)^(30 / 365.25) = -503.929536591041
    # cf2: 2000 / (1 + 0.1)^0             = 2000
    # cf3: is_cash is false, so           = 0
    # cf4: 100 / (1 + 0.1)^(150 / 365.25) = 96.16143833177938
    # ------------------------------------------------------------
    # TOTAL                               = 1592.2319017407383

def test_npv_no_cashflows():
    """Tests the CashFlowStream.npv method with a stream that has no cashflows."""
    cf_stream = CashFlowStream([])
    npv = cf_stream.npv(0.1, date(2100, 1, 1))
    tol = Decimal(1e-8)
    assert abs(npv) < tol
