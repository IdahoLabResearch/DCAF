from typing import Any, Literal, Protocol


type Period = Literal["day", "month", "quarter", "year"]
type DayCountConvention = Literal["actual/365"]


class SupportsLessThan(Protocol):
    def __lt__(self, __other: Any) -> bool: ...
