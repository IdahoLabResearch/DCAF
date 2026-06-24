"""Tax incentive functions for generation and capital investment.

This module provides composable functions for computing production and investment
tax incentives from DCAF stream objects.

Functions:
    ptc: Compute Production Tax Credit (PTC) cashflows from a generation stream
    itc: Compute Investment Tax Credit (ITC) credit cashflow from a CAPEX stream
    itc_adjusted_basis: Compute adjusted depreciable basis after ITC (IRS 50% basis-reduction rule)
"""

from datetime import date

from dcaf.streams.cashflows import CashFlow, CashFlowStream
from dcaf.finance.escalation import EscalationPolicy
from dcaf.streams.generation import GenerationStream, _generation_escalation
from dcaf.shared.types import (
    DayCountConvention,
    Period,
    ProFormaCategory,
    TaxTreatment,
    normalize_cashflow_classification,
)
from dcaf.shared.formatting import format_label


def ptc(
    generation_stream: GenerationStream,
    rate_per_mwh: float,
    years: int,
    escalation: float = 0.0,
    label: str = "PTC",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.TAX_CREDIT,
    tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
    *,
    escalation_period: Period = "year",
    amount_reference_date: date | None = None,
    day_count_convention: DayCountConvention = "actual/actual",
    escalation_policy: EscalationPolicy | None = None,
) -> CashFlowStream:
    """
    Compute Production Tax Credit cashflows from a generation stream.

    This function converts eligible generation entries into positive credit
    cashflows using a per-MWh PTC rate. Eligibility is limited to entries
    dated within the first ``years`` calendar years beginning with the earliest
    generation entry date in ``generation_stream``.

    The PTC rate may be escalated over time using the same escalation policy
    conventions used elsewhere in the generation-to-cashflow bridge. A simple
    scalar ``escalation`` may be provided for constant compounding, or an
    explicit ``escalation_policy`` may be supplied for custom escalation
    behavior.

    Parameters
    ----------
    generation_stream : GenerationStream
        Stream of generation entries to evaluate for PTC eligibility.
    rate_per_mwh : float
        Base Production Tax Credit rate in dollars per MWh.
    years : int
        Number of years of PTC eligibility, measured from the earliest entry
        date in ``generation_stream``. Entries with ``entry.date.year`` greater
        than or equal to ``first_entry_year + years`` are excluded.
    escalation : float, optional
        Compound escalation rate for the PTC value, interpreted over
        ``escalation_period``. With the default ``escalation_period="year"``,
        this is an annual escalation rate. Default is ``0.0``.
    label : str, optional
        Label template applied to each generated credit cashflow. If ``"{n}"``
        is present, it is replaced with the 1-based count of eligible PTC
        entries. Default is ``"PTC"``.
    pro_forma_category : ProFormaCategory or str or None, optional
        Pro-forma category applied to each credit flow. Default is ``"tax_credit"``.
    tax_treatment : TaxTreatment or str, optional
        Tax treatment applied to each credit flow. Default is ``"none"``, so
        PTC credits are not included in taxable income.
    escalation_period : Period, optional
        Compounding period associated with ``escalation``. Default is
        ``"year"``.
    amount_reference_date : date, optional
        Date at which ``rate_per_mwh`` is known. If omitted, the earliest
        generation entry date is used as the escalation reference point.
    day_count_convention : DayCountConvention, optional
        Day-count convention used for annual PTC escalation.
    escalation_policy : EscalationPolicy, optional
        Advanced override for custom escalation behavior. When provided, it
        must not be combined with ``escalation``, ``escalation_period``, or
        ``amount_reference_date``.

    Returns
    -------
    CashFlowStream
        Cashflow stream containing positive PTC credit cashflows for eligible
        generation entries only. Returns an empty stream if
        ``generation_stream`` is empty.

    Raises
    ------
    ValueError
        If ``escalation_policy`` is combined with simple escalation inputs that
        are intended to be mutually exclusive.

    Examples
    --------
    Basic PTC conversion over a 10-year eligibility window:

    >>> from datetime import date
    >>> from dcaf.streams import GenerationStream
    >>> from dcaf.tax import ptc
    >>> generation = GenerationStream.from_capacity(1000, 0.92, date(2025, 1, 1), 20)
    >>> credits = ptc(generation, rate_per_mwh=27.5, years=10)
    >>> credits.count()
    10

    Apply annual escalation to the PTC value:

    >>> credits = ptc(generation, rate_per_mwh=27.5, years=10, escalation=0.02)
    >>> credits.entries[1].amount > credits.entries[0].amount
    True

    Use an earlier reference date for escalation:

    >>> from dcaf.streams import Generation
    >>> generation = GenerationStream([
    ...     Generation(1000.0, date(2030, 7, 1)),
    ...     Generation(1000.0, date(2030, 8, 1)),
    ... ])
    >>> credits = ptc(
    ...     generation,
    ...     rate_per_mwh=10.0,
    ...     years=5,
    ... )
    >>> credits.count()
    2
    >>> credits.sum()
    20000.0
    """
    if not generation_stream.entries:
        return CashFlowStream()

    first_entry_date = min(entry.date for entry in generation_stream.entries)
    cutoff_year = first_entry_date.year + years
    policy = _generation_escalation(
        entries=generation_stream.entries,
        escalation=escalation,
        escalation_period=escalation_period,
        amount_reference_date=amount_reference_date,
        day_count_convention=day_count_convention,
        escalation_policy=escalation_policy,
    )
    resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category,
        tax_treatment,
    )

    entries: list[CashFlow] = []
    n = 0
    for entry in generation_stream.entries:
        if entry.date.year >= cutoff_year:
            continue
        n += 1
        ptc_rate = rate_per_mwh * policy.factor(entry.date)
        flow_label = format_label(label, n)
        entries.append(
            CashFlow(
                amount=entry.amount_mwh * ptc_rate,
                date=entry.date,
                label=flow_label,
                is_cash=True,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
        )
    return CashFlowStream(entries)


def itc(
    capex_stream: CashFlowStream,
    rate: float,
    placed_in_service: date,
    label: str = "ITC",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.TAX_CREDIT,
    tax_treatment: TaxTreatment | str = TaxTreatment.NONE,
) -> CashFlowStream:
    """Compute Investment Tax Credit (ITC) from a CAPEX stream.

    Sums total qualifying basis from capex_stream and applies the ITC rate to produce
    a single dollar-for-dollar tax credit cashflow dated at placed_in_service.

    The caller is responsible for assembling the complete CAPEX CashFlowStream (which
    may span multiple construction years) before calling this function. Multi-year
    construction is handled naturally by summing over the entire stream.

    Args:
        capex_stream: Stream of CAPEX cashflows (amounts stored as negatives; abs is taken)
        rate: ITC rate as a decimal (e.g., 0.30 for 30% Section 48E credit)
        placed_in_service: Date the asset is placed in service; the credit date
        label: Label for the single resulting credit cashflow. Defaults to "ITC".
        pro_forma_category: Pro-forma category for the credit cashflow. Defaults to ``"tax_credit"``.
        tax_treatment: Tax treatment for the credit cashflow. Defaults to ``"none"``.

    Returns:
        CashFlowStream containing a single ITC credit cashflow (positive amount, is_cash=True).
        Returns an empty stream if capex_stream is empty or rate is zero.

    Examples
    --------
        >>> from datetime import date
        >>> from dcaf.streams import CashFlowStream, CashFlow
        >>> capex = CashFlowStream([
        ...     CashFlow(
        ...         -10_000_000,
        ...         date(2028, 6, 1),
        ...         "Construction CAPEX",
        ...         pro_forma_category="capital_cost",
        ...     )
        ...     ,
        ...     CashFlow(
        ...         -2_000_000,
        ...         date(2029, 3, 1),
        ...         "Supplemental CAPEX",
        ...         pro_forma_category="capital_cost",
        ...     )
        ... ])
        >>> credit = itc(capex, rate=0.30, placed_in_service=date(2030, 1, 1))
        >>> [(cf.date, cf.amount) for cf in credit]
        [(datetime.date(2030, 1, 1), 3600000.0)]
    """
    if not capex_stream.entries or rate == 0.0:
        return CashFlowStream()

    resolved_category, resolved_tax_treatment = normalize_cashflow_classification(
        pro_forma_category,
        tax_treatment,
    )
    total_basis = abs(capex_stream.sum())
    credit_amount = total_basis * rate

    return CashFlowStream(
        [
            CashFlow(
                amount=credit_amount,
                date=placed_in_service,
                label=label,
                is_cash=True,
                pro_forma_category=resolved_category,
                tax_treatment=resolved_tax_treatment,
            )
        ]
    )


def itc_adjusted_basis(capex_stream: CashFlowStream, rate: float) -> float:
    """Compute adjusted depreciable basis after taking ITC.

    Applies the IRS 50% basis-reduction rule: the depreciable basis is reduced by
    half the ITC credit taken. This must be used for downstream MACRS calculations
    when ITC is claimed.

    Formula: total_basis × (1 - rate / 2)

    Source: https://www.law.cornell.edu/uscode/text/26/48E

    Args:
        capex_stream: Stream of CAPEX cashflows (amounts stored as negatives; abs is taken)
        rate: ITC rate as a decimal (e.g., 0.30 for 30%)

    Returns:
        Adjusted depreciable basis as a float. Returns 0.0 if capex_stream is empty.

    Examples
    --------
        >>> from datetime import date
        >>> from dcaf.streams import CashFlowStream, CashFlow
        >>> capex = CashFlowStream([
        ...     CashFlow(
        ...         -100_000_000,
        ...         date(2028, 6, 1),
        ...         "CAPEX",
        ...         pro_forma_category="capital_cost",
        ...     )
        ...     ,
        ...     CashFlow(
        ...         -20_000_000,
        ...         date(2029, 3, 1),
        ...         "CAPEX Additions",
        ...         pro_forma_category="capital_cost",
        ...     )
        ... ])
        >>> itc_adjusted_basis(capex, rate=0.30)
        102000000.0
    """
    if not capex_stream.entries:
        return 0.0

    total_basis = abs(capex_stream.sum())
    return total_basis * (1 - rate / 2)
