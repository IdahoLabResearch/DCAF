"""Shared types and utility functions."""

from dcaf.shared.formatting import format_label
from dcaf.shared.time import (
    compound_factor,
    elapsed_periods,
    event_date,
    hours_per_period,
    period_end,
    period_start,
    time_delta_per_period,
    timedelta_fractional_years,
)
from dcaf.shared.types import (
    DayCountConvention,
    InterestTreatment,
    MACRSConvention,
    MACRSPropertyClass,
    Period,
    ProFormaCategory,
    SpendSchedule,
    SpendScheduleName,
    SupportsLessThan,
    TaxTreatment,
    TimingConvention,
    VDBConvention,
)

__all__ = [
    "DayCountConvention",
    "InterestTreatment",
    "MACRSConvention",
    "MACRSPropertyClass",
    "Period",
    "ProFormaCategory",
    "SpendSchedule",
    "SpendScheduleName",
    "SupportsLessThan",
    "TaxTreatment",
    "TimingConvention",
    "VDBConvention",
    "compound_factor",
    "elapsed_periods",
    "event_date",
    "format_label",
    "hours_per_period",
    "period_end",
    "period_start",
    "time_delta_per_period",
    "timedelta_fractional_years",
]
