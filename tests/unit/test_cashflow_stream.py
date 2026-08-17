# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from datetime import date
import pytest

from dcaf.shared.types import ProFormaCategory, TaxTreatment
from dcaf.shared.time import PeriodTruncationWarning, elapsed_periods
from dcaf.streams import CashFlow, CashFlowGroup, CashFlowStream, GenerationStream
from dcaf.finance.escalation import ConstantRateEscalation, EscalationBuilder, IndexSeriesEscalation


def _annual_factor(start: date, end: date, rate: float) -> float:
    return (1.0 + rate) ** ((end - start).days / 365.0)


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
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        tax_treatment=TaxTreatment.TAXABLE,
    )
    cf2 = CashFlow(
        amount=2000.0,
        date=date(2026, 1, 31),
        label="rev",
        pro_forma_category=ProFormaCategory.REVENUE,
        tax_treatment=TaxTreatment.TAXABLE,
    )
    cf3 = CashFlow(
        amount=-1000.0,
        date=date(2026, 4, 1),
        label="exp_2",
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        is_cash=False,
    )
    cf4 = CashFlow(
        amount=100.0,
        date=date(2026, 6, 30),
        label="rev_2",
    )
    cf_stream = CashFlowStream([cf1, cf2, cf3, cf4])
    return (cf_stream, [cf1, cf2, cf3, cf4])


def test_from_recurring_defaults():
    """Tests the CashFlowStream.from_recurring method with defaults for all optional arguments."""
    cf_stream = CashFlowStream.from_recurring(start=date(2026, 1, 1), periods=4, amount=1000.0)
    assert len(cf_stream.entries) == 4
    expected_dates = [
        date(2026, 12, 31),
        date(2027, 12, 31),
        date(2028, 12, 31),
        date(2029, 12, 31),
    ]
    for i, flow in enumerate(cf_stream.entries):
        assert flow.date == expected_dates[i]  # Check that annual frequency is default
        assert flow.amount == 1000.0  # Check that escalation is zero by default
        assert len(flow.label) > 0  # Check that some default label is set
        assert flow.is_cash is True  # Check that is_cash defaults to True
        assert flow.pro_forma_category is ProFormaCategory.OTHER
        assert flow.tax_treatment is TaxTreatment.NONE


def test_from_recurring_supports_timing_conventions():
    begin = CashFlowStream.from_recurring(
        start=date(2026, 1, 1),
        periods=1,
        amount=1000.0,
        timing="begin",
    )
    middle = CashFlowStream.from_recurring(
        start=date(2026, 1, 1),
        periods=1,
        amount=1000.0,
        timing="middle",
    )
    end = CashFlowStream.from_recurring(
        start=date(2026, 1, 1),
        periods=1,
        amount=1000.0,
    )

    assert begin.entries[0].date == date(2026, 1, 1)
    assert middle.entries[0].date == date(2026, 7, 2)
    assert end.entries[0].date == date(2026, 12, 31)


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


def test_from_recurring_fractional_period_prorates_complete_days_and_warns():
    with pytest.warns(PeriodTruncationWarning, match="last included date is 2026-01-15"):
        cf_stream = CashFlowStream.from_recurring(
            start=date(2026, 1, 1),
            periods=0.5,
            amount=3100.0,
            frequency="month",
        )

    assert cf_stream.count() == 1
    assert cf_stream.entries[0].date == date(2026, 1, 15)
    assert cf_stream.entries[0].amount == pytest.approx(1500.0)


def test_from_recurring_annual_escalation_is_date_based():
    """Annual escalation is evaluated against payment dates, not recurrence count."""
    cf_stream = CashFlowStream.from_recurring(
        start=date(2026, 3, 5),
        periods=3,
        amount=-200.0,
        frequency="month",
        escalation=0.1,
        label="test recurring cf",
        is_cash=False,
        tax_treatment=TaxTreatment.TAXABLE,
    )
    expected_dates = [date(2026, 4, 4), date(2026, 5, 4), date(2026, 6, 4)]
    expected_amounts = [
        -200.0 * _annual_factor(date(2026, 3, 5), flow_date, 0.1) for flow_date in expected_dates
    ]
    for i, flow in enumerate(cf_stream.entries):
        assert flow.date == expected_dates[i]
        assert flow.amount == pytest.approx(expected_amounts[i])
        assert flow.label == "test recurring cf"
        assert flow.is_cash is False
        assert flow.pro_forma_category is ProFormaCategory.OTHER
        assert flow.tax_treatment is TaxTreatment.TAXABLE


def test_from_recurring_supports_explicit_nonannual_escalation_period():
    """Non-annual escalation periods can be specified independently of frequency."""
    cf_stream = CashFlowStream.from_recurring(
        start=date(2030, 9, 4),
        periods=3,
        amount=10_000.0,
        frequency="quarter",
        escalation=0.2,
        label="quarterly payment",
        escalation_period="quarter",
    )
    expected_dates = [date(2030, 12, 3), date(2031, 3, 3), date(2031, 6, 3)]
    expected_amounts = [
        10_000.0 * (1.2 ** elapsed_periods(date(2030, 9, 4), flow_date, "quarter"))
        for flow_date in expected_dates
    ]
    for i, flow in enumerate(cf_stream.entries):
        assert flow.date == expected_dates[i]
        assert flow.amount == pytest.approx(expected_amounts[i])
        assert flow.label == "quarterly payment"


