# © 2026 Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""Tests for anchored dates in the recurring cashflow and capacity generation factories.

Each factory places boundary *k* at ``start + k * period``, computed from the schedule start
rather than from the previous boundary. Each takes ``end_of_month`` (default ``True``): when the
start is the last day of its month, every monthly, quarterly, or yearly boundary is the last day
of its month. The same flag controls the factory's implicit escalation; a user-supplied
``escalation_policy`` keeps its own setting.
"""

from datetime import date, timedelta
import inspect
import warnings

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from dcaf.finance.escalation import ConstantRateEscalation
from dcaf.finance.opex import fixed_opex
from dcaf.shared.time import _whole_days_with_truncation, add_periods
from dcaf.streams import CashFlowStream, Generation, GenerationStream
from dcaf.tax.incentives import ptc
from strategies import ANCHOR_DATES, FREQUENCIES

END_OF_MONTH = st.booleans()
RATE = 0.01

# A monthly rate escalated from an Apr. 30 reference to May 31: with the flag on, May 31 is
# exactly one scheduled month later; with it off, the month ends May 30 and May 31 is one day
# into the 31-day month [May 30, Jun. 30).
MONTH_END_ESCALATION = [(True, (1 + RATE) ** 1), (False, (1 + RATE) ** (1 + 1 / 31))]


@pytest.mark.parametrize(
    "function",
    [
        CashFlowStream.from_recurring,
        GenerationStream.from_capacity,
        GenerationStream.with_capacity,
        fixed_opex,
    ],
)
def test_end_of_month_defaults_to_true(function):
    """Every stream factory follows month-ends unless told otherwise."""
    parameter = inspect.signature(function).parameters["end_of_month"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is True


# === Dates ===


@given(ANCHOR_DATES, st.integers(1, 24), FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 2, "month", True)  # Apr. 30, May 31
@example(date(2030, 4, 30), 2, "month", False)  # Apr. 30, May 30
def test_from_recurring_books_on_anchored_boundaries(start, periods, frequency, end_of_month):
    """Begin-timed recurring cashflows book on boundaries ``start + k * period``."""
    stream = CashFlowStream.from_recurring(
        start, periods, 1.0, frequency, timing="begin", end_of_month=end_of_month
    )
    assert [flow.date for flow in stream] == [
        add_periods(start, k, frequency, end_of_month=end_of_month) for k in range(periods)
    ]


@given(ANCHOR_DATES, st.integers(1, 24), FREQUENCIES, END_OF_MONTH)
@example(date(2030, 4, 30), 2, "month", True)  # [Apr. 30, May 31), [May 31, Jun. 30)
@example(date(2030, 4, 30), 2, "month", False)  # [Apr. 30, May 30), [May 30, Jun. 30)
def test_from_capacity_periods_span_anchored_boundaries(start, periods, frequency, end_of_month):
    """Capacity generation periods run between consecutive boundaries ``start + k * period``."""
    stream = GenerationStream.from_capacity(
        1.0, 1.0, start, periods, frequency, end_of_month=end_of_month
    )
    boundaries = [
        add_periods(start, k, frequency, end_of_month=end_of_month) for k in range(periods + 1)
    ]
    assert [(g.period_start, g.period_end) for g in stream] == list(zip(boundaries, boundaries[1:]))


@given(ANCHOR_DATES, st.integers(1, 24), FREQUENCIES, END_OF_MONTH)
def test_with_capacity_appends_the_anchored_capacity_stream(
    start, periods, frequency, end_of_month
):
    """Appending capacity generation produces the same periods as building it directly."""
    base = GenerationStream([Generation(1.0, start - timedelta(days=1))])
    expanded = base.with_capacity(1.0, 1.0, start, periods, frequency, end_of_month=end_of_month)
    direct = GenerationStream.from_capacity(
        1.0, 1.0, start, periods, frequency, end_of_month=end_of_month
    )
    assert list(expanded[1:]) == list(direct)


@pytest.mark.parametrize(
    "start, periods, frequency, expected",
    [
        (
            date(2030, 1, 31),
            4,
            "month",
            [date(2030, 1, 31), date(2030, 2, 28), date(2030, 3, 31), date(2030, 4, 30)],
        ),
        (
            date(2028, 2, 29),
            5,
            "year",
            [
                date(2028, 2, 29),
                date(2029, 2, 28),
                date(2030, 2, 28),
                date(2031, 2, 28),
                date(2032, 2, 29),
            ],
        ),
    ],
)
def test_from_recurring_late_month_schedules_keep_their_anchor(start, periods, frequency, expected):
    """A Jan. 31 monthly schedule returns to month-ends after February, and a Feb. 29 yearly
    schedule returns to Feb. 29 in the next leap year."""
    stream = CashFlowStream.from_recurring(start, periods, 1.0, frequency, timing="begin")
    assert [flow.date for flow in stream] == expected


def test_from_capacity_late_month_schedule_keeps_its_anchor():
    """A Jan. 31 monthly capacity schedule returns to month-ends after February."""
    stream = GenerationStream.from_capacity(1, 1, date(2030, 1, 31), 4, "month")
    assert [(g.period_start, g.period_end) for g in stream] == [
        (date(2030, 1, 31), date(2030, 2, 28)),
        (date(2030, 2, 28), date(2030, 3, 31)),
        (date(2030, 3, 31), date(2030, 4, 30)),
        (date(2030, 4, 30), date(2030, 5, 31)),
    ]


# === Fractional final period ===


@given(
    ANCHOR_DATES,
    st.integers(0, 24),
    st.floats(min_value=0.01, max_value=0.99),
    FREQUENCIES,
    END_OF_MONTH,
)
@example(date(2030, 4, 30), 1, 0.5, "month", True)  # half of [May 31, Jun. 30): 15 days
@example(date(2030, 4, 30), 1, 0.5, "month", False)  # half of [May 30, Jun. 30): 15 days
def test_from_capacity_fractional_tail_is_a_share_of_the_next_scheduled_period(
    start, whole, fraction, frequency, end_of_month
):
    """A fractional period count appends a final period holding the complete days of that share
    of the next scheduled period ``[start + n * period, start + (n + 1) * period)``."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stream = GenerationStream.from_capacity(
            1.0, 1.0, start, whole + fraction, frequency, end_of_month=end_of_month
        )

    tail_start = add_periods(start, whole, frequency, end_of_month=end_of_month)
    next_boundary = add_periods(start, whole + 1, frequency, end_of_month=end_of_month)
    tail_days, _ = _whole_days_with_truncation(fraction * (next_boundary - tail_start).days)

    assert len(stream) == whole + (tail_days > 0)
    if tail_days:
        assert (stream[-1].period_start, stream[-1].period_end) == (
            tail_start,
            tail_start + timedelta(days=tail_days),
        )


