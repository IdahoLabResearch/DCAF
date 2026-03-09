"""
MACRS depreciation schedules for tax modeling.

Provides IRS MACRS rate tables and a factory function for generating
depreciation CashFlowStream objects.
"""

from datetime import date
from typing import assert_never

from .cashflows import CashFlow, CashFlowStream, CashFlowTags
from .types import MACRSConvention, MACRSPropertyClass


# Tables A-1 through A-5 from the provided PDF, encoded as decimals.
# Values preserve the table precision (e.g., 7.219% -> 0.07219; 6.563% -> 0.06563).
# Source: https://www.irs.gov/pub/irs-pdf/p946.pdf

MACRS_RATES: dict[int, tuple[float, ...]] = {
    # Table A-1 (Half-Year Convention)
    3: (0.3333, 0.4445, 0.1481, 0.0741),
    5: (0.2000, 0.3200, 0.1920, 0.1152, 0.1152, 0.0576),
    7: (0.1429, 0.2449, 0.1749, 0.1249, 0.0893, 0.0892, 0.0893, 0.0446),
    10: (
        0.1000, 0.1800, 0.1440, 0.1152, 0.0922, 0.0737,
        0.0655, 0.0655, 0.0656, 0.0655, 0.0328,
    ),
    15: (
        0.0500, 0.0950, 0.0855, 0.0770, 0.0693, 0.0623, 0.0590, 0.0590,
        0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0295,
    ),
    20: (
        0.03750, 0.07219, 0.06677, 0.06177, 0.05713, 0.05285, 0.04888,
        0.04522, 0.04462, 0.04461, 0.04462, 0.04461, 0.04462, 0.04461,
        0.04462, 0.04461, 0.04462, 0.04461, 0.04462, 0.04461, 0.02231,
    ),
}


MACRS_MID_QUARTER_RATES: dict[int, dict[int, tuple[float, ...]]] = {
    # Tables A-2 .. A-5 (Mid-Quarter Convention)
    3: {
        1: (0.5833, 0.2778, 0.1235, 0.0154),
        2: (0.4167, 0.3889, 0.1414, 0.0530),
        3: (0.2500, 0.5000, 0.1667, 0.0833),
        4: (0.0833, 0.6111, 0.2037, 0.1019),
    },
    5: {
        1: (0.3500, 0.2600, 0.1560, 0.1101, 0.1101, 0.0138),
        2: (0.2500, 0.3000, 0.1800, 0.1137, 0.1137, 0.0426),
        3: (0.1500, 0.3400, 0.2040, 0.1224, 0.1130, 0.0706),
        4: (0.0500, 0.3800, 0.2280, 0.1368, 0.1094, 0.0958),
    },
    7: {
        1: (0.2500, 0.2143, 0.1531, 0.1093, 0.0875, 0.0874, 0.0875, 0.0109),
        2: (0.1785, 0.2347, 0.1676, 0.1197, 0.0887, 0.0887, 0.0887, 0.0334),
        3: (0.1071, 0.2551, 0.1822, 0.1302, 0.0930, 0.0885, 0.0886, 0.0553),
        4: (0.0357, 0.2755, 0.1968, 0.1406, 0.1004, 0.0873, 0.0873, 0.0764),
    },
    10: {
        1: (
            0.1750, 0.1650, 0.1320, 0.1056, 0.0845, 0.0676,
            0.0655, 0.0655, 0.0656, 0.0655, 0.0082,
        ),
        2: (
            0.1250, 0.1750, 0.1400, 0.1120, 0.0896, 0.0717,
            0.0655, 0.0655, 0.0656, 0.0655, 0.0246,
        ),
        3: (
            0.0750, 0.1850, 0.1480, 0.1184, 0.0947, 0.0758,
            0.0655, 0.0655, 0.0656, 0.0655, 0.0410,
        ),
        4: (
            0.0250, 0.1950, 0.1560, 0.1248, 0.0998, 0.0799,
            0.0655, 0.0655, 0.0656, 0.0655, 0.0574,
        ),
    },
    15: {
        1: (
            0.0875, 0.0913, 0.0821, 0.0739, 0.0665, 0.0599, 0.0590, 0.0591,
            0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0074,
        ),
        2: (
            0.0625, 0.0938, 0.0844, 0.0759, 0.0683, 0.0615, 0.0591, 0.0590,
            0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0221,
        ),
        3: (
            0.0375, 0.0963, 0.0866, 0.0780, 0.0702, 0.0631, 0.0590, 0.0590,
            0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0369,
        ),
        4: (
            0.0125, 0.0988, 0.0889, 0.0800, 0.0720, 0.0648, 0.0590, 0.0590,
            0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0517,
        ),
    },
    20: {
        1: (
            0.06563, 0.07000, 0.06482, 0.05996, 0.05546, 0.05130, 0.04746,
            0.04459, 0.04459, 0.04459, 0.04459, 0.04460, 0.04459, 0.04460,
            0.04459, 0.04460, 0.04459, 0.04460, 0.04459, 0.04460, 0.00565,
        ),
        2: (
            0.04688, 0.07148, 0.06612, 0.06116, 0.05658, 0.05233, 0.04841,
            0.04478, 0.04463, 0.04463, 0.04463, 0.04463, 0.04463, 0.04463,
            0.04462, 0.04463, 0.04462, 0.04463, 0.04462, 0.04463, 0.01673,
        ),
        3: (
            0.02813, 0.07289, 0.06742, 0.06237, 0.05769, 0.05336, 0.04936,
            0.04566, 0.04460, 0.04460, 0.04460, 0.04460, 0.04461, 0.04460,
            0.04461, 0.04460, 0.04461, 0.04460, 0.04461, 0.04460, 0.02788,
        ),
        4: (
            0.00938, 0.07430, 0.06872, 0.06357, 0.05880, 0.05439, 0.05031,
            0.04654, 0.04458, 0.04458, 0.04458, 0.04458, 0.04458, 0.04458,
            0.04458, 0.04458, 0.04458, 0.04458, 0.04458, 0.04459, 0.03901,
        ),
    },
}