def test_from_recurring_annual_escalation_supports_daily_frequency():
    """Daily recurring streams still treat bare escalation as annual by default."""
    start = date(2026, 1, 1)
    cf_stream = CashFlowStream.from_recurring(
        start=start,
        periods=3,
        amount=100.0,
        frequency="day",
        escalation=0.1,
    )
    expected_dates = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    expected_amounts = [
        100.0 * _annual_factor(start, flow_date, 0.1) for flow_date in expected_dates
    ]
    for i, flow in enumerate(cf_stream.entries):
        assert flow.date == expected_dates[i]
        assert flow.amount == pytest.approx(expected_amounts[i])


def test_from_recurring_supports_earlier_amount_reference_date():
    """Recurring amounts can be escalated from an earlier known-value date."""
    reference_date = date(2026, 1, 1)
    cf_stream = CashFlowStream.from_recurring(
        start=date(2026, 7, 1),
        periods=2,
        amount=100.0,
        frequency="month",
        escalation=0.12,
        amount_reference_date=reference_date,
    )
    expected_dates = [date(2026, 7, 31), date(2026, 8, 31)]
    expected_amounts = [
        100.0 * _annual_factor(reference_date, flow_date, 0.12) for flow_date in expected_dates
    ]
    for i, flow in enumerate(cf_stream.entries):
        assert flow.date == expected_dates[i]
        assert flow.amount == pytest.approx(expected_amounts[i])


def test_from_recurring_supports_escalation_policy_parity_with_constant_rate():
    policy = ConstantRateEscalation(reference_date=date(2026, 1, 1), rate=0.12)
    simple = CashFlowStream.from_recurring(
        start=date(2026, 7, 1),
        periods=2,
        amount=100.0,
        frequency="month",
        escalation=0.12,
        amount_reference_date=date(2026, 1, 1),
    )
    advanced = CashFlowStream.from_recurring(
        start=date(2026, 7, 1),
        periods=2,
        amount=100.0,
        frequency="month",
        escalation_policy=policy,
    )

    assert [flow.date for flow in advanced.entries] == [flow.date for flow in simple.entries]
    assert [flow.amount for flow in advanced.entries] == pytest.approx(
        [flow.amount for flow in simple.entries]
    )


def test_from_recurring_supports_index_series_escalation_policy():
    policy = IndexSeriesEscalation(
        reference_date=date(2026, 1, 1),
        points=(
            (date(2026, 1, 1), 100.0),
            (date(2026, 2, 1), 103.0),
            (date(2026, 3, 1), 106.09),
        ),
    )
    cf_stream = CashFlowStream.from_recurring(
        start=date(2026, 1, 15),
        periods=3,
        amount=100.0,
        frequency="month",
        escalation_policy=policy,
    )

    assert [flow.amount for flow in cf_stream.entries] == pytest.approx([103.0, 106.09, 106.09])


def test_from_recurring_rejects_mixed_simple_and_policy_inputs():
    policy = ConstantRateEscalation(reference_date=date(2026, 1, 1), rate=0.02)

    with pytest.raises(ValueError, match="cannot be combined"):
        CashFlowStream.from_recurring(
            start=date(2026, 1, 1),
            periods=2,
            amount=100.0,
            escalation=0.02,
            escalation_policy=policy,
        )


def test_from_recurring_rejects_escalation_builder_override():
    builder = EscalationBuilder(reference_date=date(2026, 1, 1)).constant_rate(0.02)

    with pytest.raises(TypeError, match="call \\.build\\(\\) first"):
        CashFlowStream.from_recurring(
            start=date(2026, 1, 1),
            periods=2,
            amount=100.0,
            escalation_policy=builder,
        )


def test_from_streams_preserves_order_and_duplicates():
    """from_streams concatenates mixed input forms without removing duplicates."""
    first = CashFlow(100.0, date(2026, 1, 1), label="first")
    duplicate = CashFlow(200.0, date(2026, 2, 1), label="duplicate")
    last = CashFlow(300.0, date(2026, 3, 1), label="last")

    result = CashFlowStream.from_streams(
        [first, duplicate],
        CashFlowStream([duplicate]),
        last,
    )

    assert result.entries == [first, duplicate, duplicate, last]


def test_from_streams_rejects_other_stream_types():
    """from_streams rejects stream subclasses from other domains."""
    generation_stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 1)
    with pytest.raises(TypeError, match="Cannot combine CashFlowStream with GenerationStream"):
        CashFlowStream.from_streams(generation_stream)


def test_apply_no_condition(_create_cf_stream):
    """Tests the CashFlowStream.apply method with no condition."""

    def _modify_cf(cf):
        return cf.replace(amount=cf.amount * 2)

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.apply(_modify_cf)
    assert isinstance(cf_stream_new, CashFlowStream)
    assert cf_stream_new[0].amount == -1000
    assert cf_stream_new[1].amount == 4000
    assert cf_stream_new[2].amount == -2000
    assert cf_stream_new[3].amount == 200
    assert cf_stream_old[0].amount == -500  # Verifies that the original object was not modified


