"""Shared pytest fixtures for the priority rule tests."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ticket_triage.models import Ticket
from ticket_triage.priority.base import TriageContext

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_tickets.json"
RULES_FILE = Path(__file__).resolve().parents[1] / "data" / "priority_rules.json"

FIXED_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixed_now():
    return FIXED_NOW


@pytest.fixture
def make_ticket():
    """Factory: build a Ticket with sensible defaults, overriding only what a test needs."""
    counter = iter(range(1, 10_000))

    def _make(**overrides):
        n = next(counter)
        fields = {
            "id": f"t-{n}",
            "subject": "General question",
            "body": f"Something about the product ({n}).",
            "customer": f"CUST-{n:03d}",
            "created_at": "2026-10-19T12:00:00Z",
        }
        fields.update(overrides)
        return Ticket(**fields)

    return _make


@pytest.fixture
def ctx(fixed_now):
    return TriageContext(now=fixed_now)


@pytest.fixture
def sample_tickets():
    """The five valid tickets from data/sample_tickets.json."""
    records = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    seen = set()
    tickets = []
    for record in records:
        if not record.get("customer") or record["id"] in seen:
            continue
        if record["created_at"] == "yesterday afternoon":
            continue
        seen.add(record["id"])
        tickets.append(Ticket.from_dict(record))
    return tickets
