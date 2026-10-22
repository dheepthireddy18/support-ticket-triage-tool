"""
Base class for priority rules, plus the small value objects rules share.

A rule looks at one Ticket (and the run's TriageContext) and returns a
RuleResult: a 0-100 score and a human-readable reason. The engine combines
the results; rules never touch the database or the clock themselves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Mapping, Sequence

from ticket_triage.models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM, Ticket

# Lowest to highest. Used to compare levels and to escalate one step.
LEVELS = (PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH)

MIN_SCORE = 0.0
MAX_SCORE = 100.0


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def validate_level(level: str | None) -> str | None:
    """Return the level unchanged, or raise ValueError if it is not a known level."""
    if level is not None and level not in LEVELS:
        raise ValueError(f"unknown priority level {level!r}; expected one of {LEVELS}")
    return level


def validate_score(score: float, what: str = "score") -> float:
    """Return the score as a float, or raise ValueError if it is outside 0-100."""
    score = float(score)
    if not MIN_SCORE <= score <= MAX_SCORE:
        raise ValueError(f"{what} must be between 0 and 100, got {score}")
    return score


def higher_level(a: str, b: str) -> str:
    """Return whichever of two levels is more urgent."""
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def next_level(level: str) -> str | None:
    """Return the level one step more urgent, or None if already HIGH."""
    index = LEVELS.index(level)
    return LEVELS[index + 1] if index + 1 < len(LEVELS) else None


@dataclass(frozen=True)
class RuleResult:
    """One rule's opinion about one ticket."""

    rule_name: str
    score: float | None  # 0-100; None means the rule abstains (no opinion)
    reason: str
    min_level: str | None = None  # Optional floor, e.g. HIGH for outages


@dataclass(frozen=True)
class TriageContext:
    """Everything a rule may need beyond the ticket itself, fixed for one run."""

    now: datetime
    # created_at of every stored ticket, grouped by customer (includes the ticket being scored)
    customer_history: Mapping[str, Sequence[datetime]] = field(default_factory=dict)
    # Earlier results of score_once rules, keyed by (ticket_id, rule_name)
    saved_results: Mapping[tuple[str, str], RuleResult] = field(default_factory=dict)


class PriorityRule(ABC):
    """
    Base class for all priority rules.

    To add a rule: subclass this, set a unique `name`, implement `evaluate()`,
    and register it in ticket_triage.priority.config.RULE_BUILDERS.
    """

    name: ClassVar[str]
    # True for rules whose answer doesn't change over time but is costly to get
    # (e.g. a paid API call). The engine reuses a ticket's saved score from an
    # earlier run instead of evaluating the rule again.
    score_once: ClassVar[bool] = False

    def __init__(self, weight: float = 1.0, enabled: bool = True) -> None:
        if weight < 0:
            raise ValueError(f"{self.name} weight must be 0 or more, got {weight}")
        self.weight = float(weight)
        self.enabled = bool(enabled)

    @abstractmethod
    def evaluate(self, ticket: Ticket, ctx: TriageContext) -> RuleResult:
        """Score one ticket. Must not raise for any valid Ticket."""

    def result(
        self, score: float | None, reason: str, min_level: str | None = None
    ) -> RuleResult:
        """Build a RuleResult tagged with this rule's name."""
        return RuleResult(self.name, score, reason, min_level)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(weight={self.weight}, enabled={self.enabled})"