def test_apply_with_condition(_create_cf_stream):
    """Tests that CashFlowStream.apply method with a condition."""

    def _modify_cf(cf):
        return cf.replace(amount=cf.amount * 2)

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.apply(_modify_cf, lambda cf: "exp" in cf.label)
    assert isinstance(cf_stream_new, CashFlowStream)
    assert len(cf_stream_new) == 4
    assert cf_stream_new[0].amount == -1000  # Modified
    assert cf_stream_new[1].amount == 2000
    assert cf_stream_new[2].amount == -2000  # Modified
    assert cf_stream_new[3].amount == 100
    assert cf_stream_old[0].amount == -500  # Verifies that the original object was not modified


def test_apply_preserves_output_ordering(_create_cf_stream):
    """apply() is one-to-one and preserves input order in its output."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.apply(lambda cf: cf.replace(amount=cf.amount * 2))
    assert [flow.date for flow in result.entries] == [flow.date for flow in flows]


def test_apply_streamwise(_create_cf_stream):
    """Tests the CashFlowStream.apply_streamwise method."""

    def _modify_stream(stream):
        num_flows = len(stream.entries)
        new_flows = [
            CashFlow(
                cf.amount,
                cf.date,
                cf.label + f"_cf_{i + 1}/{num_flows}",
                cf.is_cash,
                cf.pro_forma_category,
                cf.tax_treatment,
            )
            for i, cf in enumerate(stream.entries)
        ]
        return CashFlowStream(new_flows)

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.apply_streamwise(_modify_stream)
    assert cf_stream_new.entries[0].label == "exp_cf_1/4"
    assert cf_stream_new.entries[1].label == "rev_cf_2/4"
    assert cf_stream_new.entries[2].label == "exp_2_cf_3/4"
    assert cf_stream_new.entries[3].label == "rev_2_cf_4/4"

    # Verify that the original cashflow stream was not modified
    assert cf_stream_old.entries[0].label == "exp"


def test_filter(_create_cf_stream):
    """Tests the CashFlowStream.filter method."""

    def _predicate(cf):
        return "exp" in cf.label

    cf_stream_old = _create_cf_stream[0]
    cf_stream_new = cf_stream_old.filter(_predicate)
    # Check the amounts, not the labels, just to avoid anything weird
    assert len(cf_stream_new.entries) == 2
    assert cf_stream_new.entries[0].amount == -500
    assert cf_stream_new.entries[1].amount == -1000

    # Verify that the original cashflow stream was not modified
    assert len(cf_stream_old.entries) == 4


def test_group_by(_create_cf_stream):
    """Tests the CashFlowStream.group_by method."""

    def _grouping(cf):
        return cf.date.month >= 6

    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by(_grouping)
    assert isinstance(cf_group, CashFlowGroup)
    assert len(cf_group.groups) == 2
    assert cf_group[False].entries == [flows[0], flows[1], flows[2]]
    assert cf_group[True].entries == [flows[3]]


def test_group_by_no_selector_raises(_create_cf_stream):
    """group_by() with neither a callable nor a period raises ValueError."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(ValueError, match="Provide exactly one of 'fn' or 'period'"):
        cf_stream.group_by()


def test_group_by_both_selectors_raises(_create_cf_stream):
    """group_by() with both a callable and a period raises ValueError."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(ValueError, match="Provide exactly one of 'fn' or 'period'"):
        cf_stream.group_by(lambda cf: cf.date.month, period="month")


def test_group_by_period_matches_group_by_period_kwarg(_create_cf_stream):
    """group_by_period(period) produces the same groups as group_by(period=period)."""
    cf_stream = _create_cf_stream[0]
    via_helper = cf_stream.group_by_period("month")
    via_kwarg = cf_stream.group_by(period="month")
    assert via_helper.groups.keys() == via_kwarg.groups.keys()
    for key in via_helper.groups:
        assert via_helper[key].entries == via_kwarg[key].entries


def test_group_by_preserves_total_count_and_duplicates(_create_cf_stream):
    """Grouping preserves the total entry count and all duplicate entries."""
    _, flows = _create_cf_stream
    duplicate = flows[0]
    stream = CashFlowStream([flows[0], duplicate, flows[1], flows[2], flows[3]])

    cf_group = stream.group_by(lambda cf: cf.pro_forma_category)

    grouped_entries = [entry for entries in cf_group.groups.values() for entry in entries.entries]
    assert len(grouped_entries) == len(stream.entries)
    assert grouped_entries.count(duplicate) == 2


def test_group_by_pro_forma_category(_create_cf_stream):
    """Tests grouping by pro-forma category."""
    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by_pro_forma_category()
    assert isinstance(cf_group, CashFlowGroup)
    assert len(cf_group.groups) == 3
    assert cf_group[ProFormaCategory.OPERATING_COST].entries == [flows[0], flows[2]]
    assert cf_group[ProFormaCategory.REVENUE].entries == [flows[1]]
    assert cf_group[ProFormaCategory.OTHER].entries == [flows[3]]


def test_group_by_tax_treatment(_create_cf_stream):
    """Tests grouping by tax treatment."""
    cf_stream, flows = _create_cf_stream
    cf_group = cf_stream.group_by_tax_treatment()
    assert isinstance(cf_group, CashFlowGroup)
    assert len(cf_group.groups) == 2
    assert cf_group[TaxTreatment.TAXABLE].entries == [flows[0], flows[1]]
    assert cf_group[TaxTreatment.NONE].entries == [flows[2], flows[3]]


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
    assert sorted_cf_stream.entries == [flows[3], flows[0], flows[2], flows[1]]

    # Verify that the original cashflow stream was not modified
    assert cf_stream_old.entries == flows


def test_sort_stable_for_equal_keys():
    """Sorting is stable: entries with equal keys keep their relative order."""
    cf_a = CashFlow(amount=100.0, date=date(2026, 1, 1), label="a")
    cf_b = CashFlow(amount=200.0, date=date(2026, 1, 1), label="b")
    cf_c = CashFlow(amount=300.0, date=date(2026, 1, 1), label="c")
    stream = CashFlowStream([cf_b, cf_a, cf_c])

    result = stream.sort(lambda cf: cf.date)

    assert result.entries == [cf_b, cf_a, cf_c]


def test_scale_changes_only_amount(_create_cf_stream):
    """scale() changes only amount and preserves all other fields."""
    cf_stream, flows = _create_cf_stream
    scaled = cf_stream.scale(1.5)
    for original, scaled_flow in zip(flows, scaled.entries):
        assert scaled_flow.amount == pytest.approx(original.amount * 1.5)
        assert scaled_flow.date == original.date
        assert scaled_flow.label == original.label
        assert scaled_flow.is_cash == original.is_cash
        assert scaled_flow.pro_forma_category == original.pro_forma_category
        assert scaled_flow.tax_treatment == original.tax_treatment


@pytest.mark.parametrize(
    ("factor", "expected_multiplier"),
    [
        (1, 1),
        (0, 0),
        (-2, -2),
    ],
)
def test_scale_identity_zero_and_sign_reversal(_create_cf_stream, factor, expected_multiplier):
    """Scaling by 1, 0, and a negative factor covers identity, zero, and sign reversal."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.scale(factor)
    for original, scaled_flow in zip(flows, result.entries):
        assert scaled_flow.amount == pytest.approx(original.amount * expected_multiplier)