def macrs_schedule(
    cost_basis: float,
    placed_in_service: date,
    property_class: MACRSPropertyClass,
    convention: MACRSConvention = "half-year",
    label: str = "MACRS Depreciation Yr {n}",
    tags: frozenset[CashFlowTags] = frozenset(
        {CashFlowTags.DEPRECIATION, CashFlowTags.TAX_DEDUCTIBLE}
    ),
) -> CashFlowStream:
    """
    Generate a MACRS depreciation schedule.

    Parameters
    ----------
    cost_basis : float
        Depreciable basis (positive number). Flows will be negative.
    placed_in_service : date
        Date the asset is placed in service; first depreciation is on this date.
    property_class : MACRSPropertyClass
        IRS property class (3, 5, 7, 10, 15, or 20).
    convention : MACRSConvention, optional
        ``"half-year"`` (default) or ``"mid-quarter"``.  Under the
        mid-quarter convention the placed-in-service quarter is derived
        automatically from ``placed_in_service``.
    label : str, optional
        Label template.
    tags : frozenset[CashFlowTags], optional
        Tags for each flow.

    Returns
    -------
    CashFlowStream
    """
    match convention:
        case "half-year":
            rates = MACRS_RATES[property_class]
        case "mid-quarter":
            quarter = (placed_in_service.month - 1) // 3 + 1
            rates = MACRS_MID_QUARTER_RATES[property_class][quarter]
        case _:
            assert_never(convention)
    flows: list[CashFlow] = []
    for i, rate in enumerate(rates):
        dep_date = date(
            placed_in_service.year + i, placed_in_service.month, placed_in_service.day
        )
        flow_label = label.format(n=i + 1) if "{n}" in label else label
        flows.append(
            CashFlow(
                amount=-cost_basis * rate,
                date=dep_date,
                label=flow_label,
                is_cash=False,
                tags=tags,
            )
        )
    return CashFlowStream(flows)
