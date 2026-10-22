"""Waiting-time rule: the longer a ticket has waited, the more urgent it is."""

from __future__ import annotations

from ticket_triage.models import Ticket
from ticket_triage.priority.base import MAX_SCORE, PriorityRule, RuleResult, TriageContext


class WaitingTimeRule(PriorityRule):
    """Map hours since created_at linearly to 0-100, reaching 100 at `cap_hours`."""

    name = "waiting_time"

    def __init__(self, cap_hours: float = 72, weight: float = 1.0, enabled: bool = True) -> None:
        super().__init__(weight, enabled)
        if cap_hours <= 0:
            raise ValueError(f"waiting_time cap_hours must be more than 0, got {cap_hours}")
        self.cap_hours = float(cap_hours)

    def evaluate(self, ticket: Ticket, ctx: TriageContext) -> RuleResult:
        hours = (ctx.now - ticket.created_at_datetime).total_seconds() / 3600

        if hours < 0:
            return self.result(0.0, "created_at is in the future; treated as just arrived")

        score = min(MAX_SCORE, hours / self.cap_hours * MAX_SCORE)
        reason = f"waiting {hours:.1f}h"
        if hours >= self.cap_hours:
            reason += f" (at or past the {self.cap_hours:g}h cap)"
        return self.result(score, reason)
