# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Shared formatting helpers used across DCAF."""


def format_label(label: str, period_number: int) -> str:
    """Apply the shared ``{n}`` label templating convention.

    Examples
    --------
    >>> format_label("Year {n} Revenue", 3)
    'Year 3 Revenue'
    """
    return label.format(n=period_number) if "{n}" in label else label
