# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Shared validation helpers used across DCAF."""

from datetime import date, datetime
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


def validate_capacity_factor(value: float, name: str) -> None:
    """Raise ``ValueError`` unless *value* is finite and lies in ``[0, 1]``."""
    if not isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def validate_date(value: object, name: str) -> None:
    """Raise ``TypeError`` unless *value* is a plain ``date``.

    ``datetime`` is a ``date`` subclass, so it is rejected explicitly: DCAF resolves time to
    whole days and has no sub-daily concept.
    """
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.date, not {type(value).__name__}")
