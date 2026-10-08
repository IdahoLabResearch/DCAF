# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for anchored schedule dates in debt, construction, depreciation, and tax credit schedules.

Every finance and tax schedule places date *k* at ``start + k * period``, computed from the
schedule start rather than from the previous date. Each schedule takes ``end_of_month``
(default ``True``): when the start is the last day of its month, every monthly, quarterly, or
yearly date is the last day of its month.
"""

from datetime import date, timedelta
import inspect

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from dcaf.finance.amortization import (
    AmortizationSchedule,
    _calendarize_amortization_schedule,
    amortize,
)
from dcaf.finance.construction import (
    ConstructionFinancing,
    ConstructionSpendBuilder,
    construction_spend_schedule,
)
from dcaf.streams.generation import Generation, GenerationStream
from dcaf.tax.depreciation import macrs_schedule, vdb_schedule
from dcaf.tax.incentives import ptc
from strategies import ANCHOR_DATES, FREQUENCIES, anchored_boundary

END_OF_MONTH = st.booleans()
NON_DAILY_FREQUENCIES = st.sampled_from(["month", "quarter", "year"])


@pytest.mark.parametrize(
    "function",
    [
        AmortizationSchedule.builder,
        AmortizationSchedule.build,
        amortize,
        construction_spend_schedule,
        ConstructionSpendBuilder,
        vdb_schedule,
        macrs_schedule,
        ptc,
    ],
)
def test_end_of_month_defaults_to_true(function):
    """Every finance and tax schedule follows month-ends unless told otherwise."""
    parameter = inspect.signature(function).parameters["end_of_month"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is True


# === Amortization ===


@given(ANCHOR_DATES, st.integers(1, 60), FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 3, "month", True)  # Apr. 30, May 31, Jun. 30
@example(date(2030, 4, 30), 3, "month", False)  # Apr. 30, May 30, Jun. 30
@example(date(2030, 1, 31), 3, "month", True)  # Jan. 31, Feb. 28, Mar. 31
def test_amortization_payment_dates_are_anchored(start, term, frequency, end_of_month):
    """Payment *k* falls on boundary *k* of the schedule anchored at the first payment date."""
    schedule = AmortizationSchedule.build(
        100_000.0, 0.05, term, start, frequency, end_of_month=end_of_month
    )
    expected = [anchored_boundary(start, k, frequency, end_of_month) for k in range(term)]
    for stream in (schedule.total, schedule.interest, schedule.principal):
        assert [flow.date for flow in stream] == expected


@given(
    ANCHOR_DATES,
    st.integers(1, 36),
    FREQUENCIES,
    END_OF_MONTH,
    st.integers(-40, 400),
    st.integers(1, 400),
)
@example(date(2030, 4, 30), 6, "month", True, 31, 1)  # selects only May 31
@example(date(2030, 4, 30), 6, "month", False, 31, 1)  # May 31 is not a payment date
def test_amortization_interest_free_dates_select_anchored_payments(
    start, term, frequency, end_of_month, offset_days, span_days
):
    """An interest-free date window ``[from_date, to_date)`` covers exactly the payments whose
    anchored dates fall inside it."""
    from_date = start + timedelta(days=offset_days)
    to_date = from_date + timedelta(days=span_days)
    schedule = (
        AmortizationSchedule.builder(
            100_000.0, 0.05, term, start, frequency, end_of_month=end_of_month
        )
        .interest_free(from_date=from_date, to_date=to_date)
        .build()
    )
    payment_dates = [anchored_boundary(start, k, frequency, end_of_month) for k in range(term)]
    expected_free = [from_date <= payment_date < to_date for payment_date in payment_dates]
    assert [flow.amount == 0.0 for flow in schedule.interest] == expected_free


@pytest.mark.parametrize(
    ("end_of_month", "april_share"),
    [
        # One payment covers [Apr. 30, May 31): 1 of its 31 days is in April.
        (True, 1 / 31),
        # One payment covers [Apr. 30, May 30): 1 of its 30 days is in April.
        (False, 1 / 30),
    ],
)
def test_calendarized_amortization_spreads_over_the_anchored_payment_period(
    end_of_month, april_share
):
    """Spreading payments over calendar months treats the final payment as covering the
    interval up to the next anchored boundary."""
    schedule = AmortizationSchedule.build(
        100_000.0, 0.06, 1, date(2030, 4, 30), "month", end_of_month=end_of_month
    )
    calendarized = _calendarize_amortization_schedule(
        schedule,
        frequency="month",
        timing="end",
        day_count_convention="actual/actual",
        end_of_month=end_of_month,
    )
    interest = schedule.interest.entries[0].amount
    april, may = calendarized.interest.entries
    assert april.date == date(2030, 4, 30)
    assert april.amount == pytest.approx(interest * april_share)
    assert april.amount + may.amount == pytest.approx(interest)


# === Construction ===


@given(ANCHOR_DATES, st.integers(1, 1500), NON_DAILY_FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 92, "month", True)  # Apr. 30 -> Jul. 31 on month-ends
@example(date(2030, 4, 30), 92, "month", False)  # Apr. 30 -> Jul. 31 off month-ends
@example(date(2030, 6, 30), 200, "quarter", True)  # Jun. 30, Sep. 30, Dec. 31
def test_construction_interest_periods_are_anchored(start, days, servicing_period, end_of_month):
    """Construction interest is booked at the end of each servicing period anchored at the
    construction start, with the last period cut at the exclusive construction end."""
    end = start + timedelta(days=days)
    stream = construction_spend_schedule(
        1_000_000.0,
        start,
        end,
        # Spend booked at period begin draws debt on the start date, so every servicing period
        # accrues interest.
        timing="begin",
        financing=ConstructionFinancing.debt(
            0.5, interest_rate=0.06, servicing_period=servicing_period
        ),
        end_of_month=end_of_month,
    )

    expected = []
    k = 1
    while anchored_boundary(start, k - 1, servicing_period, end_of_month) < end:
        expected.append(min(anchored_boundary(start, k, servicing_period, end_of_month), end))
        k += 1
    interest_dates = [flow.date for flow in stream if flow.label == "Capitalized Interest"]
    assert interest_dates == expected


@pytest.mark.parametrize(
    ("end_of_month", "expected_dates"),
    [
        (
            True,
            [date(2030, 5, 31), date(2030, 6, 30), date(2030, 7, 31)]
        ),
        (
            False,
            [date(2030, 5, 30), date(2030, 6, 30), date(2030, 7, 30), date(2030, 7, 31)],
        ),
    ],
)
def test_construction_interest_from_a_30_day_month_end(end_of_month, expected_dates):
    """Monthly construction interest from Apr. 30 is booked on month-ends under the month-end
    rule, and on the 30th, plus a final one-day stub, without it."""
    stream = construction_spend_schedule(
        1_000_000.0,
        date(2030, 4, 30),
        date(2030, 7, 31),
        timing="begin",
        financing=ConstructionFinancing.debt(0.5, interest_rate=0.06),
        end_of_month=end_of_month,
    )
    interest_dates = [flow.date for flow in stream if flow.label == "Capitalized Interest"]
    assert interest_dates == expected_dates


@pytest.mark.parametrize(
    ("end_of_month", "periods"),
    [
        (True, 1.0),
        # Without the month-end rule, May 31 is one day into the 31-day month [May 30, Jun. 30).
        (False, 1 + 1 / 31),
    ],
)
def test_construction_escalation_follows_end_of_month(end_of_month, periods):
    """The implicit construction escalation uses the same month-end rule as the schedule. One
    flat month of spend booked May 31 escalates monthly from an Apr. 30 reference date."""
    stream = construction_spend_schedule(
        1_000_000.0,
        date(2030, 5, 1),
        date(2030, 6, 1),
        escalation=0.01,
        escalation_period="month",
        amount_reference_date=date(2030, 4, 30),
        end_of_month=end_of_month,
    )
    (spend,) = stream.entries
    assert spend.date == date(2030, 5, 31)
    assert spend.amount == pytest.approx(-1_000_000.0 * 1.01**periods)


def test_construction_builder_passes_end_of_month_through():
    """The construction builder records the month-end rule and builds the same schedule as the
    direct function."""
    kwargs = dict(
        timing="begin",
        financing=ConstructionFinancing.debt(0.5, interest_rate=0.06),
        end_of_month=False,
    )
    builder = ConstructionSpendBuilder(1_000_000.0, date(2030, 4, 30), date(2030, 7, 31), **kwargs)
    assert builder.config.end_of_month is False
    assert builder.build() == construction_spend_schedule(
        1_000_000.0, date(2030, 4, 30), date(2030, 7, 31), **kwargs
    )


# === Depreciation ===


@given(ANCHOR_DATES, st.integers(1, 40), FREQUENCIES, END_OF_MONTH)
@example(date(2030, 1, 31), 3, "month", True)  # Jan. 31, Feb. 28, Mar. 31
@example(date(2030, 1, 31), 3, "month", False)  # Jan. 31, Feb. 28, Mar. 31
@example(date(2030, 4, 30), 3, "month", True)  # Apr. 30, May 31, Jun. 30
@example(date(2028, 2, 29), 5, "year", True)  # returns to Feb. 29 in 2032
def test_vdb_schedule_dates_are_anchored(placed_in_service, life, frequency, end_of_month):
    """Depreciation period *k* is dated at boundary *k* of the schedule anchored at the
    placed-in-service date, never drifting after a clamped short month."""
    stream = vdb_schedule(
        1000.0, 0.0, placed_in_service, life, frequency, end_of_month=end_of_month
    )
    expected = [
        anchored_boundary(placed_in_service, k, frequency, end_of_month) for k in range(life)
    ]
    assert [flow.date for flow in stream] == expected


@given(
    ANCHOR_DATES,
    st.integers(1, 40),
    FREQUENCIES,
    END_OF_MONTH,
    st.sampled_from(["half-year", "mid-quarter"]),
    st.booleans(),
)
@example(date(2030, 1, 31), 3, "month", True, "half-year", True)  # Jan. 31 -> Mar. 31
@example(date(2028, 2, 29), 5, "year", False, "mid-quarter", True)  # Feb. 29 -> 2033
def test_vdb_schedule_convention_dates_are_anchored(
    placed_in_service, life, frequency, end_of_month, convention, terminal_catch_up
):
    """Convention-aware schedules without explicit ``schedule_dates`` place each flow on a
    boundary of the schedule anchored at the placed-in-service date, starting there."""
    stream = vdb_schedule(
        1000.0,
        0.0,
        placed_in_service,
        life,
        frequency,
        convention=convention,
        terminal_catch_up=terminal_catch_up,
        end_of_month=end_of_month,
    )
    boundaries = [
        anchored_boundary(placed_in_service, k, frequency, end_of_month)
        for k in range(life + terminal_catch_up)
    ]
    dates = [flow.date for flow in stream]
    assert dates[0] == placed_in_service
    assert all(flow_date in boundaries for flow_date in dates)
    assert dates == sorted(set(dates))


@given(
    ANCHOR_DATES,
    st.sampled_from([3, 5, 7, 10, 15, 20]),
    st.sampled_from(["half-year", "mid-quarter"]),
    END_OF_MONTH,
)
@example(date(2028, 2, 29), 5, "half-year", True)  # leap-day placement no longer raises
@example(date(2028, 2, 29), 5, "half-year", False)
@example(date(2029, 2, 28), 5, "half-year", True)  # Feb. 29, 2032 under the month-end rule
def test_macrs_schedule_dates_are_anchored(
    placed_in_service, property_class, convention, end_of_month
):
    """MACRS year *k* is dated at yearly boundary *k* anchored at the placed-in-service date,
    including a Feb. 29 placement, and the schedule still recovers the full basis."""
    stream = macrs_schedule(
        1000.0, placed_in_service, property_class, convention, end_of_month=end_of_month
    )
    expected = [
        anchored_boundary(placed_in_service, k, "year", end_of_month) for k in range(stream.count())
    ]
    assert [flow.date for flow in stream] == expected
    assert stream.sum() == pytest.approx(-1000.0)


@pytest.mark.parametrize(
    ("placed_in_service", "end_of_month", "expected"),
    [
        (
            date(2028, 2, 29),
            True,
            [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28)],
        ),
        (
            date(2029, 2, 28),
            True,
            [date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)],
        ),
        (
            date(2029, 2, 28),
            False,
            [date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 28)],
        ),
    ],
)
def test_macrs_schedule_february_placements(placed_in_service, end_of_month, expected):
    """A 3-year MACRS schedule (four half-year-convention entries) placed on a February
    month-end, worked out by hand."""
    stream = macrs_schedule(1000.0, placed_in_service, 3, end_of_month=end_of_month)
    assert [flow.date for flow in stream] == expected


# === Production tax credit ===


@given(ANCHOR_DATES, st.integers(1, 20), END_OF_MONTH)
@example(date(2029, 2, 28), 3, True)  # eligibility ends Feb. 29, 2032
@example(date(2029, 2, 28), 3, False)  # eligibility ends Feb. 28, 2032
@example(date(2028, 2, 29), 1, True)  # eligibility ends Feb. 28, 2029
def test_ptc_eligibility_ends_on_the_anchored_anniversary(start, years, end_of_month):
    """PTC eligibility is ``[start, start + years)`` with the end on the anchored yearly
    boundary: generation on the day before that boundary earns credit, and generation on the
    boundary does not."""
    eligibility_end = anchored_boundary(start, years, "year", end_of_month)
    generation = GenerationStream(
        [
            Generation(1.0, start),
            Generation(1.0, eligibility_end - timedelta(days=1)),
            Generation(1.0, eligibility_end),
        ]
    )
    credits = ptc(generation, rate_per_mwh=1.0, years=years, end_of_month=end_of_month)
    assert credits.sum() == pytest.approx(2.0)
    assert max(flow.date for flow in credits) < eligibility_end
