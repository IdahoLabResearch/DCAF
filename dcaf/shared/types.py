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


class ProFormaCategory(StrEnum):
    REVENUE = "revenue"
    OPERATING_COST = "operating_cost"
    CAPITAL_COST = "capital_cost"
    TAX = "tax"
    TAX_CREDIT = "tax_credit"
    DEPRECIATION = "depreciation"
    FINANCING_INTEREST = "financing_interest"
    FINANCING_PRINCIPAL = "financing_principal"
    OTHER = "other"


class TaxTreatment(StrEnum):
    NONE = "none"
    TAXABLE = "taxable"
    DEDUCTIBLE = "deductible"


class _PeriodEnum(StrEnum):
    DAY = "day"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class _InterestTreatmentEnum(StrEnum):
    CAPITALIZE = "capitalize"
    PAY = "pay"


def _normalize_enum_value(value: str) -> str:
    """Normalize user-facing enum strings for permissive parsing."""
    return value.strip().lower().replace(" ", "_").replace("-", "_")


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
            f"Unknown interest treatment '{treatment}'. Expected one of: 'capitalize', 'pay'"
        ) from exc


def parse_pro_forma_category(
    category: ProFormaCategory | str,
) -> ProFormaCategory:
    """Normalize user-facing pro-forma category strings to the internal enum."""
    if isinstance(category, ProFormaCategory):
        return category
    normalized = _normalize_enum_value(category)
    try:
        return ProFormaCategory(normalized)
    except ValueError as exc:
        valid = ", ".join(member.value for member in ProFormaCategory)
        raise ValueError(
            f"Unknown pro forma category '{category}'. Expected one of: {valid}"
        ) from exc


def normalize_pro_forma_category(
    category: ProFormaCategory | str | None,
) -> ProFormaCategory | None:
    """Normalize an optional pro-forma category, preserving ``None``."""
    if category is None:
        return None
    return parse_pro_forma_category(category)


def parse_tax_treatment(
    treatment: TaxTreatment | str,
) -> TaxTreatment:
    """Normalize user-facing tax-treatment strings to the internal enum."""
    if isinstance(treatment, TaxTreatment):
        return treatment
    normalized = _normalize_enum_value(treatment)
    try:
        return TaxTreatment(normalized)
    except ValueError as exc:
        valid = ", ".join(member.value for member in TaxTreatment)
        raise ValueError(f"Unknown tax treatment '{treatment}'. Expected one of: {valid}") from exc


def normalize_cashflow_classification(
    pro_forma_category: ProFormaCategory | str | None,
    tax_treatment: TaxTreatment | str,
) -> tuple[ProFormaCategory | None, TaxTreatment]:
    """Normalize user-facing cashflow classification inputs."""
    return normalize_pro_forma_category(pro_forma_category), parse_tax_treatment(tax_treatment)


class SupportsLessThan(Protocol):
    def __lt__(self, __other: Any) -> bool: ...
