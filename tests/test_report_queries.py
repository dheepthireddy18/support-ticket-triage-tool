"""Tests for the report's database read layer (fixtures live in conftest.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from ticket_triage.models import Ticket
from ticket_triage.report import (
    AGE_BUCKETS,
    count_by_age,
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


class TestCountByAge:
    NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

    def add_aged(self, add_ticket, ticket_id, age):
        """Add a ticket that has been waiting for `age` at NOW."""
        add_ticket(ticket_id, (self.NOW - age).isoformat())

    def test_empty_db_returns_every_bucket_as_zero(self, conn):
        counts = count_by_age(conn, self.NOW)

        assert list(counts) == [label for label, _ in AGE_BUCKETS]
        assert set(counts.values()) == {0}

    def test_buckets_include_their_lower_bound_and_exclude_the_upper(self, conn, add_ticket):
        ages = {
            "a": timedelta(hours=2), "b": timedelta(hours=23, minutes=59),
            "c": timedelta(hours=24), "d": timedelta(days=2, hours=12),
            "e": timedelta(days=3), "f": timedelta(days=6, hours=23),
            "g": timedelta(days=7), "h": timedelta(days=30),
        }
        for ticket_id, age in ages.items():
            self.add_aged(add_ticket, ticket_id, age)

        assert count_by_age(conn, self.NOW) == {
            "Under 1 day": 2, "1-3 days": 2, "3-7 days": 2, "7+ days": 2,
        }

    def test_ticket_created_after_now_counts_as_under_one_day(self, conn, add_ticket):
        self.add_aged(add_ticket, "future", timedelta(days=-2))

        assert count_by_age(conn, self.NOW)["Under 1 day"] == 1

    def test_handles_z_offset_and_naive_created_at(self, conn, add_ticket):
        add_ticket("z", "2026-10-17T09:00:00Z")          # 2d 3h
        add_ticket("off", "2026-10-17T09:00:00+00:00")   # 2d 3h
        add_ticket("naive", "2026-10-17T09:00:00")       # no timezone: UTC, 2d 3h

        assert count_by_age(conn, self.NOW)["1-3 days"] == 3

    def test_unreadable_created_at_is_not_counted(self, conn, add_ticket):
        add_ticket("bad", "not a date")
        self.add_aged(add_ticket, "ok", timedelta(hours=1))

        counts = count_by_age(conn, self.NOW)

        assert sum(counts.values()) == 1
