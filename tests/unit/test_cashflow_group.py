from datetime import date
import pytest

from dcaf import CashFlow, CashFlowGroup, CashFlowTags, CashFlowStream


@pytest.fixture()
def _create_cf_grp():
    """Creates a CashFlowGroup instance on which tests can be executed."""
    cf1 = CashFlow(
        1000.0,
        date(2025, 1, 1),
        is_cash=True,
        label="cf1",
        tags=frozenset({CashFlowTags.REVENUE}),
    )
    cf2 = CashFlow(
        -2000.0,
        date(2026, 8, 1),
        is_cash=True,
        label="cf2",
    )
    cf3 = CashFlow(
        5000.0,
        date(2026, 12, 31),
        is_cash=False,
        tags=frozenset({CashFlowTags.EXPENSE}),
    )

    cf_stream_1 = CashFlowStream([cf1])
    cf_stream_2 = CashFlowStream([cf2, cf3])

    cf_grp = CashFlowGroup({"stream1": cf_stream_1, "stream2": cf_stream_2})
    return (cf_grp, (cf_stream_1, cf_stream_2), (cf1, cf2, cf3))


def test_aggregate(_create_cf_grp):
    """Tests the CashFlowGroup.aggregate method."""
    cf_group = _create_cf_grp[0]
    result_groups = cf_group.aggregate(lambda s: s.count())
    assert result_groups == {"stream1": 1, "stream2": 2}


def test_ungroup(_create_cf_grp):
    """Tests the CashFlowGroup.ungroup method."""
    cf_group = _create_cf_grp[0]
    cf1, cf2, cf3 = _create_cf_grp[2]
    result_stream = cf_group.ungroup()
    assert set(result_stream.entries) == {cf1, cf2, cf3}


def test_dict_methods(_create_cf_grp):
    """Tests the CashFlowGroup.keys, .values, and .items methods."""
    cf_group = _create_cf_grp[0]
    stream1, stream2 = _create_cf_grp[1]
    assert set(cf_group.keys()) == {"stream1", "stream2"}
    values = cf_group.values()
    assert len(values) == 2
    assert stream1 in values and stream2 in values
    assert dict(cf_group.items()) == {"stream1": stream1, "stream2": stream2}


def test_magic_methods(_create_cf_grp):
    """Tests the CashFlowGroup.__getitem__, .__len__, and .__iter__ magic methods."""
    cf_group = _create_cf_grp[0]
    stream1, stream2 = _create_cf_grp[1]
    # Test __getitem__
    assert cf_group["stream1"] == stream1
    assert cf_group["stream2"] == stream2
    # Test __len__
    assert len(cf_group) == 2
    # Test __iter__
    assert set(cf_group) == {"stream1", "stream2"}


def test_sum(_create_cf_grp):
    """Tests the CashFlowGroup.sum method."""
    cf_group = _create_cf_grp[0]
    assert cf_group.sum() == {"stream1": 1000.0, "stream2": 3000.0}


def test_count(_create_cf_grp):
    """Tests the CashFlowGroup.count method."""
    cf_group = _create_cf_grp[0]
    assert cf_group.count() == {"stream1": 1, "stream2": 2}


# ---- filter_groups tests ----


def test_filter_groups(_create_cf_grp):
    """Tests filter_groups keeps groups matching the predicate."""
    cf_group = _create_cf_grp[0]
    result = cf_group.filter_groups(lambda k, s: s.count() > 1)
    assert len(result) == 1
    assert "stream2" in result


def test_filter_groups_by_key(_create_cf_grp):
    """Tests filter_groups filtering by key value."""
    cf_group = _create_cf_grp[0]
    result = cf_group.filter_groups(lambda k, s: k == "stream1")
    assert len(result) == 1
    assert "stream1" in result


def test_filter_groups_none_match(_create_cf_grp):
    """Tests filter_groups when no groups match."""
    cf_group = _create_cf_grp[0]
    result = cf_group.filter_groups(lambda k, s: False)
    assert len(result) == 0


def test_filter_groups_all_match(_create_cf_grp):
    """Tests filter_groups when all groups match."""
    cf_group = _create_cf_grp[0]
    result = cf_group.filter_groups(lambda k, s: True)
    assert len(result) == 2
    assert result.groups == cf_group.groups


def test_filter_groups_by_sum(_create_cf_grp):
    """Tests filter_groups using aggregate-like condition on sum."""
    cf_group = _create_cf_grp[0]
    result = cf_group.filter_groups(lambda k, s: s.sum() > 0)
    assert len(result) == 2  # stream1=1000, stream2=3000, both positive
