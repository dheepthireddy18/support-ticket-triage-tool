"""Shared pytest fixtures for tests that need a database with tickets in it."""

import pytest

from ticket_triage.db import init_db
from ticket_triage.models import RejectedRecord, Ticket


@pytest.fixture
def conn():
    """An empty in-memory database with the full schema, closed after the test."""
    connection = init_db(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def add_ticket(conn):
    """
    Factory: insert one ticket into `conn`.

    add_ticket("a", "2026-10-19T08:00:00Z", "HIGH") stores a ticket with id "a".
    Subject, body and customer are derived from the id; pass keyword arguments
    (e.g. customer="ACME") to override any Ticket field.
    """

    def _add(ticket_id, created_at, priority=None, **overrides):
        fields = {
            "id": ticket_id,
            "subject": f"Subject {ticket_id}",
            "body": f"Body {ticket_id}",
            "customer": f"CUST-{ticket_id}",
            "created_at": created_at,
            "priority_level": priority,
        }
        fields.update(overrides)
        ticket = Ticket(**fields)
        conn.execute(
            "INSERT INTO tickets (id, content_hash, subject, body, customer, "
            "created_at, inserted_at, status, priority_level, classified_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ticket.to_db_tuple(),
        )
        conn.commit()

    return _add


@pytest.fixture
def add_rejected(conn):
    """Factory: insert one rejected record into `conn`."""

    def _add(reason="missing customer"):
        record = RejectedRecord(raw_record={"id": "x"}, rejection_reason=reason)
        conn.execute(
            "INSERT INTO rejected_tickets (raw_record, rejection_reason, rejected_at) "
            "VALUES (?, ?, ?)",
            record.to_db_tuple(),
        )
        conn.commit()

    return _add
