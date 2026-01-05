"""
``dcaf.core.depreciation``

This module contains the MACRS depreciation schedule constants
and any additional functions dealing with project depreciation.
"""

MACRS_15_YEAR: dict[int, float] = {
    0: 0.0500,
    1: 0.0950,
    2: 0.0855,
    3: 0.0770,
    4: 0.0693,
    5: 0.0623,
    6: 0.0590,
    7: 0.0590,
    8: 0.0591,
    9: 0.0590,
    10: 0.0591,
    11: 0.0590,
    12: 0.0591,
    13: 0.0590,
    14: 0.0591,
    15: 0.0295,
}

MACRS_20_YEAR: dict[int, float] = {
    0: 0.03750,
    1: 0.07219,
    2: 0.06677,
    3: 0.06177,
    4: 0.05713,
    5: 0.05285,
    6: 0.04888,
    7: 0.04522,
    8: 0.04462,
    9: 0.04461,
    10: 0.04462,
    11: 0.04461,
    12: 0.04462,
    13: 0.04461,
    14: 0.04462,
    15: 0.04461,
    16: 0.04462,
    17: 0.04461,
    18: 0.04462,
    19: 0.04461,
    20: 0.02231,
}


def build_depreciation_series(
    depreciable_basis: float,
    schedule: dict[int, float],
    n_years: int
) -> list[float]:
    """
    Build a depreciation series over n_years given a MACRS-style schedule.

    Parameters
    ----------
    depreciable_basis : float
        Total depreciable amount.
    schedule : dict[int, float]
        Mapping from year index (0-based) to depreciation fraction 
        of depreciable_basis.
    n_years : int
        Number of years to model.

    Returns
    -------
    list[float]
        Depreciation expense per year.
    """
    series = [0.0] * n_years
    for year, fraction in schedule.items():
        if 0 <= year < n_years:
            series[year] = depreciable_basis * fraction
    return series
