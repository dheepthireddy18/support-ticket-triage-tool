"""Tests for the optional LLM urgency rule. No network calls: the client is a fake."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

from ticket_triage.priority import llm_rule
from ticket_triage.priority.engine import PriorityEngine
from ticket_triage.priority.llm_rule import LLMUrgencyRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule

REAL_DOTENV_LOADER = llm_rule.load_dotenv_file  # Saved before the autouse fixture replaces it


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
    """Start every test with no credentials, and never read the real .env file."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", *llm_rule.AZURE_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_rule, "load_dotenv_file", lambda: None)


@pytest.fixture
def fake_sdk(monkeypatch):
    """Replace the anthropic package with a fake that records how the client is built."""
    built = []

    class FakeAnthropic:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.messages = FakeMessages(fake_client(json.dumps({"urgency": 60, "reason": "ok"})).messages.response)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    return built


@pytest.fixture
def azure_env(monkeypatch):
    monkeypatch.setenv("AZURE_AI_KEY", "test-key-123")
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "https://class-sandbox.example.com/")
    monkeypatch.setenv("AZURE_AI_DEPLOYMENT", "claude-opus-5-5-fast")


def test_abstains_without_any_credentials(make_ticket, ctx, fake_sdk):
    result = LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert result.score is None
    assert result.reason == "skipped: no Azure or Anthropic API key settings found"
    assert fake_sdk == []


def test_azure_settings_build_a_foundry_client(make_ticket, ctx, fake_sdk, azure_env):
    rule = LLMUrgencyRule(model="claude-haiku-5-5", timeout_seconds=7)

    result = rule.evaluate(make_ticket(), ctx)

    assert result.score == 60
    assert fake_sdk == [
        {
            "api_key": "test-key-123",
            "base_url": "https://class-sandbox.example.com/anthropic",
            "timeout": 7.0,
            "max_retries": 1,
        }
    ]
    # The deployment name replaces the configured model
    assert rule._client.messages.calls[0]["model"] == "claude-opus-5-5-fast"


def test_azure_settings_win_over_anthropic_key(make_ticket, ctx, fake_sdk, azure_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-other")

    LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert fake_sdk[0]["api_key"] == "test-key-123"


@pytest.mark.parametrize("unset", llm_rule.AZURE_VARS)
def test_incomplete_azure_settings_abstain_and_name_the_gap(
    make_ticket, ctx, fake_sdk, azure_env, monkeypatch, unset
):
    monkeypatch.delenv(unset)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-other")  # Not used: Azure was clearly intended

    result = LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert result.score is None
    assert result.reason == f"skipped: incomplete Azure settings, missing {unset}"
    assert fake_sdk == []


def test_blank_azure_value_counts_as_missing(make_ticket, ctx, fake_sdk, azure_env, monkeypatch):
    monkeypatch.setenv("AZURE_AI_KEY", "   ")

    result = LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert "missing AZURE_AI_KEY" in result.reason


def test_anthropic_key_used_when_no_azure_settings(make_ticket, ctx, fake_sdk, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    rule = LLMUrgencyRule(model="claude-haiku-5-5")

    rule.evaluate(make_ticket(), ctx)

    assert fake_sdk == [{"timeout": 10.0, "max_retries": 1}]
    assert rule._client.messages.calls[0]["model"] == "claude-haiku-5-5"


def test_missing_sdk_abstains(make_ticket, ctx, azure_env, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import anthropic -> ImportError

    result = LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert result.score is None
    assert "anthropic package not installed" in result.reason


def test_client_is_built_once_per_rule(make_ticket, ctx, fake_sdk, azure_env):
    rule = LLMUrgencyRule()

    rule.evaluate(make_ticket(), ctx)
    rule.evaluate(make_ticket(), ctx)

    assert len(fake_sdk) == 1


def test_settings_are_read_from_dotenv_file(make_ticket, ctx, fake_sdk, monkeypatch, tmp_path):
    pytest.importorskip("dotenv")
    (tmp_path / ".env").write_text(
        "AZURE_AI_KEY=from-file\n"
        "AZURE_AI_ENDPOINT=https://file.example.com\n"
        "AZURE_AI_DEPLOYMENT=claude-opus-5-5-fast\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # load_dotenv writes to os.environ; give it a throwaway copy so nothing leaks to other tests.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(llm_rule, "load_dotenv_file", REAL_DOTENV_LOADER)

    LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert fake_sdk[0]["api_key"] == "from-file"
    assert fake_sdk[0]["base_url"] == "https://file.example.com/anthropic"


def test_environment_wins_over_dotenv_file(make_ticket, ctx, fake_sdk, monkeypatch, tmp_path):
    pytest.importorskip("dotenv")
    (tmp_path / ".env").write_text(
        "AZURE_AI_KEY=from-file\nAZURE_AI_ENDPOINT=https://file.example.com\n"
        "AZURE_AI_DEPLOYMENT=d\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", os.environ.copy())
    os.environ["AZURE_AI_KEY"] = "from-environment"
    monkeypatch.setattr(llm_rule, "load_dotenv_file", REAL_DOTENV_LOADER)

    LLMUrgencyRule().evaluate(make_ticket(), ctx)

    assert fake_sdk[0]["api_key"] == "from-environment"


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


def test_output_schema_avoids_unsupported_integer_limits():
    """The API rejects minimum/maximum on integer fields (400 invalid_request_error)."""
    urgency = llm_rule.OUTPUT_SCHEMA["properties"]["urgency"]
    assert urgency["type"] == "integer"
    assert "minimum" not in urgency and "maximum" not in urgency
