"""
Optional decision-model rule: ask TypeSafe's Jev where a ticket sits on an
urgency rubric.

Jev doesn't write text. It answers typed questions: for a "score" question it
returns the ticket's position along the rubric levels we send (0 for the
first level up to len(levels) - 1 for the last, possibly in between), plus how
confident it is. This rule maps that position onto 0-100.

Disabled by default. When enabled it abstains (score None) if TYPESAFE_API_KEY
isn't set (in the environment or .env) or the call fails for any reason, so the
tool always works with the other rules alone. Uses only the standard library.
API: POST https://api.typesafe.ai/v1/systemone (https://docs.typesafe.ai).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from ticket_triage.models import Ticket
from ticket_triage.priority.base import MAX_SCORE, PriorityRule, RuleResult, TriageContext
from ticket_triage.priority.llm_rule import load_dotenv_file

logger = logging.getLogger(__name__)

TYPESAFE_KEY_VAR = "TYPESAFE_API_KEY"
DEFAULT_BASE_URL = "https://api.typesafe.ai"
QUESTION = "urgency"

INSTRUCTIONS = "How urgently should the support team act on this customer support ticket?"

# Ordered from least to most urgent; Jev's score is a position along this list.
URGENCY_LEVELS = [
    "Can wait: feature requests, feedback or general questions with no time pressure.",
    "Routine: a normal question or minor issue the customer can work around.",
    "Important: the customer is partly blocked, or a billing problem needs prompt attention.",
    "Urgent: the customer cannot use the product, or money was taken in error.",
    "Critical: an outage, data loss, a security problem, or many users unable to work.",
]

# (url, headers, body, timeout) -> parsed JSON response. Injected in tests.
Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def urllib_transport(
    url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class JevUrgencyRule(PriorityRule):
    """Score a ticket with TypeSafe's Jev. Abstains instead of raising on any problem."""

    name = "jev_urgency"

    def __init__(
        self,
        model: str = "jev-latest",
        timeout_seconds: float = 10.0,
        min_confidence: float = 0.0,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport | None = None,
        weight: float = 1.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(weight, enabled)
        if timeout_seconds <= 0:
            raise ValueError(f"jev_urgency timeout_seconds must be more than 0, got {timeout_seconds}")
        if not 0 <= min_confidence <= 1:
            raise ValueError(f"jev_urgency min_confidence must be between 0 and 1, got {min_confidence}")
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self.min_confidence = float(min_confidence)
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self._transport = transport or urllib_transport
        self._key: str | None = None
        self._key_checked = False

    def _api_key(self) -> str | None:
        """Read the key once per rule. Never logged."""
        if not self._key_checked:
            load_dotenv_file()
            self._key = os.environ.get(TYPESAFE_KEY_VAR, "").strip() or None
            self._key_checked = True
        return self._key

    def evaluate(self, ticket: Ticket, ctx: TriageContext) -> RuleResult:
        key = self._api_key()
        if key is None:
            return self.result(None, f"skipped: no {TYPESAFE_KEY_VAR} setting found")

        body = {
            "model": self.model,
            "state": f"Subject: {ticket.subject}\n\n{ticket.body}",
            "questions": {
                QUESTION: {"type": "score", "instructions": INSTRUCTIONS, "criteria": URGENCY_LEVELS}
            },
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        try:
            answer = self._transport(self.url, headers, body, self.timeout_seconds)["answers"][QUESTION]
            position = float(answer["score"])
            top = len(URGENCY_LEVELS) - 1
            if not 0 <= position <= top:
                raise ValueError(f"score {position} is outside 0-{top}")
            confidence = answer.get("confidence")
        except urllib.error.HTTPError as exc:
            logger.warning("Jev urgency rule failed for ticket %s: HTTP %s", ticket.id, exc.code)
            return self.result(None, f"skipped: Jev call failed (HTTP {exc.code})")
        except Exception as exc:  # Any failure means "no opinion", never a failed run
            logger.warning("Jev urgency rule failed for ticket %s: %s", ticket.id, exc)
            return self.result(None, f"skipped: Jev call failed ({type(exc).__name__})")

        if confidence is not None and float(confidence) < self.min_confidence:
            return self.result(
                None, f"skipped: Jev confidence {float(confidence):.2f} below {self.min_confidence:g}"
            )

        level = URGENCY_LEVELS[round(position)].split(":")[0]
        reason = f"Jev: closest to {level!r} (position {position:.2f} of {top})"
        if confidence is not None:
            reason += f", confidence {float(confidence):.2f}"
        return self.result(position / top * MAX_SCORE, reason)
