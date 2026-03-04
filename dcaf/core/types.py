from typing import Any, Literal, Protocol


type Period = Literal["day", "month", "quarter", "year"]
type DayCountConvention = Literal["actual/365"]
type MACRSPropertyClass = Literal[3, 5, 7, 10, 15, 20]
type MACRSConvention = Literal["half-year", "mid-quarter"]


class SupportsLessThan(Protocol):
    def __lt__(self, __other: Any) -> bool: ...
