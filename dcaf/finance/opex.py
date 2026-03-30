"""
Fixed operating expense cash flows.

Provides ``fixed_opex()`` as a thin facade over ``CashFlowStream.from_recurring()``.
For variable OPEX, use ``GenerationStream.to_cost()`` directly.
"""

from datetime import date

from dcaf.finance.escalation import EscalationPolicy
from dcaf.shared.types import Period, ProFormaCategory, TaxTreatment
from dcaf.streams.cashflows import CashFlowStream


def fixed_opex(
    amount: float,
    start: date,
    periods: int,
    frequency: Period = "year",
    escalation: float = 0.0,
    label: str = "Fixed OPEX",
    pro_forma_category: ProFormaCategory | str | None = ProFormaCategory.OPERATING_COST,
    tax_treatment: TaxTreatment | str = TaxTreatment.DEDUCTIBLE,
    *,
    escalation_period: Period = "year",
    amount_reference_date: date | None = None,
    escalation_policy: EscalationPolicy | None = None,
) -> CashFlowStream:
    """
    Create a stream of recurring fixed operating expense cashflows.

    Parameters
    ----------
    amount : float
        Base cost for the first period (positive or negative; always stored as negative).
    start : date
        Date of the first cashflow.
    periods : int
        Number of cashflows to generate.
    frequency : Period
        Payment frequency. One of ``"day"``, ``"month"``, ``"quarter"``, ``"year"``.
        Default is ``"year"``.
    escalation : float
        Compound escalation rate, interpreted over ``escalation_period``.
        With the default ``escalation_period="year"``, ``0.025`` means 2.5%
        year-on-year growth. Pass a different ``escalation_period`` to model
        rates quoted per month, quarter, or day. Default is ``0.0``.
    escalation_period : Period
        Compounding period associated with ``escalation``. Default is ``"year"``.
        Pass a non-annual value such as ``"month"`` to model escalation rates
        quoted per month, quarter, or day.
    amount_reference_date : date, optional
        Date at which ``amount`` is known. Escalation is evaluated from this
        date to each payment date. Defaults to ``start``.
    escalation_policy : EscalationPolicy, optional
        Advanced override for custom escalation behavior. When provided, it
        must not be combined with ``escalation``, ``escalation_period``, or
        ``amount_reference_date``.
    label : str
        Label template; ``{n}`` is replaced with the 1-based period index.
        Default is ``"Fixed OPEX"``.
    pro_forma_category : ProFormaCategory or str or None
        Pro-forma category applied to every cashflow. Default is ``"operating_cost"``.
    tax_treatment : TaxTreatment or str
        Tax treatment applied to every cashflow. Default is ``"deductible"``.

    Returns
    -------
    CashFlowStream
        Recurring negative cashflows representing the fixed OPEX cost.

    Examples
    --------
    >>> from datetime import date
    >>> from dcaf.finance import fixed_opex
    >>> stream = fixed_opex(amount=500_000, start=date(2025, 1, 1), periods=3, escalation=0.025)
    >>> [(f.date.year, f.amount) for f in stream.entries]
    [(2025, -500000.0), (2026, -512500.0), (2027, -525312.5)]
    """
    return CashFlowStream.from_recurring(
        amount=-abs(amount),
        start=start,
        periods=periods,
        frequency=frequency,
        escalation=escalation,
        escalation_period=escalation_period,
        amount_reference_date=amount_reference_date,
        escalation_policy=escalation_policy,
        label=label,
        pro_forma_category=pro_forma_category,
        tax_treatment=tax_treatment,
    )