def test_scale(_create_cf_stream):
    """Tests the CashFlowStream.scale method."""
    cf_stream, flows = _create_cf_stream
    scaled_cf_stream = cf_stream.scale(1.5)
    assert abs(scaled_cf_stream.entries[0].amount - (-750)) < 1e-8
    assert abs(scaled_cf_stream.entries[1].amount - 3000) < 1e-8

    # Verify that the original cashflow stream was not modified
    assert cf_stream.entries == flows


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


def test_len_dunder(_create_cf_stream):
    """``len(stream)`` returns the number of cashflows."""
    cf_stream_with_flows = _create_cf_stream[0]
    assert len(cf_stream_with_flows) == 4
    assert len(CashFlowStream([])) == 0


def test_iter_dunder(_create_cf_stream):
    """Iterating a CashFlowStream yields its cashflows in order."""
    cf_stream, flows = _create_cf_stream
    assert list(cf_stream) == list(flows)


def test_getitem_int_dunder(_create_cf_stream):
    """Integer indexing returns a single CashFlow."""
    cf_stream, flows = _create_cf_stream
    assert cf_stream[0] == flows[0]
    assert cf_stream[2] == flows[2]


def test_getitem_slice_dunder(_create_cf_stream):
    """Slice indexing returns a CashFlowStream with the selected entries."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream[1:3]
    assert isinstance(result, CashFlowStream)
    assert result.entries == [flows[1], flows[2]]


def test_getitem_negative_index_matches_list(_create_cf_stream):
    """Negative indices match ordinary Python list indexing."""
    cf_stream, flows = _create_cf_stream
    assert cf_stream[-1] == flows[-1]
    assert cf_stream[-2] == flows[-2]


def test_getitem_stepped_slice_matches_list(_create_cf_stream):
    """Stepped slices match ordinary Python list slicing."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream[::2]
    assert isinstance(result, CashFlowStream)
    assert result.entries == flows[::2]


def test_getitem_reversed_slice_matches_list(_create_cf_stream):
    """Reversed slices match ordinary Python list slicing."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream[::-1]
    assert isinstance(result, CashFlowStream)
    assert result.entries == flows[::-1]


def test_truthiness_follows_length(_create_cf_stream):
    """Empty streams are falsy and non-empty streams are truthy."""
    assert bool(_create_cf_stream[0]) is True
    assert bool(CashFlowStream([])) is False


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
    """Tests that the default day count convention is actual/actual."""
    cf_stream = _create_cf_stream[0]
    npv_default = cf_stream.npv(0.1, date(2026, 1, 31))
    npv_explicit = cf_stream.npv(0.1, date(2026, 1, 31), convention="actual/actual")
    assert npv_default == npv_explicit


def test_npv_uses_constant_rate_escalation_for_discounting(_create_cf_stream):
    """NPV matches evaluation through the shared constant-rate policy."""
    cf_stream = _create_cf_stream[0]
    valuation_date = date(2026, 1, 31)
    policy = ConstantRateEscalation(
        valuation_date,
        rate=0.1,
        day_count_convention="actual/actual",
    )

    expected = sum(
        flow.amount / policy.factor(flow.date) for flow in cf_stream.entries if flow.is_cash
    )

    assert cf_stream.npv(0.1, valuation_date) == pytest.approx(expected)


def test_npv_no_cashflows():
    """Tests the CashFlowStream.npv method with a stream that has no cashflows."""
    cf_stream = CashFlowStream([])
    npv = cf_stream.npv(0.1, date(2100, 1, 1))
    tol = 1e-8
    assert abs(npv) < tol


def test_npv_preserves_small_remainder_under_cancellation():
    """At zero rate, exact offsetting amounts leave the original small cashflow."""
    valuation_date = date(2026, 1, 1)
    stream = CashFlowStream(
        [
            CashFlow(1_000_000_000_000.0, valuation_date),
            CashFlow(0.01, valuation_date),
            CashFlow(-1_000_000_000_000.0, valuation_date),
        ]
    )

    assert stream.npv(rate=0.0, valuation_date=valuation_date) == 0.01


def test_npv_rejects_rate_at_minus_one():
    """The stream wrapper enforces the shared real-valued rate domain."""
    valuation_date = date(2026, 1, 1)
    stream = CashFlowStream([CashFlow(100.0, date(2026, 7, 1))])

    with pytest.raises(ValueError, match="rate must be greater than -1.0"):
        stream.npv(rate=-1.0, valuation_date=valuation_date)


# ---- filter by classification / is_cash keyword tests ----


def test_filter_by_pro_forma_category(_create_cf_stream):
    """Tests filter(pro_forma_category=...) keyword argument."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.filter(pro_forma_category=ProFormaCategory.OPERATING_COST)
    assert result.entries == [flows[0], flows[2]]


