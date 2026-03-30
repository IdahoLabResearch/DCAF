"""Tests for debt amortization schedule generation."""

from datetime import date

import pytest

from dcaf.finance import AmortizationSchedule
from dcaf.shared.types import ProFormaCategory, TaxTreatment


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
    total_principal = sum(f.amount for f in monthly_schedule.principal.entries)
    assert abs(total_principal + 100_000.0) < 0.01


# === All flows negative ===


def test_all_flows_negative(monthly_schedule: AmortizationSchedule):
    """Every amount is <= 0 across all three streams."""
    for stream in (monthly_schedule.total, monthly_schedule.interest, monthly_schedule.principal):
        assert all(f.amount <= 0 for f in stream.entries)


# === Fixed payment ===


def test_fixed_payment(monthly_schedule: AmortizationSchedule):
    """All total flows have equal amounts (fixed payment)."""
    amounts = [f.amount for f in monthly_schedule.total.entries]
    assert all(abs(a - amounts[0]) < 1e-6 for a in amounts)


# === Interest decreases monotonically ===


def test_interest_decreases(monthly_schedule: AmortizationSchedule):
    """Interest component decreases monotonically during amortizing portion."""
    amounts = [abs(f.amount) for f in monthly_schedule.interest.entries]
    for i in range(1, len(amounts)):
        assert amounts[i] <= amounts[i - 1] + 1e-10


# === Principal increases monotonically ===


def test_principal_increases(monthly_schedule: AmortizationSchedule):
    """Principal component increases monotonically during amortizing portion."""
    amounts = [abs(f.amount) for f in monthly_schedule.principal.entries]
    for i in range(1, len(amounts)):
        assert amounts[i] >= amounts[i - 1] - 1e-10


# === Interest + principal = total ===


def test_interest_plus_principal_equals_total(monthly_schedule: AmortizationSchedule):
    """Per-period invariant: interest + principal = total."""
    for t, i, p in zip(
        monthly_schedule.total.entries,
        monthly_schedule.interest.entries,
        monthly_schedule.principal.entries,
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
    for f in schedule.principal.entries[:12]:
        assert f.amount == 0.0

    # IO period: interest is constant (balance unchanged)
    io_interest = [f.amount for f in schedule.interest.entries[:12]]
    assert all(abs(a - io_interest[0]) < 1e-10 for a in io_interest)

    # Amortizing portion still has 48 periods
    amort_principal = schedule.principal.entries[12:]
    assert all(f.amount < 0 for f in amort_principal)

    # Total principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.entries)
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
        assert schedule.interest.entries[i].amount == 0.0

    # Non-free periods have nonzero interest
    for i in [0, 1, 2, 6, 7]:
        assert schedule.interest.entries[i].amount < 0.0

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.entries)
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
        assert schedule.interest.entries[i].amount == 0.0

    # Periods outside the range have nonzero interest
    for i in [0, 1, 2, 6, 7]:
        assert schedule.interest.entries[i].amount < 0.0

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.entries)
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
        assert schedule.interest.entries[i].amount < 0.0

    # Periods 3-5 are interest-free
    for i in range(3, 6):
        assert schedule.interest.entries[i].amount == 0.0

    total_principal = sum(f.amount for f in schedule.principal.entries)
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
    first_interest = abs(schedule.interest.entries[0].amount)
    assert abs(first_interest - 500.0) < 1.0

    # After rate change, interest should be lower than if rate hadn't changed
    # Period 6 interest should reflect the 3% rate on remaining balance
    period_6_interest = abs(schedule.interest.entries[6].amount)
    assert period_6_interest < first_interest

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.entries)
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
    for f in schedule.principal.entries[:6]:
        assert f.amount == 0.0

    # Post-IO, pre-rate-change: principal is being paid
    for f in schedule.principal.entries[6:12]:
        assert f.amount < 0.0

    # After rate change period 12: interest rate is lower
    # Interest at period 12 should reflect 4% rate
    interest_11 = abs(schedule.interest.entries[11].amount)
    interest_12 = abs(schedule.interest.entries[12].amount)
    # Period 12 has lower rate, and balance dropped from principal payment,
    # so interest should be noticeably less
    assert interest_12 < interest_11

    # Principal still sums to loan
    total_principal = sum(f.amount for f in schedule.principal.entries)
    assert abs(total_principal + 120_000.0) < 0.01