@given(
    ANCHOR_DATES,
    st.integers(0, 24),
    st.floats(min_value=0.01, max_value=0.99),
    st.sampled_from(["month", "quarter"]),
    END_OF_MONTH,
)
@example(date(2030, 4, 30), 1, 0.5, "month", True)  # 15 of the 30 days in [May 31, Jun. 30)
@example(date(2030, 4, 30), 1, 0.5, "month", False)  # 15 of the 31 days in [May 30, Jun. 30)
def test_from_recurring_fractional_tail_is_prorated_by_the_next_scheduled_period(
    start, whole, fraction, frequency, end_of_month
):
    """A fractional monthly or quarterly tail books at the start of its scheduled period, at the
    share of that period's days it covers."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stream = CashFlowStream.from_recurring(
            start, whole + fraction, 1.0, frequency, timing="begin", end_of_month=end_of_month
        )

    tail_start = add_periods(start, whole, frequency, end_of_month=end_of_month)
    period_days = (
        add_periods(start, whole + 1, frequency, end_of_month=end_of_month) - tail_start
    ).days
    tail_days, _ = _whole_days_with_truncation(fraction * period_days)

    assert len(stream) == whole + (tail_days > 0)
    if tail_days:
        assert stream[-1].date == tail_start
        assert stream[-1].amount == pytest.approx(tail_days / period_days)


# === Implicit escalation ===


@pytest.mark.parametrize("end_of_month, factor", MONTH_END_ESCALATION)
def test_from_recurring_escalation_follows_end_of_month(end_of_month, factor):
    """The implicit escalation measures months with the factory's ``end_of_month`` flag."""
    stream = CashFlowStream.from_recurring(
        date(2030, 5, 31),
        1,
        1.0,
        "day",
        escalation=RATE,
        escalation_period="month",
        amount_reference_date=date(2030, 4, 30),
        end_of_month=end_of_month,
    )
    assert [flow.amount for flow in stream] == [pytest.approx(factor)]


@pytest.mark.parametrize("end_of_month, factor", MONTH_END_ESCALATION)
def test_fixed_opex_escalation_follows_end_of_month(end_of_month, factor):
    """Fixed OPEX escalation measures months with its ``end_of_month`` flag."""
    stream = fixed_opex(
        1.0,
        date(2030, 5, 31),
        1,
        "day",
        escalation=RATE,
        escalation_period="month",
        amount_reference_date=date(2030, 4, 30),
        end_of_month=end_of_month,
    )
    assert [flow.amount for flow in stream] == [pytest.approx(-factor)]


@pytest.mark.parametrize("end_of_month, factor", MONTH_END_ESCALATION)
def test_ptc_rate_escalation_follows_end_of_month(end_of_month, factor):
    """A monthly PTC rate escalates off month-ends from an Apr. 30 reference only when
    ``end_of_month`` is set."""
    generation = GenerationStream([Generation(1.0, date(2030, 5, 31))])
    credits = ptc(
        generation,
        rate_per_mwh=1.0,
        years=1,
        escalation=RATE,
        escalation_period="month",
        amount_reference_date=date(2030, 4, 30),
        end_of_month=end_of_month,
    )
    assert [flow.amount for flow in credits] == [pytest.approx(factor)]


@pytest.mark.parametrize("policy_end_of_month, factor", MONTH_END_ESCALATION)
def test_user_escalation_policy_keeps_its_own_end_of_month(policy_end_of_month, factor):
    """A supplied escalation policy measures with its own flag, not the factory's."""
    policy = ConstantRateEscalation(
        date(2030, 4, 30), rate=RATE, period="month", end_of_month=policy_end_of_month
    )
    stream = CashFlowStream.from_recurring(
        date(2030, 5, 31),
        1,
        1.0,
        "day",
        escalation_policy=policy,
        end_of_month=not policy_end_of_month,
    )
    credits = ptc(
        GenerationStream([Generation(1.0, date(2030, 5, 31))]),
        rate_per_mwh=1.0,
        years=1,
        escalation_policy=policy,
        end_of_month=not policy_end_of_month,
    )
    assert [flow.amount for flow in stream] == [pytest.approx(factor)]
    assert [flow.amount for flow in credits] == [pytest.approx(factor)]
