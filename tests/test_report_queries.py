"""Tests for the report's database read layer."""

import pytest

from ticket_triage.db import init_db
from ticket_triage.models import RejectedRecord, Ticket
from ticket_triage.report import (
    count_by_priority,
    count_rejected,
    count_unclassified,
    fetch_longest_waiting,
    fetch_top_urgent,
)


@pytest.fixture
def conn():
    connection = init_db(":memory:")
    yield connection
    connection.close()


def add_ticket(conn, ticket_id, created_at, priority=None, **overrides):
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
        "INSERT INTO tickets (id, content_hash, subject, body, customer, created_at, "
        "inserted_at, status, priority_level, classified_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ticket.to_db_tuple(),
    )
    conn.commit()


def add_rejected(conn, reason="missing customer"):
    record = RejectedRecord(raw_record={"id": "x"}, rejection_reason=reason)
    conn.execute(
        "INSERT INTO rejected_tickets (raw_record, rejection_reason, rejected_at) "
        "VALUES (?, ?, ?)",
        record.to_db_tuple(),
    )
    conn.commit()


@pytest.fixture
def seeded(conn):
    add_ticket(conn, "a", "2026-10-19T08:00:00Z", "HIGH")
    add_ticket(conn, "b", "2026-10-19T09:00:00Z", "HIGH")
    add_ticket(conn, "c", "2026-10-19T09:00:00Z", "HIGH")  # ties with b
    add_ticket(conn, "d", "2026-10-18T07:00:00Z", "MEDIUM")
    add_ticket(conn, "e", "2026-10-17T07:00:00Z", "LOW")
    add_ticket(conn, "f", "2026-10-16T07:00:00Z", None)  # unclassified
    add_rejected(conn)
    add_rejected(conn, "bad date")
    return conn


class TestCountByPriority:
    def test_counts_each_level(self, seeded):
        assert count_by_priority(seeded) == {"HIGH": 3, "MEDIUM": 1, "LOW": 1}

    def test_empty_db_returns_zeros(self, conn):
        assert count_by_priority(conn) == {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    def test_missing_levels_are_zero(self, conn):
        add_ticket(conn, "a", "2026-10-19T08:00:00Z", "LOW")
        assert count_by_priority(conn) == {"HIGH": 0, "MEDIUM": 0, "LOW": 1}

    def test_null_priority_not_counted_in_levels(self, conn):
        add_ticket(conn, "a", "2026-10-19T08:00:00Z", None)
        assert sum(count_by_priority(conn).values()) == 0


class TestCountUnclassified:
    def test_counts_null_priority_only(self, seeded):
        assert count_unclassified(seeded) == 1

    def test_empty_db(self, conn):
        assert count_unclassified(conn) == 0


class TestFetchTopUrgent:
    def test_only_high_oldest_first_with_tie_broken_by_id(self, seeded):
        ids = [t.id for t in fetch_top_urgent(seeded, 10)]
        assert ids == ["a", "b", "c"]

    def test_limit_applies(self, seeded):
        assert [t.id for t in fetch_top_urgent(seeded, 2)] == ["a", "b"]

    def test_returns_tickets(self, seeded):
        ticket = fetch_top_urgent(seeded, 1)[0]
        assert isinstance(ticket, Ticket)
        assert ticket.priority_level == "HIGH"

    def test_no_high_tickets(self, conn):
        add_ticket(conn, "a", "2026-10-19T08:00:00Z", "LOW")
        assert fetch_top_urgent(conn, 5) == []

    def test_empty_db(self, conn):
        assert fetch_top_urgent(conn, 5) == []


class TestFetchLongestWaiting:
    def test_all_priorities_oldest_first(self, seeded):
        ids = [t.id for t in fetch_longest_waiting(seeded, 10)]
        assert ids == ["f", "e", "d", "a", "b", "c"]

    def test_limit_applies(self, seeded):
        assert [t.id for t in fetch_longest_waiting(seeded, 2)] == ["f", "e"]

    def test_ties_broken_by_id(self, conn):
        add_ticket(conn, "z", "2026-10-19T08:00:00Z", "LOW")
        add_ticket(conn, "m", "2026-10-19T08:00:00Z", "HIGH")
        assert [t.id for t in fetch_longest_waiting(conn, 5)] == ["m", "z"]

    def test_empty_db(self, conn):
        assert fetch_longest_waiting(conn, 5) == []


class TestCountRejected:
    def test_counts_rows(self, seeded):
        assert count_rejected(seeded) == 2

    def test_empty_db(self, conn):
        assert count_rejected(conn) == 0
