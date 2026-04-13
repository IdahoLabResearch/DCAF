"""Project timing value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta

from dcaf.shared.time import timedelta_fractional_years
from dcaf.shared.types import Period, TimingConvention


@dataclass(frozen=True)
class ProjectTimeline:
    """Project timing assumptions used by the high-level builder.

    Parameters
    ----------
    construction_start : date, optional
        Date on which construction begins.
    operations_start : date, optional
        Date on which the asset enters operation. This is used as the default
        operating start date for generation, fixed OPEX, debt service,
        depreciation, and tax incentives.
    operations_end : date, optional
        Last date of operations (inclusive). When recurring operating streams do
        not specify an explicit period count, the builder infers the modeled
        schedule from ``operations_start`` through ``operations_end`` and
        prorates any trailing partial period.

        For example, setting ``operations_start=date(2026, 1, 1)`` and
        ``operations_end=date(2027, 12, 31)`` models two full years of
        operation.
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
            if self.operations_end < self.operations_start:
                raise ValueError("operations_end must not be before operations_start")

    @property
    def operations_end_exclusive(self) -> date | None:
        """Return the exclusive operations end boundary.

        Returns
        -------
        date or None
            One day past :attr:`operations_end`, or ``None`` when
            ``operations_end`` is unset.
        """
        if self.operations_end is None:
            return None
        return self.operations_end + relativedelta(days=1)

    @property
    def operating_years(self) -> float | None:
        """Return the fractional operating life in years when both dates are set.

        The calculation uses the inclusive ``operations_end``; the effective
        exclusive boundary is one day past ``operations_end``.

        Returns
        -------
        float or None
            Fractional operating life in years, or ``None`` when either
            boundary date is missing.
        """
        if self.operations_start is None or self.operations_end_exclusive is None:
            return None
        return timedelta_fractional_years(self.operations_start, self.operations_end_exclusive)


__all__ = ["ProjectTimeline"]
