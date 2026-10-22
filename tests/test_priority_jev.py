"""Tests for the optional Jev decision-model rule. No network calls: the transport is a fake."""

import io
import os
import urllib.error

import pytest

from ticket_triage.priority import jev_rule, llm_rule
from ticket_triage.priority.jev_rule import TYPESAFE_KEY_VAR, URGENCY_LEVELS, JevUrgencyRule

REAL_DOTENV_LOADER = llm_rule.load_dotenv_file  # Saved before the autouse fixture replaces it


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    """Start every test with no key, and never read the real .env file."""
    monkeypatch.delenv(TYPESAFE_KEY_VAR, raising=False)
    monkeypatch.setattr(jev_rule, "load_dotenv_file", lambda: None)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(TYPESAFE_KEY_VAR, "ts-test-key")


class FakeTransport:
    """Records each request and returns a canned Jev answer (or raises)."""

    def __init__(self, score=3.0, confidence=0.8, response=None, error=None):
        self.calls = []
        self.error = error
        self.response = response or {
            "model": "jev-1.13.0",
            "answers": {
                "urgency": {
                    "type": "score",
                    "score": score,
                    "confidence": confidence,
                    "legend": {str(i): level for i, level in enumerate(URGENCY_LEVELS)},
                }
            },
        }

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if self.error:
            raise self.error
        return self.response


def test_abstains_without_a_key(make_ticket, ctx):
    transport = FakeTransport()

    result = JevUrgencyRule(transport=transport).evaluate(make_ticket(), ctx)

    assert result.score is None
    assert result.reason == "skipped: no TYPESAFE_API_KEY setting found"
    assert transport.calls == []


def test_sends_the_documented_request(make_ticket, ctx, key):
    transport = FakeTransport()
    rule = JevUrgencyRule(model="jev-latest", timeout_seconds=7, transport=transport)

    rule.evaluate(make_ticket(subject="Site down", body="Nothing loads."), ctx)

    call = transport.calls[0]
    assert call["url"] == "https://api.typesafe.ai/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer ts-test-key"
    assert call["timeout"] == 7
    body = call["body"]
    assert body["model"] == "jev-latest"
    assert body["state"] == "Subject: Site down\n\nNothing loads."
    question = body["questions"]["urgency"]
    assert question["type"] == "score"
    assert question["criteria"] == URGENCY_LEVELS


@pytest.mark.parametrize(
    "position, expected",
    [(0.0, 0.0), (1.0, 25.0), (2.6, 65.0), (3.0, 75.0), (4.0, 100.0)],
)
def test_rubric_position_maps_onto_0_to_100(make_ticket, ctx, key, position, expected):
    rule = JevUrgencyRule(transport=FakeTransport(score=position))

    assert rule.evaluate(make_ticket(), ctx).score == pytest.approx(expected)


def test_reason_names_the_closest_level_and_confidence(make_ticket, ctx, key):
    rule = JevUrgencyRule(transport=FakeTransport(score=3.4, confidence=0.82))

    reason = rule.evaluate(make_ticket(), ctx).reason

    assert reason == "Jev: closest to 'Urgent' (position 3.40 of 4), confidence 0.82"


def test_low_confidence_abstains_when_a_minimum_is_set(make_ticket, ctx, key):
    rule = JevUrgencyRule(min_confidence=0.6, transport=FakeTransport(confidence=0.3))

    result = rule.evaluate(make_ticket(), ctx)

    assert result.score is None
    assert result.reason == "skipped: Jev confidence 0.30 below 0.6"


def test_confidence_is_ignored_by_default(make_ticket, ctx, key):
    rule = JevUrgencyRule(transport=FakeTransport(score=2.0, confidence=0.1))
    assert rule.evaluate(make_ticket(), ctx).score == pytest.approx(50)


def test_http_error_abstains_with_status(make_ticket, ctx, key):
    error = urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    result = JevUrgencyRule(transport=FakeTransport(error=error)).evaluate(make_ticket(), ctx)

    assert result.score is None
    assert result.reason == "skipped: Jev call failed (HTTP 401)"


@pytest.mark.parametrize(
    "transport",
    [
        FakeTransport(error=urllib.error.URLError("no network")),
        FakeTransport(error=TimeoutError()),
        FakeTransport(response={"answers": {}}),  # No urgency answer
        FakeTransport(response={"error": "bad request"}),
        FakeTransport(score=7.0),  # Outside the 5-level rubric
        FakeTransport(score=-1.0),
        FakeTransport(score="high"),
    ],
)
def test_any_failure_abstains(make_ticket, ctx, key, transport):
    result = JevUrgencyRule(transport=transport).evaluate(make_ticket(), ctx)

    assert result.score is None
    assert result.reason.startswith("skipped: Jev call failed")


def test_custom_base_url(make_ticket, ctx, key):
    transport = FakeTransport()
    JevUrgencyRule(base_url="https://gateway.example.com/", transport=transport).evaluate(make_ticket(), ctx)
    assert transport.calls[0]["url"] == "https://gateway.example.com/v1/systemone"


def test_key_is_read_once_per_rule(make_ticket, ctx, key, monkeypatch):
    reads = []
    monkeypatch.setattr(jev_rule, "load_dotenv_file", lambda: reads.append(1))
    rule = JevUrgencyRule(transport=FakeTransport())

    rule.evaluate(make_ticket(), ctx)
    rule.evaluate(make_ticket(), ctx)

    assert reads == [1]


def test_key_is_read_from_dotenv_file(make_ticket, ctx, monkeypatch, tmp_path):
    pytest.importorskip("dotenv")
    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # load_dotenv writes to os.environ; give it a throwaway copy so nothing leaks to other tests.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(jev_rule, "load_dotenv_file", REAL_DOTENV_LOADER)
    transport = FakeTransport()

    JevUrgencyRule(transport=transport).evaluate(make_ticket(), ctx)

    assert transport.calls[0]["headers"]["Authorization"] == "Bearer from-file"


@pytest.mark.parametrize("kwargs", [{"timeout_seconds": 0}, {"min_confidence": 1.5}, {"min_confidence": -0.1}])
def test_bad_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        JevUrgencyRule(**kwargs)


def test_engine_uses_jev_score_alongside_other_rules(make_ticket, fixed_now, key):
    from datetime import timedelta

    from ticket_triage.priority.base import TriageContext
    from ticket_triage.priority.engine import PriorityEngine
    from ticket_triage.priority.waiting_rule import WaitingTimeRule

    ticket = make_ticket(created_at=(fixed_now - timedelta(hours=36)).isoformat())  # waiting = 50
    engine = PriorityEngine([WaitingTimeRule(cap_hours=72), JevUrgencyRule(transport=FakeTransport(score=4.0))])

    result = engine.score(ticket, TriageContext(now=fixed_now))

    assert result.score == pytest.approx(75)  # (50 + 100) / 2


def test_ssl_context_uses_os_certificates_when_truststore_is_installed():
    pytest.importorskip("truststore")
    import truststore

    assert isinstance(jev_rule.ssl_context(), truststore.SSLContext)


def test_ssl_context_falls_back_to_default_without_truststore(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "truststore", None)  # import truststore -> ImportError
    assert jev_rule.ssl_context() is None