def test_filter_by_pro_forma_category_no_match(_create_cf_stream):
    """Tests category filtering when no flows match."""
    cf_stream = _create_cf_stream[0]
    result = cf_stream.filter(pro_forma_category=ProFormaCategory.DEPRECIATION)
    assert result.entries == []


def test_filter_by_pro_forma_category_empty_stream():
    """Tests category filtering on an empty stream."""
    result = CashFlowStream([]).filter(pro_forma_category=ProFormaCategory.REVENUE)
    assert result.entries == []


def test_filter_no_predicate_or_kwargs_raises(_create_cf_stream):
    """filter() with neither a callable predicate nor keyword criteria raises ValueError."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(ValueError, match="Provide either a callable predicate or keyword"):
        cf_stream.filter()


def test_filter_predicate_and_kwargs_raises(_create_cf_stream):
    """filter() with both a callable predicate and keyword criteria raises ValueError."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(ValueError, match="Cannot combine a callable predicate"):
        cf_stream.filter(lambda cf: True, is_cash=True)


def test_filter_multiple_keywords_use_and_semantics():
    """Multiple keyword criteria are combined with AND, not OR."""
    matches_both = CashFlow(
        amount=100.0,
        date=date(2026, 1, 1),
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        is_cash=True,
    )
    matches_category_only = CashFlow(
        amount=200.0,
        date=date(2026, 2, 1),
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        is_cash=False,
    )
    matches_is_cash_only = CashFlow(
        amount=300.0,
        date=date(2026, 3, 1),
        pro_forma_category=ProFormaCategory.REVENUE,
        is_cash=True,
    )
    stream = CashFlowStream([matches_both, matches_category_only, matches_is_cash_only])

    result = stream.filter(pro_forma_category=ProFormaCategory.OPERATING_COST, is_cash=True)

    # If AND were accidentally changed to OR, all three entries would match.
    assert result.entries == [matches_both]


def test_filter_is_cash_false_selects_non_cash_entries():
    """is_cash=False must select non-cash entries, not be treated as an omitted argument."""
    cash_flow = CashFlow(amount=100.0, date=date(2026, 1, 1), is_cash=True)
    non_cash_flow = CashFlow(amount=200.0, date=date(2026, 2, 1), is_cash=False)
    stream = CashFlowStream([cash_flow, non_cash_flow])

    result = stream.filter(is_cash=False)

    # A bug that treats `False` as "not provided" would return both entries.
    assert result.entries == [non_cash_flow]


def test_filter_pro_forma_category_none_selects_uncategorized():
    """pro_forma_category=None selects only entries with no pro-forma category."""
    categorized = CashFlow(
        amount=100.0,
        date=date(2026, 1, 1),
        pro_forma_category=ProFormaCategory.REVENUE,
    )
    uncategorized = CashFlow(amount=200.0, date=date(2026, 2, 1), pro_forma_category=None)
    stream = CashFlowStream([categorized, uncategorized])

    result = stream.filter(pro_forma_category=None)

    assert result.entries == [uncategorized]


def test_filter_string_classification_normalized_consistently():
    """String pro_forma_category/tax_treatment inputs are normalized like enum inputs."""
    flow = CashFlow(
        amount=100.0,
        date=date(2026, 1, 1),
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        tax_treatment=TaxTreatment.DEDUCTIBLE,
    )
    other_flow = CashFlow(
        amount=200.0,
        date=date(2026, 2, 1),
        pro_forma_category=ProFormaCategory.REVENUE,
        tax_treatment=TaxTreatment.TAXABLE,
    )
    stream = CashFlowStream([flow, other_flow])

    enum_result = stream.filter(
        pro_forma_category=ProFormaCategory.OPERATING_COST,
        tax_treatment=TaxTreatment.DEDUCTIBLE,
    )
    string_result = stream.filter(
        pro_forma_category="Operating Cost",
        tax_treatment="DEDUCTIBLE",
    )

    assert string_result.entries == enum_result.entries == [flow]


# ---- non-mutation sweep ----


