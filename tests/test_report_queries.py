"""Tests for the report's database read layer (fixtures live in conftest.py)."""

import pytest

from ticket_triage.models import Ticket
from ticket_triage.report import (
    count_by_priority,
    count_rejected,
    count_unclassified,
    fetch_longest_waiting,
    fetch_top_urgent,
)


@pytest.fixture
def seeded(conn, add_ticket, add_rejected):
    """
    A database with six tickets and two rejected records.

    Oldest to newest: f (unclassified), e (LOW), d (MEDIUM), a (HIGH), then
    b and c (both HIGH, same created_at, so they tie and are ordered by id).
    """
    add_ticket("a", "2026-10-19T08:00:00Z", "HIGH")
    add_ticket("b", "2026-10-19T09:00:00Z", "HIGH")
    add_ticket("c", "2026-10-19T09:00:00Z", "HIGH")
    add_ticket("d", "2026-10-18T07:00:00Z", "MEDIUM")
    add_ticket("e", "2026-10-17T07:00:00Z", "LOW")
    add_ticket("f", "2026-10-16T07:00:00Z", None)
    add_rejected()
    add_rejected("bad date")
    return conn


class TestCountByPriority:
    def test_counts_each_level(self, seeded):
        assert count_by_priority(seeded) == {"HIGH": 3, "MEDIUM": 1, "LOW": 1}

    def test_empty_db_returns_zeros(self, conn):
        assert count_by_priority(conn) == {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    def test_missing_levels_are_zero(self, conn, add_ticket):
        add_ticket("a", "2026-10-19T08:00:00Z", "LOW")
        assert count_by_priority(conn) == {"HIGH": 0, "MEDIUM": 0, "LOW": 1}

    def test_null_priority_not_counted_in_levels(self, conn, add_ticket):
        add_ticket("a", "2026-10-19T08:00:00Z", None)
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

    def test_no_high_tickets(self, conn, add_ticket):
        add_ticket("a", "2026-10-19T08:00:00Z", "LOW")
        assert fetch_top_urgent(conn, 5) == []

    def test_empty_db(self, conn):
        assert fetch_top_urgent(conn, 5) == []


class TestFetchLongestWaiting:
    def test_all_priorities_oldest_first(self, seeded):
        ids = [t.id for t in fetch_longest_waiting(seeded, 10)]
        assert ids == ["f", "e", "d", "a", "b", "c"]

    def test_limit_applies(self, seeded):
        assert [t.id for t in fetch_longest_waiting(seeded, 2)] == ["f", "e"]

    def test_ties_broken_by_id(self, conn, add_ticket):
        # Inserted "z" first, so a correct result proves id (not insert order) breaks the tie.
        add_ticket("z", "2026-10-19T08:00:00Z", "LOW")
        add_ticket("m", "2026-10-19T08:00:00Z", "HIGH")
        assert [t.id for t in fetch_longest_waiting(conn, 5)] == ["m", "z"]

    def test_empty_db(self, conn):
        assert fetch_longest_waiting(conn, 5) == []


class TestCountRejected:
    def test_counts_rows(self, seeded):
        assert count_rejected(seeded) == 2

    def test_empty_db(self, conn):
        assert count_rejected(conn) == 0


class TestResolvedTicketsAreLeftOut:
    """Resolved tickets (resolved_at set) leave every part of the report."""

    @pytest.fixture
    def with_resolved(self, seeded):
        seeded.execute(
            "UPDATE tickets SET resolved_at = '2026-10-20T10:00:00Z' WHERE id IN ('a', 'e', 'f')"
        )
        seeded.commit()
        return seeded

    def test_counts_by_priority(self, with_resolved):
        assert count_by_priority(with_resolved) == {"HIGH": 2, "MEDIUM": 1, "LOW": 0}

    def test_unclassified(self, with_resolved):
        assert count_unclassified(with_resolved) == 0

    def test_top_urgent(self, with_resolved):
        assert [t.id for t in fetch_top_urgent(with_resolved, 5)] == ["b", "c"]

    def test_longest_waiting(self, with_resolved):
        assert [t.id for t in fetch_longest_waiting(with_resolved, 5)] == ["d", "b", "c"]

    def test_rejected_records_are_unaffected(self, with_resolved):
        assert count_rejected(with_resolved) == 2
