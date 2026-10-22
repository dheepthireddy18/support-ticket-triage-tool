"""Tests for the PriorityRule base class and the built-in rules."""

from datetime import timedelta

import pytest

from ticket_triage.models import PRIORITY_HIGH, PRIORITY_MEDIUM
from ticket_triage.priority.base import PriorityRule, TriageContext
from ticket_triage.priority.keyword_rule import Keyword, KeywordRule
from ticket_triage.priority.repeat_rule import RepeatContactRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule

# --- Base class --------------------------------------------------------------


def test_base_class_cannot_be_instantiated():
    with pytest.raises(TypeError):
        PriorityRule()


def test_subclass_without_evaluate_cannot_be_instantiated():
    class Incomplete(PriorityRule):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()


def test_minimal_subclass_works(make_ticket, ctx):
    class Constant(PriorityRule):
        name = "constant"

        def evaluate(self, ticket, ctx):
            return self.result(42, "always 42")

    result = Constant(weight=2).evaluate(make_ticket(), ctx)

    assert result.rule_name == "constant"
    assert result.score == 42
    assert result.reason == "always 42"


def test_negative_weight_is_rejected():
    with pytest.raises(ValueError):
        WaitingTimeRule(weight=-1)


# --- KeywordRule -------------------------------------------------------------

KEYWORDS = [
    Keyword("outage", 90, PRIORITY_HIGH),
    Keyword("urgent", 80),
    Keyword("payment failed", 70),
    Keyword("invoice", 30),
]


@pytest.fixture
def keyword_rule():
    return KeywordRule(KEYWORDS, baseline=10)


@pytest.mark.parametrize(
    "subject, body, expected",
    [
        ("URGENT please", "", 80),  # case-insensitive
        ("Hello", "this is urgent", 80),  # body matches too
        ("Invoice and outage", "", 90),  # highest of several matches wins
        ("Payment   failed", "", 70),  # phrase matches across extra whitespace
        ("Hello", "Question about my invoice.", 30),
        ("Urgently needed", "", 10),  # whole words only
        ("Hello", "nothing to see", 10),  # no match gives the baseline
    ],
)
def test_keyword_scores(keyword_rule, make_ticket, ctx, subject, body, expected):
    result = keyword_rule.evaluate(make_ticket(subject=subject, body=body), ctx)
    assert result.score == expected


def test_keyword_floor_is_set_only_by_floor_keywords(keyword_rule, make_ticket, ctx):
    outage = keyword_rule.evaluate(make_ticket(subject="Production outage"), ctx)
    urgent = keyword_rule.evaluate(make_ticket(subject="Urgent"), ctx)

    assert outage.min_level == PRIORITY_HIGH
    assert "at least HIGH" in outage.reason
    assert urgent.min_level is None


def test_keyword_highest_floor_wins(make_ticket, ctx):
    rule = KeywordRule([Keyword("slow", 20, PRIORITY_MEDIUM), Keyword("down", 50, PRIORITY_HIGH)])
    result = rule.evaluate(make_ticket(body="site is slow and now down"), ctx)
    assert result.min_level == PRIORITY_HIGH


def test_keyword_reason_lists_matches(keyword_rule, make_ticket, ctx):
    result = keyword_rule.evaluate(make_ticket(subject="Urgent invoice"), ctx)
    assert "'urgent' (80)" in result.reason
    assert "'invoice' (30)" in result.reason


@pytest.mark.parametrize(
    "kwargs",
    [
        {"phrase": "x", "score": 101},
        {"phrase": "x", "score": -1},
        {"phrase": "  ", "score": 10},
        {"phrase": "x", "score": 10, "min_level": "CRITICAL"},
    ],
)
def test_invalid_keywords_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Keyword(**kwargs)


# --- RepeatContactRule -------------------------------------------------------


def history_ctx(make_ticket, now, customer, *created_at):
    history = [make_ticket(customer=customer, created_at=c).created_at_datetime for c in created_at]
    return TriageContext(now=now, customer_history={customer: history})


@pytest.mark.parametrize(
    "earlier, expected",
    [
        ([], 0),
        (["2026-10-18T09:00:00Z"], 50),
        (["2026-10-17T09:00:00Z", "2026-10-18T09:00:00Z"], 80),
        (["2026-10-15T09:00:00Z"] * 5, 80),  # 3 or more all score 80
    ],
)
def test_repeat_contact_counts(make_ticket, fixed_now, earlier, expected):
    ticket = make_ticket(customer="CUST-1", created_at="2026-10-19T09:00:00Z")
    ctx = history_ctx(make_ticket, fixed_now, "CUST-1", *earlier, ticket.created_at)

    result = RepeatContactRule().evaluate(ticket, ctx)

    assert result.score == expected


def test_repeat_contact_ignores_tickets_outside_window(make_ticket, fixed_now):
    ticket = make_ticket(customer="CUST-1", created_at="2026-10-19T09:00:00Z")
    ctx = history_ctx(make_ticket, fixed_now, "CUST-1", "2026-10-01T09:00:00Z", ticket.created_at)

    assert RepeatContactRule(window_days=7).evaluate(ticket, ctx).score == 0


def test_repeat_contact_ignores_later_tickets(make_ticket, fixed_now):
    ticket = make_ticket(customer="CUST-1", created_at="2026-10-18T09:00:00Z")
    ctx = history_ctx(make_ticket, fixed_now, "CUST-1", ticket.created_at, "2026-10-19T09:00:00Z")

    assert RepeatContactRule().evaluate(ticket, ctx).score == 0


def test_repeat_contact_customers_do_not_affect_each_other(make_ticket, fixed_now):
    ticket = make_ticket(customer="CUST-1", created_at="2026-10-19T09:00:00Z")
    ctx = TriageContext(
        now=fixed_now,
        customer_history={
            "CUST-1": [ticket.created_at_datetime],
            "CUST-10": [make_ticket(created_at="2026-10-18T09:00:00Z").created_at_datetime] * 3,
        },
    )

    assert RepeatContactRule().evaluate(ticket, ctx).score == 0


def test_repeat_contact_works_without_history(make_ticket, ctx):
    result = RepeatContactRule().evaluate(make_ticket(), ctx)
    assert result.score == 0
    assert "first ticket" in result.reason


@pytest.mark.parametrize(
    "kwargs",
    [{"window_days": 0}, {"steps": {2: 50}}, {"steps": {1: 0, 2: 150}}],
)
def test_repeat_contact_rejects_bad_settings(kwargs):
    with pytest.raises(ValueError):
        RepeatContactRule(**kwargs)


# --- WaitingTimeRule ---------------------------------------------------------


@pytest.mark.parametrize(
    "hours_waiting, expected",
    [(0, 0), (36, 50), (72, 100), (200, 100)],
)
def test_waiting_time_scores(make_ticket, fixed_now, hours_waiting, expected):
    created = fixed_now - timedelta(hours=hours_waiting)
    ticket = make_ticket(created_at=created.isoformat())

    result = WaitingTimeRule(cap_hours=72).evaluate(ticket, TriageContext(now=fixed_now))

    assert result.score == pytest.approx(expected)


def test_waiting_time_future_timestamp_scores_zero(make_ticket, fixed_now):
    ticket = make_ticket(created_at=(fixed_now + timedelta(hours=5)).isoformat())

    result = WaitingTimeRule().evaluate(ticket, TriageContext(now=fixed_now))

    assert result.score == 0
    assert "future" in result.reason


def test_waiting_time_rejects_zero_cap():
    with pytest.raises(ValueError):
        WaitingTimeRule(cap_hours=0)
