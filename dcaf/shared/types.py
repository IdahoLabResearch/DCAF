"""Shared type aliases, enums, and parsing helpers used across DCAF.

Defines the constrained vocabularies the rest of the library builds on:

- Time and convention aliases: :data:`Period`, :data:`TimingConvention`,
  :data:`DayCountConvention`.
- Depreciation aliases: :data:`MACRSPropertyClass`, :data:`MACRSConvention`,
  :data:`VDBConvention`.
- Construction spend aliases: :data:`SpendSchedule`, :data:`SpendScheduleName`.
- Classification enums attached to every cashflow: :class:`ProFormaCategory`
  (how a flow appears in a pro forma) and :class:`TaxTreatment` (whether a flow
  is taxable, deductible, or neutral).

The ``parse_*`` helpers normalize loosely-typed user input (strings) into these
canonical values.
"""

from enum import StrEnum
from typing import Any, Literal, Protocol, TypeAlias


Period: TypeAlias = Literal["day", "month", "quarter", "year"]
TimingConvention: TypeAlias = Literal["end", "begin", "middle"]
DayCountConvention: TypeAlias = Literal["actual/365-no-leap", "actual/365-fixed", "actual/actual"]
MACRSPropertyClass: TypeAlias = Literal[3, 5, 7, 10, 15, 20]
MACRSConvention: TypeAlias = Literal["half-year", "mid-quarter"]
VDBConvention: TypeAlias = Literal[
    "none", "half-year", "mid-quarter", "best-of-half-year-mid-quarter"
]
SpendSchedulePoint: TypeAlias = tuple[float, float]
SpendSchedule: TypeAlias = tuple[SpendSchedulePoint, ...]
InterestTreatment: TypeAlias = Literal["capitalize", "pay"]
SpendScheduleName: TypeAlias = Literal["flat", "bell", "ramped", "triangle", "linear", "upfront"]


class ProFormaCategory(StrEnum):
    REVENUE = "revenue"
    OPERATING_COST = "operating_cost"
    CAPITAL_COST = "capital_cost"
    TAX = "tax"
    TAX_CREDIT = "tax_credit"
    DEPRECIATION = "depreciation"
    FINANCING_PROCEEDS = "financing_proceeds"
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


class _DayCountConventionEnum(StrEnum):
    ACTUAL_365_NO_LEAP = "actual/365-no-leap"
    ACTUAL_365_FIXED = "actual/365-fixed"
    ACTUAL_ACTUAL = "actual/actual"


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


def parse_day_count_convention(convention: str) -> _DayCountConventionEnum:
    """Normalize user-facing day-count convention strings to an internal enum."""
    normalized = convention.strip().lower().replace(" ", "").replace("_", "-")
    aliases = {
        "actual/365nl": _DayCountConventionEnum.ACTUAL_365_NO_LEAP,
        "actual/365-nl": _DayCountConventionEnum.ACTUAL_365_NO_LEAP,
        "actual/365noleap": _DayCountConventionEnum.ACTUAL_365_NO_LEAP,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return _DayCountConventionEnum(normalized)
    except ValueError as exc:
        valid = ", ".join(member.value for member in _DayCountConventionEnum)
        raise ValueError(
            f"Unknown day count convention '{convention}'. Expected one of: {valid}"
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
