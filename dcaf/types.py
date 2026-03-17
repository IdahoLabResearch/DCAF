from enum import StrEnum
from typing import Any, Literal, Protocol


type Period = Literal["day", "month", "quarter", "year"]
type DayCountConvention = Literal["actual/365"]
type MACRSPropertyClass = Literal[3, 5, 7, 10, 15, 20]
type MACRSConvention = Literal["half-year", "mid-quarter"]
type VDBConvention = Literal["none", "half-year", "mid-quarter", "best-of-half-year-mid-quarter"]
type SpendSchedulePoint = tuple[float, float]
type SpendSchedule = tuple[SpendSchedulePoint, ...]
type InterestTreatment = Literal["capitalize", "pay"]
type SpendScheduleName = Literal["flat", "bell", "ramped", "triangle", "linear"]


class _PeriodEnum(StrEnum):
    DAY = "day"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class _InterestTreatmentEnum(StrEnum):
    CAPITALIZE = "capitalize"
    PAY = "pay"


def parse_period(period: str) -> _PeriodEnum:
    """Normalize user-facing period strings to an internal enum."""
    try:
        return _PeriodEnum(period)
    except ValueError as exc:
        raise ValueError(
            f"Unknown period '{period}'. Expected one of: 'day', 'month', 'quarter', 'year'"
        ) from exc


def parse_interest_treatment(treatment: str) -> _InterestTreatmentEnum:
    """Normalize construction interest treatment strings to an internal enum."""
    try:
        return _InterestTreatmentEnum(treatment)
    except ValueError as exc:
        raise ValueError(
            f"Unknown interest treatment '{treatment}'. "
            "Expected one of: 'capitalize', 'pay'"
        ) from exc


class SupportsLessThan(Protocol):
    def __lt__(self, __other: Any) -> bool: ...