# === Classification ===


def test_interest_classification(monthly_schedule: AmortizationSchedule):
    """Interest flows have the correct default classification."""
    for f in monthly_schedule.interest.entries:
        assert f.pro_forma_category is ProFormaCategory.FINANCING_INTEREST
        assert f.tax_treatment is TaxTreatment.DEDUCTIBLE


def test_principal_classification(monthly_schedule: AmortizationSchedule):
    """Principal flows have the correct default classification."""
    for f in monthly_schedule.principal.entries:
        assert f.pro_forma_category is ProFormaCategory.FINANCING_PRINCIPAL
        assert f.tax_treatment is TaxTreatment.NONE


def test_total_classification(monthly_schedule: AmortizationSchedule):
    """Total flows are intentionally uncategorized composite rows."""
    for f in monthly_schedule.total.entries:
        assert f.pro_forma_category is None
        assert f.tax_treatment is TaxTreatment.NONE


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
    dates = [f.date for f in schedule.total.entries]
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
    dates = [f.date for f in schedule.total.entries]
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
    dates = [f.date for f in schedule.total.entries]
    assert dates == [date(2026, 6, 15), date(2027, 6, 15), date(2028, 6, 15)]


def test_annual_amortization_interest_uses_prior_year_end_balance():
    """Annual schedules accrue interest from the opening balance of each year."""
    schedule = AmortizationSchedule.build(
        principal=100_000.0,
        annual_rate=0.10,
        term=3,
        start_date=date(2026, 1, 1),
        frequency="year",
    )

    assert schedule.interest.entries[0].amount == pytest.approx(-10_000.0)

    opening_balance_year_2 = 100_000.0 + schedule.principal.entries[0].amount
    expected_year_2_interest = -opening_balance_year_2 * 0.10
    assert schedule.interest.entries[1].amount == pytest.approx(expected_year_2_interest)


# === Zero rate ===


def test_zero_rate():
    """With zero rate, principal is evenly divided and interest is zero."""
    schedule = AmortizationSchedule.build(
        principal=12_000.0,
        annual_rate=0.0,
        term=12,
        start_date=date(2026, 1, 1),
    )
    for f in schedule.interest.entries:
        assert f.amount == 0.0
    for f in schedule.principal.entries:
        assert abs(f.amount + 1_000.0) < 1e-10
    for f in schedule.total.entries:
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
    payment = abs(schedule.total.entries[0].amount)
    assert abs(payment - 536.82) < 0.01


# === is_cash ===


def test_all_flows_are_cash(monthly_schedule: AmortizationSchedule):
    """All amortization flows are cash flows."""
    for stream in (monthly_schedule.total, monthly_schedule.interest, monthly_schedule.principal):
        assert all(f.is_cash for f in stream.entries)


def test_invalid_frequency_is_rejected():
    with pytest.raises(ValueError, match="Unknown period"):
        AmortizationSchedule.build(
            principal=10_000.0,
            annual_rate=0.05,
            term=6,
            start_date=date(2026, 1, 1),
            frequency="week",  # type: ignore[arg-type]
        )


# === label index interpolation ===


def test_default_labels(monthly_schedule):
    """No index interpolation in default labels."""
    total_flows = monthly_schedule.total.entries
    interest_flows = monthly_schedule.interest.entries
    principal_flows = monthly_schedule.principal.entries

    # Check that labels do not vary between timesteps
    assert total_flows[0].label == total_flows[1].label
    assert interest_flows[0].label == interest_flows[1].label
    assert principal_flows[0].label == principal_flows[1].label


def test_index_interpolation_in_labels():
    """Index interpolation in labels is correct"""
    schedule = AmortizationSchedule.build(
        principal=1_000.0,
        annual_rate=0.1,
        term=3,
        start_date=date(2026, 1, 1),
        frequency="year",
        label="total year {n}",
        interest_label="interest year {n}",
        principal_label="principal year {n}",
    )

    total_flows = schedule.total.entries
    assert total_flows[0].label == "total year 1"
    assert total_flows[2].label == "total year 3"

    interest_flows = schedule.interest.entries
    assert interest_flows[0].label == "interest year 1"
    assert interest_flows[2].label == "interest year 3"

    principal_flows = schedule.principal.entries
    assert principal_flows[0].label == "principal year 1"
    assert principal_flows[2].label == "principal year 3"
