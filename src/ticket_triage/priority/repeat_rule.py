"""Repeat-contact rule: a customer writing in again usually means the issue isn't solved."""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from ticket_triage.models import Ticket
from ticket_triage.priority.base import (
    PriorityRule,
    RuleResult,
    TriageContext,
    validate_score,
)

DEFAULT_STEPS = {1: 0.0, 2: 50.0, 3: 80.0}


class RepeatContactRule(PriorityRule):
    """
    Count this customer's tickets in the window before this ticket (including
    this one) and map the count to a score. The history comes from the
    TriageContext, so whoever builds the context decides which tickets count.
    """

    name = "repeat_contact"

    def __init__(
        self,
        window_days: float = 7,
        steps: Mapping[int, float] | None = None,
        weight: float = 1.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(weight, enabled)
        if window_days <= 0:
            raise ValueError(f"repeat_contact window_days must be more than 0, got {window_days}")
        self.window = timedelta(days=window_days)

        steps = dict(DEFAULT_STEPS if steps is None else steps)
        if not steps or min(steps) != 1 or any(count < 1 for count in steps):
            raise ValueError("repeat_contact steps must start at a count of 1")
        self.steps = {
            int(count): validate_score(score, f"repeat_contact score for {count} tickets")
            for count, score in steps.items()
        }

    def evaluate(self, ticket: Ticket, ctx: TriageContext) -> RuleResult:
        created = ticket.created_at_datetime
        start = created - self.window
        history = ctx.customer_history.get(ticket.customer, ())
        # The ticket itself is in the history; max() guards a context built without it.
        count = max(1, sum(1 for seen in history if start <= seen <= created))

        step = max(c for c in self.steps if c <= count)
        score = self.steps[step]
        days = self.window.total_seconds() / 86400
        if count == 1:
            reason = f"first ticket from {ticket.customer} in {days:g} days"
        else:
            reason = f"{count} tickets from {ticket.customer} in {days:g} days"
        return self.result(score, reason)
