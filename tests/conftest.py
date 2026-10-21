"""Shared pytest fixtures for the priority rule tests."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ticket_triage.db import init_db
from ticket_triage.models import Ticket
from ticket_triage.priority.base import TriageContext

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_tickets.json"

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
def db_conn():
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    """An on-disk database, for code that opens its own connections (the worker)."""
    path = tmp_path / "tickets.db"
    init_db(path).close()
    return path


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


def insert_ticket(conn, ticket):
    """Insert a Ticket row the same way intake does."""
    with conn:
        conn.execute(
            """
            INSERT INTO tickets (
                id, content_hash, subject, body, customer, created_at, inserted_at, status,
                priority_level, classified_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ticket.to_db_tuple(),
        )
