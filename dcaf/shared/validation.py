# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Shared validation helpers used across DCAF."""

from math import isfinite


def validate_finite(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is not finite (inf or NaN)."""
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")


def validate_non_negative(value: float, name: str) -> None:
    """Raise ``ValueError`` if *value* is negative or not finite."""
    validate_finite(value, name)
    if value < 0.0:
        raise ValueError(f"{name} must be non-negative")
