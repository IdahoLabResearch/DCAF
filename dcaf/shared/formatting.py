"""Shared formatting helpers used across DCAF."""


def format_label(label: str, period_number: int) -> str:
    """Apply the shared ``{n}`` label templating convention."""
    return label.format(n=period_number) if "{n}" in label else label
