"""Keyword rule: urgent words in the subject or body raise the priority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ticket_triage.models import Ticket
from ticket_triage.priority.base import (
    LEVELS,
    PriorityRule,
    RuleResult,
    TriageContext,
    validate_level,
    validate_score,
)


@dataclass(frozen=True)
class Keyword:
    """A word or phrase to look for, the score it gives, and an optional floor."""

    phrase: str
    score: float
    min_level: str | None = None

    def __post_init__(self) -> None:
        if not self.phrase.strip():
            raise ValueError("keyword must not be empty")
        validate_score(self.score, f"score for keyword {self.phrase!r}")
        validate_level(self.min_level)


def _pattern(phrase: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive pattern; spaces in a phrase match any whitespace."""
    words = [re.escape(word) for word in phrase.split()]
    return re.compile(r"\b" + r"\s+".join(words) + r"\b", re.IGNORECASE)


class KeywordRule(PriorityRule):
    """Score a ticket by the most urgent keyword found in its subject or body."""

    name = "keyword"

    def __init__(
        self,
        keywords: Iterable[Keyword],
        baseline: float = 10.0,
        weight: float = 1.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(weight, enabled)
        self.keywords = list(keywords)
        self.baseline = validate_score(baseline, "keyword baseline")
        self._patterns = [(kw, _pattern(kw.phrase)) for kw in self.keywords]

    def evaluate(self, ticket: Ticket, ctx: TriageContext) -> RuleResult:
        text = f"{ticket.subject}\n{ticket.body}"
        matches = [kw for kw, pattern in self._patterns if pattern.search(text)]

        if not matches:
            return self.result(self.baseline, "no priority keywords matched")

        best = max(matches, key=lambda kw: kw.score)
        floors = [kw.min_level for kw in matches if kw.min_level]
        min_level = max(floors, key=LEVELS.index) if floors else None

        matched = ", ".join(f"'{kw.phrase}' ({kw.score:g})" for kw in matches)
        reason = f"matched {matched}"
        if min_level:
            reason += f"; at least {min_level}"
        return self.result(best.score, reason, min_level)