@pytest.mark.parametrize(
    ("name", "op"),
    [
        ("filter_predicate", lambda s: s.filter(lambda cf: cf.amount > 0)),
        ("filter_kwargs", lambda s: s.filter(is_cash=True)),
        ("group_by", lambda s: s.group_by(lambda cf: cf.label)),
        ("group_by_period", lambda s: s.group_by(period="month")),
        ("group_by_pro_forma_category", lambda s: s.group_by_pro_forma_category()),
        ("group_by_tax_treatment", lambda s: s.group_by_tax_treatment()),
        ("sort", lambda s: s.sort(lambda cf: cf.amount)),
        ("scale", lambda s: s.scale(2.0)),
        ("apply", lambda s: s.apply(lambda cf: cf.replace(amount=cf.amount * 2))),
        ("flat_apply", lambda s: s.flat_apply(lambda cf: [cf, cf])),
        ("filter_apply", lambda s: s.filter_apply(lambda cf: cf if cf.amount > 0 else None)),
        ("date_range", lambda s: s.date_range(start=date(2026, 2, 1))),
        ("inflows", lambda s: s.inflows()),
        ("outflows", lambda s: s.outflows()),
        ("cash_only", lambda s: s.cash_only()),
        ("append", lambda s: s.append(CashFlow(999.0, date(2026, 12, 31)))),
        ("extend", lambda s: s.extend([CashFlow(999.0, date(2026, 12, 31))])),
        ("getitem_slice", lambda s: s[1:3]),
    ],
)
def test_non_mutating_methods_leave_original_unchanged(_create_cf_stream, name, op):
    """Every non-mutating method leaves the original entry sequence unchanged."""
    cf_stream, flows = _create_cf_stream
    original_entries = list(cf_stream.entries)

    op(cf_stream)

    assert cf_stream.entries == original_entries
    assert cf_stream.entries == flows


# ---- append tests ----


def test_append():
    """Tests appending a single cashflow."""
    stream = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    new_flow = CashFlow(200.0, date(2026, 2, 1))
    result = stream.append(new_flow)
    assert len(result.entries) == 2
    assert result.entries[1] == new_flow


def test_append_immutability():
    """Tests that append does not modify the original stream."""
    original = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    _ = original.append(CashFlow(200.0, date(2026, 2, 1)))
    assert len(original.entries) == 1


def test_append_empty_stream():
    """Tests appending to an empty stream."""
    flow = CashFlow(100.0, date(2026, 1, 1))
    result = CashFlowStream([]).append(flow)
    assert result.entries == [flow]


# ---- extend tests ----


def test_extend_with_stream():
    """Tests extending with another CashFlowStream."""
    s1 = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    s2 = CashFlowStream([CashFlow(200.0, date(2026, 2, 1))])
    result = s1.extend(s2)
    assert len(result.entries) == 2


def test_extend_with_iterable():
    """Tests extending with a plain list of CashFlow objects."""
    s1 = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    extra = [CashFlow(200.0, date(2026, 2, 1)), CashFlow(300.0, date(2026, 3, 1))]
    result = s1.extend(extra)
    assert len(result.entries) == 3


def test_extend_immutability():
    """Tests that extend does not modify the original stream."""
    original = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    _ = original.extend(CashFlowStream([CashFlow(200.0, date(2026, 2, 1))]))
    assert len(original.entries) == 1


def test_extend_rejects_other_stream_types():
    """extend rejects stream subclasses from other domains."""
    original = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    generation_stream = GenerationStream.from_capacity(100, 0.9, date(2030, 1, 1), 1)
    with pytest.raises(TypeError, match="Cannot combine CashFlowStream with GenerationStream"):
        original.extend(generation_stream)


# ---- flat_apply tests ----


def test_flat_apply():
    """Tests flat_apply producing multiple flows per input."""
    stream = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    result = stream.flat_apply(lambda cf: [cf, CashFlow(cf.amount * 2, cf.date, "doubled")])
    assert len(result.entries) == 2
    assert result.entries[1].amount == 200.0


def test_flat_apply_filtering():
    """Tests flat_apply returning empty iterables to drop flows."""
    stream = CashFlowStream(
        [
            CashFlow(100.0, date(2026, 1, 1)),
            CashFlow(-50.0, date(2026, 2, 1)),
        ]
    )
    result = stream.flat_apply(lambda cf: [cf] if cf.amount > 0 else [])
    assert len(result.entries) == 1
    assert result.entries[0].amount == 100.0


def test_flat_apply_empty_stream():
    """Tests flat_apply on an empty stream."""
    result = CashFlowStream([]).flat_apply(lambda cf: [cf])
    assert result.entries == []


def test_flat_apply_preserves_output_ordering():
    """flat_apply() emits entries in order: per-input in order, each input's outputs in order."""
    cf1 = CashFlow(100.0, date(2026, 1, 1), label="a")
    cf2 = CashFlow(200.0, date(2026, 2, 1), label="b")
    stream = CashFlowStream([cf1, cf2])

    def _split(cf):
        return [
            cf.replace(label=f"{cf.label}_1"),
            cf.replace(label=f"{cf.label}_2"),
        ]

    result = stream.flat_apply(_split)
    assert [flow.label for flow in result.entries] == ["a_1", "a_2", "b_1", "b_2"]


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
    assert len(result.entries) == 1
    assert result.entries[0].amount == 200.0


