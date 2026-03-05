"""Tests for debt amortization schedule generation."""

from datetime import date

import pytest

from dcaf import AmortizationSchedule, CashFlowTags


@pytest.fixture()
def monthly_schedule() -> AmortizationSchedule:
    """Standard 10-year monthly amortization at 5%."""
    return AmortizationSchedule.build(
        principal=100_000.0,
        annual_rate=0.05,
        term=120,
        start_date=date(2026, 1, 15),
    )


# === Payment count ===


def test_payment_count(monthly_schedule: AmortizationSchedule):
    """term=120 produces 120 flows in each stream."""
    assert monthly_schedule.total.count() == 120
    assert monthly_schedule.interest.count() == 120
    assert monthly_schedule.principal.count() == 120


# === Principal sums to loan ===


def test_principal_sums_to_loan(monthly_schedule: AmortizationSchedule):
    """Sum of principal flows approximates -principal."""
    total_principal = sum(f.amount for f in monthly_schedule.principal.flows)
    assert abs(total_principal + 100_000.0) < 0.01


# === All flows negative ===


def test_all_flows_negative(monthly_schedule: AmortizationSchedule):
    """Every amount is <= 0 across all three streams."""
    for stream in (monthly_schedule.total, monthly_schedule.interest, monthly_schedule.principal):
        assert all(f.amount <= 0 for f in stream.flows)


# === Fixed payment ===


def test_fixed_payment(monthly_schedule: AmortizationSchedule):
    """All total flows have equal amounts (fixed payment)."""
    amounts = [f.amount for f in monthly_schedule.total.flows]
    assert all(abs(a - amounts[0]) < 1e-6 for a in amounts)


# === Interest decreases monotonically ===


def test_interest_decreases(monthly_schedule: AmortizationSchedule):
    """Interest component decreases monotonically during amortizing portion."""
    amounts = [abs(f.amount) for f in monthly_schedule.interest.flows]
    for i in range(1, len(amounts)):
        assert amounts[i] <= amounts[i - 1] + 1e-10


# === Principal increases monotonically ===


def test_principal_increases(monthly_schedule: AmortizationSchedule):
    """Principal component increases monotonically during amortizing portion."""
    amounts = [abs(f.amount) for f in monthly_schedule.principal.flows]
    for i in range(1, len(amounts)):
        assert amounts[i] >= amounts[i - 1] - 1e-10


# === Interest + principal = total ===


def test_interest_plus_principal_equals_total(monthly_schedule: AmortizationSchedule):
    """Per-period invariant: interest + principal = total."""
    for t, i, p in zip(
        monthly_schedule.total.flows,
        monthly_schedule.interest.flows,
        monthly_schedule.principal.flows,
        strict=True,
    ):
        assert abs(t.amount - (i.amount + p.amount)) < 1e-10


# === Interest-only periods ===


def test_interest_only_periods():
    """During IO periods, principal is 0 and balance is unchanged."""
    schedule = (
        AmortizationSchedule.builder(
            principal=200_000.0,
            annual_rate=0.06,
            term=60,
            start_date=date(2026, 1, 1),
        )
        .interest_only(12)
        .build()
    )
    # IO period: principal is zero
    for f in schedule.principal.flows[:12]:
        assert f.amount == 0.0

    # IO period: interest is constant (balance unchanged)
    io_interest = [f.amount for f in schedule.interest.flows[:12]]
    assert all(abs(a - io_interest[0]) < 1e-10 for a in io_interest)

    # Amortizing portion still has 48 periods
    amort_principal = schedule.principal.flows[12:]
    assert all(f.amount < 0 for f in amort_principal)

    # Total principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.flows)
    assert abs(total_principal + 200_000.0) < 0.01


# === Interest-free periods ===


