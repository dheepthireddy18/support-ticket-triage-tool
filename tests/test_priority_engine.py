"""Tests for combining rule scores, including cases where rules disagree."""

import pytest

from ticket_triage.models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM
from ticket_triage.priority.base import PriorityRule, TriageContext
from ticket_triage.priority.engine import PriorityEngine
from ticket_triage.priority.keyword_rule import Keyword, KeywordRule
from ticket_triage.priority.repeat_rule import RepeatContactRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule


class Fixed(PriorityRule):
    """A rule that always returns the same score (None = abstain)."""

    def __init__(self, name, score, weight=1.0, enabled=True, min_level=None):
        self.name = name
        super().__init__(weight, enabled)
        self.score = score
        self.min_level = min_level

    def evaluate(self, ticket, ctx):
        return self.result(self.score, f"fixed {self.score}", self.min_level)


def test_disagreeing_rules_are_averaged(make_ticket, ctx):
    # Keyword says very urgent, repeat-contact says not at all.
    engine = PriorityEngine([Fixed("keyword", 90), Fixed("repeat", 0)])

    result = engine.score(make_ticket(), ctx)

    assert result.score == pytest.approx(45)
    assert result.level == PRIORITY_MEDIUM


def test_weights_decide_who_wins_a_disagreement(make_ticket, ctx):
    ticket = make_ticket()
    keyword_heavy = PriorityEngine([Fixed("keyword", 90, weight=3), Fixed("repeat", 0)])
    repeat_heavy = PriorityEngine([Fixed("keyword", 90), Fixed("repeat", 0, weight=3)])

    assert keyword_heavy.score(ticket, ctx).score == pytest.approx(67.5)
    assert keyword_heavy.score(ticket, ctx).level == PRIORITY_MEDIUM
    assert repeat_heavy.score(ticket, ctx).score == pytest.approx(22.5)
    assert repeat_heavy.score(ticket, ctx).level == PRIORITY_LOW


def test_floor_beats_a_low_average(make_ticket, ctx):
    engine = PriorityEngine(
        [Fixed("keyword", 20, min_level=PRIORITY_HIGH), Fixed("repeat", 0), Fixed("wait", 0)]
    )

    result = engine.score(make_ticket(), ctx)

    assert result.score == pytest.approx(20 / 3)
    assert result.level == PRIORITY_HIGH


def test_floor_never_lowers_a_level(make_ticket, ctx):
    engine = PriorityEngine([Fixed("keyword", 95, min_level=PRIORITY_MEDIUM)])
    assert engine.score(make_ticket(), ctx).level == PRIORITY_HIGH


def test_all_rules_abstaining_gives_low(make_ticket, ctx):
    engine = PriorityEngine([Fixed("a", None), Fixed("b", None)])

    result = engine.score(make_ticket(), ctx)

    assert result.score == 0
    assert result.level == PRIORITY_LOW


def test_abstaining_rule_does_not_dilute_the_average(make_ticket, ctx):
    engine = PriorityEngine([Fixed("keyword", 80), Fixed("llm", None, weight=5)])
    assert engine.score(make_ticket(), ctx).score == pytest.approx(80)


def test_disabled_rule_is_not_run(make_ticket, ctx):
    class Exploding(PriorityRule):
        name = "exploding"

        def evaluate(self, ticket, ctx):
            raise AssertionError("disabled rule was run")

    engine = PriorityEngine([Fixed("keyword", 50), Exploding(enabled=False)])

    result = engine.score(make_ticket(), ctx)

    assert result.score == 50
    assert [r.rule_name for r in result.rule_results] == ["keyword"]


def test_zero_weight_rules_give_low(make_ticket, ctx):
    engine = PriorityEngine([Fixed("a", 100, weight=0)])
    assert engine.score(make_ticket(), ctx).level == PRIORITY_LOW


@pytest.mark.parametrize(
    "score, level",
    [
        (39.99, PRIORITY_LOW),
        (40, PRIORITY_MEDIUM),
        (69.99, PRIORITY_MEDIUM),
        (70, PRIORITY_HIGH),
        (0, PRIORITY_LOW),
        (100, PRIORITY_HIGH),
    ],
)
def test_level_boundaries(make_ticket, ctx, score, level):
    engine = PriorityEngine([Fixed("a", score)])
    assert engine.score(make_ticket(), ctx).level == level


def test_every_rule_result_is_kept(make_ticket, ctx):
    engine = PriorityEngine([Fixed("a", 10), Fixed("b", None), Fixed("c", 30)])
    result = engine.score(make_ticket(), ctx)
    assert [(r.rule_name, r.score) for r in result.rule_results] == [
        ("a", 10),
        ("b", None),
        ("c", 30),
    ]


def test_duplicate_rule_names_are_rejected():
    with pytest.raises(ValueError):
        PriorityEngine([Fixed("a", 1), Fixed("a", 2)])


def test_thresholds_must_be_ordered():
    with pytest.raises(ValueError):
        PriorityEngine([], high_threshold=30, medium_threshold=60)


def test_sample_tickets_get_documented_levels(sample_tickets, fixed_now):
    """The worked examples used in the demo (empty database, now = 2026-10-19T12:00Z)."""
    history = {}
    for ticket in sample_tickets:
        history.setdefault(ticket.customer, []).append(ticket.created_at_datetime)
    ctx = TriageContext(now=fixed_now, customer_history=history)
    engine = PriorityEngine(
        [
            KeywordRule(
                [
                    Keyword("outage", 90, PRIORITY_HIGH),
                    Keyword("urgent", 80),
                    Keyword("locked out", 80),
                    Keyword("payment failed", 70),
                    Keyword("charged", 60),
                    Keyword("invoice", 30),
                    Keyword("feature request", 10),
                ]
            ),
            RepeatContactRule(),
            WaitingTimeRule(),
        ]
    )

    levels = {t.subject: engine.score(t, ctx) for t in sample_tickets}

    outage = levels["URGENT: production outage"]
    assert outage.score < 40  # The average alone would make it LOW...
    assert outage.level == PRIORITY_HIGH  # ...the outage floor makes it HIGH.
    assert levels["Unable to log in"].level == PRIORITY_LOW
    assert levels["Payment failed twice"].level == PRIORITY_LOW
    assert levels["Question about invoice"].level == PRIORITY_LOW
    # The waiting rule (100) disagrees with keyword (10) and repeat (0); the average wins.
    feature = levels["Feature request: dark mode"]
    assert feature.score == pytest.approx(110 / 3)
    assert feature.level == PRIORITY_LOW
    # The login ticket is CUST-101's second ticket that morning.
    login_repeat = next(
        r for r in levels["Unable to log in"].rule_results if r.rule_name == "repeat_contact"
    )
    assert login_repeat.score == 50