def test_filter_apply_all_none():
    """Tests filter_apply when all flows are dropped."""
    stream = CashFlowStream([CashFlow(100.0, date(2026, 1, 1))])
    result = stream.filter_apply(lambda cf: None)
    assert result.entries == []


def test_filter_apply_preserves_output_ordering():
    """filter_apply() preserves the relative order of surviving entries."""
    cf1 = CashFlow(100.0, date(2026, 1, 1), label="a")
    cf2 = CashFlow(-50.0, date(2026, 2, 1), label="b")
    cf3 = CashFlow(300.0, date(2026, 3, 1), label="c")
    stream = CashFlowStream([cf1, cf2, cf3])

    result = stream.filter_apply(lambda cf: cf if cf.amount > 0 else None)

    assert [flow.label for flow in result.entries] == ["a", "c"]


def test_filter_apply_empty_stream():
    """Tests filter_apply on an empty stream."""
    result = CashFlowStream([]).filter_apply(lambda cf: cf)
    assert result.entries == []


# ---- inflows / outflows / cash_only tests ----


def test_inflows(_create_cf_stream):
    """Tests the inflows convenience method."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.inflows()
    assert result.entries == [flows[1], flows[3]]


def test_outflows(_create_cf_stream):
    """Tests the outflows convenience method."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.outflows()
    assert result.entries == [flows[0], flows[2]]


def test_cash_only(_create_cf_stream):
    """Tests the cash_only convenience method."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.cash_only()
    # cf3 has is_cash=False, rest are True
    assert result.entries == [flows[0], flows[1], flows[3]]


def test_inflows_and_outflows_exclude_zero_amount():
    """A zero-valued cashflow is in neither inflows() nor outflows()."""
    positive = CashFlow(amount=100.0, date=date(2026, 1, 1))
    zero = CashFlow(amount=0.0, date=date(2026, 2, 1))
    negative = CashFlow(amount=-100.0, date=date(2026, 3, 1))
    stream = CashFlowStream([positive, zero, negative])

    assert stream.inflows().entries == [positive]
    assert stream.outflows().entries == [negative]


# ---- date_range tests ----


def test_date_range_both_bounds(_create_cf_stream):
    """Tests date_range with both start (inclusive) and end (exclusive)."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range(start=date(2026, 1, 31), end=date(2026, 4, 2))
    assert result.entries == [flows[1], flows[2]]


def test_date_range_start_only(_create_cf_stream):
    """Tests date_range with only start bound."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range(start=date(2026, 4, 1))
    assert result.entries == [flows[2], flows[3]]


def test_date_range_end_only(_create_cf_stream):
    """Tests date_range with only end bound (exclusive)."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range(end=date(2026, 2, 1))
    assert result.entries == [flows[0], flows[1]]


def test_date_range_no_bounds(_create_cf_stream):
    """Tests date_range with no bounds returns all flows."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.date_range()
    assert result.entries == list(flows)


def test_date_range_empty_result(_create_cf_stream):
    """Tests date_range that matches nothing."""
    cf_stream = _create_cf_stream[0]
    result = cf_stream.date_range(start=date(2030, 1, 1))
    assert result.entries == []


def test_date_range_includes_start_excludes_end():
    """date_range(start, end) includes an entry exactly on start and excludes one exactly on end."""
    on_start = CashFlow(amount=100.0, date=date(2026, 1, 1))
    on_end = CashFlow(amount=200.0, date=date(2026, 2, 1))
    stream = CashFlowStream([on_start, on_end])

    result = stream.date_range(start=date(2026, 1, 1), end=date(2026, 2, 1))

    assert result.entries == [on_start]


def test_date_range_same_start_and_end_is_empty(_create_cf_stream):
    """date_range(start, start) returns an empty stream."""
    cf_stream = _create_cf_stream[0]
    result = cf_stream.date_range(start=date(2026, 1, 1), end=date(2026, 1, 1))
    assert result.entries == []


def test_sort_attr_amount_ascending(_create_cf_stream):
    """sort(attr='amount') sorts by amount ascending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort(attr="amount")
    assert result.entries == [flows[2], flows[0], flows[3], flows[1]]


def test_sort_attr_label(_create_cf_stream):
    """sort(attr='label') sorts by label ascending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort(attr="label")
    assert result.entries == [flows[0], flows[2], flows[1], flows[3]]


def test_sort_immutability(_create_cf_stream):
    """sort() does not modify the original stream."""
    cf_stream, flows = _create_cf_stream
    _ = cf_stream.sort(attr="amount")
    assert cf_stream.entries == list(flows)


def test_sort_empty_stream():
    """sort() on an empty stream returns an empty stream."""
    result = CashFlowStream([]).sort()
    assert result.entries == []


def test_sort_bad_attr(_create_cf_stream):
    """sort(attr=...) raises on an invalid attribute."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(AssertionError):
        cf_stream.sort(attr="nonexistent")


# ---- unified sort tests ----


def test_sort_bare_call_sorts_by_date(_create_cf_stream):
    """sort() with no arguments sorts by date ascending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort()
    assert result.entries == [flows[0], flows[1], flows[2], flows[3]]


def test_sort_default_attribute_descending(_create_cf_stream):
    """sort(descending=True) sorts by the default date attribute descending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort(descending=True)
    assert result.entries == [flows[3], flows[2], flows[1], flows[0]]


