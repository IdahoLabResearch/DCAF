"""Project timing value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dcaf.shared.time import timedelta_fractional_years
from dcaf.shared.types import Period, TimingConvention


@dataclass(frozen=True)
class ProjectTimeline:
    """Project timing assumptions used by the high-level builder.

    All date intervals in DCAF use half-open ``[start, end)`` semantics:
    ``operations_end`` is the **exclusive** boundary (the first day after
    operations cease). This matches the convention used by ``range``,
    construction phases, and outage windows.

    Parameters
    ----------
    construction_start : date, optional
        Date on which construction begins.
    operations_start : date, optional
        Date on which the asset enters operation. This is used as the default
        operating start date for generation, fixed OPEX, debt service,
        depreciation, and tax incentives.
    operations_end : date, optional
        First date **after** operations cease (exclusive). When recurring
        operating streams do not specify an explicit period count, the builder
        infers the modeled schedule from ``operations_start`` through (but not
        including) ``operations_end`` and prorates any trailing partial period.

        For example, setting ``operations_start=date(2026, 1, 1)`` and
        ``operations_end=date(2028, 1, 1)`` models two full years of
        operation (2026 and 2027).
    frequency : Period, optional
        Default frequency for recurring operating items. Default is ``"year"``.
    """

    construction_start: date | None = None
    operations_start: date | None = None
    operations_end: date | None = None
    frequency: Period = "year"
    timing: TimingConvention = "end"

    def __post_init__(self) -> None:
        if self.operations_start is not None and self.operations_end is not None:
            if self.operations_end <= self.operations_start:
                raise ValueError("operations_end must be after operations_start")

    @property
    def operating_years(self) -> float | None:
        """Return the fractional operating life in years when both dates are set.

        Returns
        -------
        float or None
            Fractional operating life in years computed as the half-open
            ``[operations_start, operations_end)`` span, or ``None`` when
            either boundary date is missing.
        """
        if self.operations_start is None or self.operations_end is None:
            return None
        return timedelta_fractional_years(self.operations_start, self.operations_end)


__all__ = ["ProjectTimeline"]
