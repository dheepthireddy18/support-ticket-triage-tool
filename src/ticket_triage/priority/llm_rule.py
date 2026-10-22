"""
Optional LLM rule: ask Claude how urgent a ticket is.

Disabled by default. When enabled it still abstains (score None) if the
`anthropic` package is not installed, no credentials are set, or the call
fails for any reason, so the tool always works with the plain rules alone.
Install with: pip install -e ".[llm]"

Credentials, checked in this order:
1. The class Azure (Microsoft Foundry) sandbox: AZURE_AI_KEY, AZURE_AI_ENDPOINT
   and AZURE_AI_DEPLOYMENT, read from the environment or a .env file in the
   folder the tool runs from. Calls go to {endpoint}/anthropic/v1/messages and
   the deployment name is used as the model.
2. Anthropic's own API: ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN, using the
   `model` setting.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ticket_triage.models import Ticket
from ticket_triage.priority.base import PriorityRule, RuleResult, TriageContext, validate_score

logger = logging.getLogger(__name__)

AZURE_KEY_VAR = "AZURE_AI_KEY"
AZURE_ENDPOINT_VAR = "AZURE_AI_ENDPOINT"
AZURE_DEPLOYMENT_VAR = "AZURE_AI_DEPLOYMENT"
AZURE_VARS = (AZURE_KEY_VAR, AZURE_ENDPOINT_VAR, AZURE_DEPLOYMENT_VAR)

SYSTEM_PROMPT = (
    "You triage customer support tickets. Rate how urgently the support team "
    "should act on the ticket, from 0 (can wait indefinitely) to 100 (drop "
    "everything: outage, data loss, security, or money taken in error). "
    "Give a one-sentence reason a support manager can read."
)

# Structured outputs don't support minimum/maximum on integers; the 0-100 range is
# stated here and in the prompt, and enforced by validate_score() on the reply.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "urgency": {"type": "integer", "description": "Urgency from 0 to 100"},
        "reason": {"type": "string"},
    },
    "required": ["urgency", "reason"],
    "additionalProperties": False,
}


def load_dotenv_file() -> None:
    """Load .env from the current folder into the environment, if python-dotenv is installed.
    Values already set in the environment win over the file."""
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    load_dotenv(find_dotenv(usecwd=True))


class LLMUrgencyRule(PriorityRule):
    """Score a ticket with Claude. Abstains instead of raising on any problem."""

    name = "llm_urgency"
    score_once = True  # A ticket's text doesn't change: one paid call per ticket

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
        self._skip_reason: str | None = None

    def _build_client(self) -> Any | None:
        """
        Build the SDK client from the environment. Returns None (and sets
        _skip_reason) when there is nothing usable. Never logs the key.
        """
        load_dotenv_file()

        azure = {var: os.environ.get(var, "").strip() for var in AZURE_VARS}
        missing = [var for var, value in azure.items() if not value]
        use_azure = len(missing) < len(AZURE_VARS)  # Any Azure setting means Azure was intended
        if use_azure and missing:
            self._skip_reason = f"incomplete Azure settings, missing {', '.join(missing)}"
            return None
        if not use_azure and not (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ):
            self._skip_reason = "no Azure or Anthropic API key settings found"
            return None

        try:
            import anthropic
        except ImportError:
            self._skip_reason = "anthropic package not installed (pip install -e \".[llm]\")"
            return None

        if use_azure:
            # The SDK appends /v1/messages and sends the key as x-api-key, matching the
            # class doc's {endpoint}/anthropic/v1/messages call.
            self.model = azure[AZURE_DEPLOYMENT_VAR]
            return anthropic.Anthropic(
                api_key=azure[AZURE_KEY_VAR],
                base_url=azure[AZURE_ENDPOINT_VAR].rstrip("/") + "/anthropic",
                timeout=self.timeout_seconds,
                max_retries=1,
            )
        return anthropic.Anthropic(timeout=self.timeout_seconds, max_retries=1)

    def _get_client(self) -> Any | None:
        if self._client is None and self._skip_reason is None:
            self._client = self._build_client()  # Built once; a skip reason is remembered too
        return self._client

    def evaluate(self, ticket: Ticket, ctx: TriageContext) -> RuleResult:
        client = self._get_client()
        if client is None:
            return self.result(None, f"skipped: {self._skip_reason}")

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