def test_interest_free_periods_by_period():
    """During interest-free periods, interest is 0 but principal is still paid."""
    schedule = (
        AmortizationSchedule.builder(
            principal=60_000.0,
            annual_rate=0.06,
            term=12,
            start_date=date(2026, 1, 1),
        )
        .interest_free(from_period=3, to_period=5)
        .build()
    )
    # Interest-free periods have zero interest
    for i in range(3, 6):
        assert schedule.interest.flows[i].amount == 0.0

    # Non-free periods have nonzero interest
    for i in [0, 1, 2, 6, 7]:
        assert schedule.interest.flows[i].amount < 0.0

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.flows)
    assert abs(total_principal + 60_000.0) < 0.01


def test_interest_free_periods_by_date():
    """Interest-free via date range resolves to the correct periods."""
    schedule = (
        AmortizationSchedule.builder(
            principal=60_000.0,
            annual_rate=0.06,
            term=12,
            start_date=date(2026, 1, 1),
        )
        .interest_free(from_date=date(2026, 4, 1), to_date=date(2026, 6, 1))
        .build()
    )
    # Periods 3, 4, 5 correspond to Apr, May, Jun
    for i in range(3, 6):
        assert schedule.interest.flows[i].amount == 0.0

    # Periods outside the range have nonzero interest
    for i in [0, 1, 2, 6, 7]:
        assert schedule.interest.flows[i].amount < 0.0

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.flows)
    assert abs(total_principal + 60_000.0) < 0.01


def test_interest_free_open_ended():
    """Omitting to_period defaults to end of schedule."""
    schedule = (
        AmortizationSchedule.builder(
            principal=24_000.0,
            annual_rate=0.06,
            term=6,
            start_date=date(2026, 1, 1),
        )
        .interest_free(from_period=3)
        .build()
    )
    # Periods 0-2 have interest
    for i in range(3):
        assert schedule.interest.flows[i].amount < 0.0

    # Periods 3-5 are interest-free
    for i in range(3, 6):
        assert schedule.interest.flows[i].amount == 0.0

    total_principal = sum(f.amount for f in schedule.principal.flows)
    assert abs(total_principal + 24_000.0) < 0.01


# === Rate change ===


def test_rate_change():
    """Rate change at period 6 produces different interest amounts."""
    schedule = (
        AmortizationSchedule.builder(
            principal=100_000.0,
            annual_rate=0.06,
            term=24,
            start_date=date(2026, 1, 1),
        )
        .rate_change(from_period=6, annual_rate=0.03)
        .build()
    )
    # First period interest at 6%: 100_000 * 0.06/12 = 500
    first_interest = abs(schedule.interest.flows[0].amount)
    assert abs(first_interest - 500.0) < 1.0

    # After rate change, interest should be lower than if rate hadn't changed
    # Period 6 interest should reflect the 3% rate on remaining balance
    period_6_interest = abs(schedule.interest.flows[6].amount)
    assert period_6_interest < first_interest

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.flows)
    assert abs(total_principal + 100_000.0) < 0.01


# === Composed rules ===


def test_composed_rules():
    """Multiple chained rules work together correctly."""
    schedule = (
        AmortizationSchedule.builder(
            principal=120_000.0,
            annual_rate=0.06,
            term=36,
            start_date=date(2026, 1, 1),
        )
        .interest_only(6)
        .rate_change(from_period=12, annual_rate=0.04)
        .build()
    )
    # IO periods: no principal
    for f in schedule.principal.flows[:6]:
        assert f.amount == 0.0

    # Post-IO, pre-rate-change: principal is being paid
    for f in schedule.principal.flows[6:12]:
        assert f.amount < 0.0

    # After rate change period 12: interest rate is lower
    # Interest at period 12 should reflect 4% rate
    interest_11 = abs(schedule.interest.flows[11].amount)
    interest_12 = abs(schedule.interest.flows[12].amount)
    # Period 12 has lower rate, and balance dropped from principal payment,
    # so interest should be noticeably less
    assert interest_12 < interest_11

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.flows)
    assert abs(total_principal + 120_000.0) < 0.01