def test_sort_attr_amount_descending(_create_cf_stream):
    """sort(attr='amount', descending=True) sorts by amount descending."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort(attr="amount", descending=True)
    assert result.entries == [flows[1], flows[3], flows[0], flows[2]]


def test_sort_callable_descending(_create_cf_stream):
    """sort(fn, descending=True) uses the callable key in descending order."""
    cf_stream, flows = _create_cf_stream
    result = cf_stream.sort(lambda cf: cf.date, descending=True)
    assert result.entries == [flows[3], flows[2], flows[1], flows[0]]


def test_sort_fn_and_attr_raises(_create_cf_stream):
    """sort(fn, attr=...) raises ValueError."""
    cf_stream = _create_cf_stream[0]
    with pytest.raises(ValueError, match="Cannot pass both"):
        cf_stream.sort(lambda cf: cf.date, attr="date")


# ---- irr tests ----


def test_irr_main():
    """IRR of invest $1000, receive $1100 after exactly one non-leap year equals 10%.

    2025 is not a leap year: 2025-01-01 → 2026-01-01 = 365 days → t = 365/365 = 1.0 exactly.
    NPV = -1000 + 1100/(1+r) = 0 → r = 0.1 exactly.
    """
    stream = CashFlowStream(
        [
            CashFlow(-1000.0, date(2025, 1, 1)),
            CashFlow(1100.0, date(2026, 1, 1)),
        ]
    )
    assert stream.irr() == pytest.approx(0.1, abs=1e-8)


def test_irr_multi_cashflow():
    """IRR of a 3-cashflow project: NPV must be zero at the computed rate.

    Uses two back-to-back non-leap years so time fractions are 1.0 and 2.0 exactly,
    making the polynomial root analytically verifiable via the quadratic formula.
    """
    # 2025 and 2026 are both non-leap years: t₂=1.0, t₃=2.0 exactly
    stream = CashFlowStream(
        [
            CashFlow(-10_000.0, date(2025, 1, 1)),
            CashFlow(5_000.0, date(2026, 1, 1)),
            CashFlow(7_000.0, date(2027, 1, 1)),
        ]
    )
    irr = stream.irr()
    # Verify by evaluating NPV at the returned rate
    ref_date = date(2025, 1, 1)
    assert stream.npv(irr, ref_date) == pytest.approx(0.0, abs=1e-6)
    # Quadratic solution: x = 1/(1+r), 7000x²+5000x−10000=0 → r = 0.12321245...
    # x = (−5000 + sqrt(305_000_000)) / 14_000
    assert irr == pytest.approx(0.12321245982864881, abs=1e-8)


def test_irr_convention_default():
    """Default 'actual/actual' convention produces the same result as the explicit argument."""
    stream = CashFlowStream(
        [
            CashFlow(-5_000.0, date(2025, 3, 1)),
            CashFlow(2_000.0, date(2026, 3, 1)),
            CashFlow(4_500.0, date(2027, 3, 1)),
        ]
    )
    assert stream.irr() == stream.irr(convention="actual/actual")


def test_irr_npv_is_zero_at_irr():
    """stream.npv(stream.irr(), ref_date) ≈ 0 for a multi-year project."""
    stream = CashFlowStream(
        [
            CashFlow(-50_000.0, date(2025, 1, 1)),
            CashFlow(15_000.0, date(2026, 1, 1)),
            CashFlow(20_000.0, date(2027, 1, 1)),
            CashFlow(25_000.0, date(2028, 1, 1)),
        ]
    )
    irr = stream.irr()
    assert stream.npv(irr, date(2025, 1, 1)) == pytest.approx(0.0, abs=1e-6)


def test_irr_initial_guess_at_domain_floor_raises_non_convergence():
    """An underflowed centroid guess should not divide by zero at r = -1."""
    stream = CashFlowStream(
        [
            CashFlow(-100.0, date(2025, 1, 1)),
            CashFlow(1.0, date(2025, 1, 2)),
        ]
    )

    with pytest.raises(ValueError, match="did not converge"):
        stream.irr()


def test_irr_no_inflows():
    """Stream with no positive cashflows raises ValueError."""
    stream = CashFlowStream(
        [
            CashFlow(-1_000.0, date(2025, 1, 1)),
            CashFlow(-500.0, date(2026, 1, 1)),
        ]
    )
    with pytest.raises(ValueError, match="inflow"):
        stream.irr()


def test_irr_no_outflows():
    """Stream with no negative cashflows raises ValueError."""
    stream = CashFlowStream(
        [
            CashFlow(1_000.0, date(2025, 1, 1)),
            CashFlow(500.0, date(2026, 1, 1)),
        ]
    )
    with pytest.raises(ValueError, match="outflow"):
        stream.irr()


def test_irr_empty():
    """Empty stream raises ValueError (no inflows or outflows)."""
    with pytest.raises(ValueError):
        CashFlowStream([]).irr()


def test_irr_excludes_non_cash():
    """Non-cash flows (is_cash=False) are excluded from the IRR calculation.

    The stream's only outflow is non-cash; cash-only view is all inflows,
    so irr() must raise ValueError rather than computing a spurious rate.
    """
    stream = CashFlowStream(
        [
            CashFlow(-5_000.0, date(2025, 1, 1), is_cash=False),
            CashFlow(1_000.0, date(2026, 1, 1)),
            CashFlow(1_500.0, date(2027, 1, 1)),
        ]
    )
    with pytest.raises(ValueError):
        stream.irr()
