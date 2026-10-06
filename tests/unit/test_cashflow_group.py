# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
from datetime import date
import pytest

from dcaf.shared.types import ProFormaCategory
from dcaf.streams import CashFlow, CashFlowGroup, CashFlowStream


@pytest.fixture()
def _create_cf_grp():
    """Creates a CashFlowGroup instance on which tests can be executed."""
    cf1 = CashFlow(
        1000.0,
        date(2025, 1, 1),
        is_cash=True,
        label="cf1",
        pro_forma_category=ProFormaCategory.REVENUE,
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
        pro_forma_category=ProFormaCategory.OPERATING_COST,
    )

    cf_stream_1 = CashFlowStream([cf1])
    cf_stream_2 = CashFlowStream([cf2, cf3])

    cf_grp = CashFlowGroup({"stream1": cf_stream_1, "stream2": cf_stream_2})
    return (cf_grp, (cf_stream_1, cf_stream_2), (cf1, cf2, cf3))


def test_sum(_create_cf_grp):
    """Tests the CashFlowGroup.sum method."""
    cf_group = _create_cf_grp[0]
    assert cf_group.sum() == {"stream1": 1000.0, "stream2": 3000.0}