# === Tags ===


def test_interest_tags(monthly_schedule: AmortizationSchedule):
    """Interest flows have correct default tags."""
    expected = {CashFlowTags.DEBT_INTEREST, CashFlowTags.EXPENSE, CashFlowTags.TAX_DEDUCTIBLE}
    for f in monthly_schedule.interest.flows:
        assert set(f.tags) == expected


def test_principal_tags(monthly_schedule: AmortizationSchedule):
    """Principal flows have correct default tags."""
    expected = {CashFlowTags.DEBT_PRINCIPAL, CashFlowTags.EXPENSE}
    for f in monthly_schedule.principal.flows:
        assert set(f.tags) == expected


def test_total_tags(monthly_schedule: AmortizationSchedule):
    """Total flows have the union of interest and principal tags."""
    expected = {
        CashFlowTags.DEBT_INTEREST,
        CashFlowTags.DEBT_PRINCIPAL,
        CashFlowTags.EXPENSE,
        CashFlowTags.TAX_DEDUCTIBLE,
    }
    for f in monthly_schedule.total.flows:
        assert set(f.tags) == expected


# === Date spacing ===


def test_monthly_date_spacing():
    """Monthly dates increment by one month."""
    schedule = AmortizationSchedule.build(
        principal=10_000.0,
        annual_rate=0.05,
        term=6,
        start_date=date(2026, 3, 1),
        frequency="month",
    )
    dates = [f.date for f in schedule.total.flows]
    assert dates == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
        date(2026, 6, 1),
        date(2026, 7, 1),
        date(2026, 8, 1),
    ]


def test_quarterly_date_spacing():
    """Quarterly dates increment by three months."""
    schedule = AmortizationSchedule.build(
        principal=10_000.0,
        annual_rate=0.05,
        term=4,
        start_date=date(2026, 1, 1),
        frequency="quarter",
    )
    dates = [f.date for f in schedule.total.flows]
    assert dates == [
        date(2026, 1, 1),
        date(2026, 4, 1),
        date(2026, 7, 1),
        date(2026, 10, 1),
    ]


def test_annual_date_spacing():
    """Annual dates increment by one year."""
    schedule = AmortizationSchedule.build(
        principal=10_000.0,
        annual_rate=0.05,
        term=3,
        start_date=date(2026, 6, 15),
        frequency="year",
    )
    dates = [f.date for f in schedule.total.flows]
    assert dates == [date(2026, 6, 15), date(2027, 6, 15), date(2028, 6, 15)]


# === Zero rate ===


def test_zero_rate():
    """With zero rate, principal is evenly divided and interest is zero."""
    schedule = AmortizationSchedule.build(
        principal=12_000.0,
        annual_rate=0.0,
        term=12,
        start_date=date(2026, 1, 1),
    )
    for f in schedule.interest.flows:
        assert f.amount == 0.0
    for f in schedule.principal.flows:
        assert abs(f.amount + 1_000.0) < 1e-10
    for f in schedule.total.flows:
        assert abs(f.amount + 1_000.0) < 1e-10


# === Known value ===


def test_known_value_30yr_mortgage():
    """$100k at 5% annual, monthly, 360 periods -> payment ~$536.82."""
    schedule = AmortizationSchedule.build(
        principal=100_000.0,
        annual_rate=0.05,
        term=360,
        start_date=date(2026, 1, 1),
    )
    payment = abs(schedule.total.flows[0].amount)
    assert abs(payment - 536.82) < 0.01


# === is_cash ===


def test_all_flows_are_cash(monthly_schedule: AmortizationSchedule):
    """All amortization flows are cash flows."""
    for stream in (monthly_schedule.total, monthly_schedule.interest, monthly_schedule.principal):
        assert all(f.is_cash for f in stream.flows)
