"""
Optional LLM rule: ask Claude how urgent a ticket is.

Disabled by default. When enabled it still abstains (score None) if the
`anthropic` package is not installed, no API credentials are set, or the call
fails for any reason, so the tool always works with the plain rules alone.
Install with: pip install -e ".[llm]"
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ticket_triage.models import Ticket
from ticket_triage.priority.base import PriorityRule, RuleResult, TriageContext, validate_score

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You triage customer support tickets. Rate how urgently the support team "
    "should act on the ticket, from 0 (can wait indefinitely) to 100 (drop "
    "everything: outage, data loss, security, or money taken in error). "
    "Give a one-sentence reason a support manager can read."
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "urgency": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string"},
    },
    "required": ["urgency", "reason"],
    "additionalProperties": False,
}


def _has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class LLMUrgencyRule(PriorityRule):
    """Score a ticket with Claude. Abstains instead of raising on any problem."""

    name = "llm_urgency"

    def __init__(
        self,
        model: str = "claude-haiku-5-5",
        timeout_seconds: float = 10.0,
        client: Any | None = None,
        weight: float = 1.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(weight, enabled)
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self._client = client  # Injected in tests; built lazily otherwise

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not _has_credentials():
            return None
        try:
            import anthropic
        except ImportError:
            return None
        self._client = anthropic.Anthropic(timeout=self.timeout_seconds, max_retries=1)
        return self._client

    def evaluate(self, ticket: Ticket, ctx: TriageContext) -> RuleResult:
        client = self._get_client()
        if client is None:
            return self.result(None, "skipped: no Anthropic API key or SDK installed")

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Subject: {ticket.subject}\n\n{ticket.body}",
                    }
                ],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                },
            )
            if response.stop_reason == "refusal":
                return self.result(None, "skipped: model declined to rate this ticket")

            text = next(block.text for block in response.content if block.type == "text")
            data = json.loads(text)
            score = validate_score(data["urgency"], "LLM urgency")
            reason = str(data["reason"]).strip() or "no reason given"
        except Exception as exc:  # Any failure means "no opinion", never a failed run
            logger.warning("LLM urgency rule failed for ticket %s: %s", ticket.id, exc)
            return self.result(None, f"skipped: LLM call failed ({type(exc).__name__})")

        return self.result(score, f"LLM: {reason}")
