"""Tests for the optional LLM urgency rule. No network calls: the client is a fake."""

import json
from types import SimpleNamespace

import pytest

from ticket_triage.priority.engine import PriorityEngine
from ticket_triage.priority.llm_rule import LLMUrgencyRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(text=None, stop_reason="end_turn", error=None):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    response = SimpleNamespace(stop_reason=stop_reason, content=content)
    return SimpleNamespace(messages=FakeMessages(response, error))


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def test_abstains_without_api_key(make_ticket, ctx):
    result = LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert result.score is None
    assert "no Anthropic API key" in result.reason


def test_parses_score_and_reason(make_ticket, ctx):
    client = fake_client(json.dumps({"urgency": 85, "reason": "Customer cannot work."}))

    result = LLMUrgencyRule(client=client).evaluate(make_ticket(subject="Down", body="All down"), ctx)

    assert result.score == 85
    assert result.reason == "LLM: Customer cannot work."
    request = client.messages.calls[0]
    assert request["model"] == "claude-haiku-5-5"
    assert "Subject: Down" in request["messages"][0]["content"]
    assert request["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    "client",
    [
        fake_client(error=RuntimeError("network down")),
        fake_client(text="not json"),
        fake_client(text=json.dumps({"urgency": 250, "reason": "x"})),
        fake_client(text=json.dumps({"reason": "missing urgency"})),
        fake_client(stop_reason="refusal"),
    ],
)
def test_any_failure_abstains(make_ticket, ctx, client):
    result = LLMUrgencyRule(client=client).evaluate(make_ticket(), ctx)

    assert result.score is None
    assert result.reason.startswith("skipped")


def test_engine_ignores_abstaining_llm(make_ticket, fixed_now):
    from datetime import timedelta

    from ticket_triage.priority.base import TriageContext

    ticket = make_ticket(created_at=(fixed_now - timedelta(hours=36)).isoformat())
    engine = PriorityEngine([WaitingTimeRule(cap_hours=72), LLMUrgencyRule(weight=5)])

    result = engine.score(ticket, TriageContext(now=fixed_now))

    assert result.score == pytest.approx(50)
