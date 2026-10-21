"""
Combine rule results into one priority score and level per ticket.

Disagreements between rules are settled by a weighted average of the rules
that gave a score. The one exception is a floor (`min_level`): a hard rule such
as "outages are always HIGH" wins whatever the average says.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ticket_triage.models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM, Ticket
from ticket_triage.priority.base import (
    PriorityRule,
    RuleResult,
    TriageContext,
    higher_level,
    validate_score,
)


@dataclass
class PriorityResult:
    """The engine's verdict for one ticket, with every rule's opinion kept."""

    ticket_id: str
    score: float
    level: str
    rule_results: list[RuleResult] = field(default_factory=list)


class PriorityEngine:
    """Run every enabled rule on a ticket and combine their scores."""

    def __init__(
        self,
        rules: Iterable[PriorityRule],
        high_threshold: float = 70.0,
        medium_threshold: float = 40.0,
    ) -> None:
        self.rules = list(rules)
        names = [rule.name for rule in self.rules]
        if len(names) != len(set(names)):
            raise ValueError(f"rule names must be unique, got {names}")

        self.high_threshold = validate_score(high_threshold, "high_threshold")
        self.medium_threshold = validate_score(medium_threshold, "medium_threshold")
        if self.medium_threshold > self.high_threshold:
            raise ValueError("medium_threshold must not be above high_threshold")

    def level_for(self, score: float) -> str:
        """Map a 0-100 score to a level using the thresholds."""
        if score >= self.high_threshold:
            return PRIORITY_HIGH
        if score >= self.medium_threshold:
            return PRIORITY_MEDIUM
        return PRIORITY_LOW

    def score(self, ticket: Ticket, ctx: TriageContext) -> PriorityResult:
        enabled = [rule for rule in self.rules if rule.enabled]
        results = [rule.evaluate(ticket, ctx) for rule in enabled]

        weighted = [
            (rule.weight, result.score)
            for rule, result in zip(enabled, results)
            if result.score is not None
        ]
        total_weight = sum(weight for weight, _ in weighted)
        if total_weight > 0:
            score = sum(weight * s for weight, s in weighted) / total_weight
        else:
            score = 0.0  # Every rule abstained (or has weight 0)

        level = self.level_for(score)
        for result in results:
            if result.min_level:
                level = higher_level(level, result.min_level)

        return PriorityResult(ticket.id, score, level, results)


def default_rules() -> list[PriorityRule]:
    """The built-in rules with their default settings (no database needed)."""
    from ticket_triage.priority.config import build_rules, default_settings

    return build_rules(*default_settings())


def build_default_engine() -> PriorityEngine:
    """An engine with the default rules and thresholds."""
    return PriorityEngine(default_rules())
