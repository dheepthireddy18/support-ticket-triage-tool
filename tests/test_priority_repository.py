"""Tests for saving priorities and their reasons in SQLite."""

from datetime import timedelta

import pytest

from ticket_triage.db import init_db
from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_MEDIUM,
    STATUS_PENDING,
    STATUS_PROCESSED,
    Ticket,
)
from ticket_triage.priority.engine import build_default_engine
from ticket_triage.priority.repository import (
    fetch_pending,
    fetch_prioritized,
    load_customer_history,
    prioritize_pending,
    save_results,
)

DAY = timedelta(hours=24)


@pytest.fixture
def db_conn():
    conn = init_db(":memory:")
    yield conn
    conn.close()


def insert(conn, ticket):
    """Insert a Ticket row the same way intake does (PENDING, no priority)."""
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


def get(conn, ticket_id):
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return Ticket.from_db_row(row)


def rule_rows(conn, ticket_id):
    return conn.execute(
        "SELECT rule_name, score, reason, created_at FROM ticket_rule_results "
        "WHERE ticket_id = ? ORDER BY id",
        (ticket_id,),
    ).fetchall()


def mark_processed(conn, ticket_id, level, when):
    with conn:
        conn.execute(
            "UPDATE tickets SET priority_level = ?, classified_at = ?, status = ? WHERE id = ?",
            (level, when.isoformat(), STATUS_PROCESSED, ticket_id),
        )


# --- prioritize_pending ------------------------------------------------------


def test_prioritize_pending_saves_level_status_and_reasons(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a", subject="Production outage"))

    results = prioritize_pending(db_conn, build_default_engine(), fixed_now)

    ticket = get(db_conn, "a")
    assert [r.ticket_id for r in results] == ["a"]
    assert ticket.status == STATUS_PROCESSED
    assert ticket.priority_level == PRIORITY_HIGH
    assert ticket.classified_at == fixed_now.isoformat()

    rows = rule_rows(db_conn, "a")
    assert [row["rule_name"] for row in rows] == ["keyword", "repeat_contact", "waiting_time"]
    assert rows[0]["score"] == 90
    assert "at least HIGH" in rows[0]["reason"]
    assert {row["created_at"] for row in rows} == {fixed_now.isoformat()}


def test_already_prioritized_tickets_are_scored_only_once(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="old"))
    mark_processed(db_conn, "old", PRIORITY_MEDIUM, fixed_now - DAY)
    insert(db_conn, make_ticket(id="new"))

    results = prioritize_pending(db_conn, build_default_engine(), fixed_now)

    assert [r.ticket_id for r in results] == ["new"]
    assert get(db_conn, "old").priority_level == PRIORITY_MEDIUM
    assert rule_rows(db_conn, "old") == []


def test_second_run_changes_nothing(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    engine = build_default_engine()
    prioritize_pending(db_conn, engine, fixed_now)

    assert prioritize_pending(db_conn, engine, fixed_now + DAY) == []
    assert len(rule_rows(db_conn, "a")) == 3
    assert get(db_conn, "a").classified_at == fixed_now.isoformat()


def test_no_pending_tickets_returns_empty(db_conn, fixed_now):
    assert prioritize_pending(db_conn, build_default_engine(), fixed_now) == []


def test_repeat_rule_counts_tickets_from_earlier_runs(db_conn, make_ticket, fixed_now):
    """A ticket stored (and prioritized) in an earlier run counts toward a new one."""
    engine = build_default_engine()
    insert(db_conn, make_ticket(id="first", customer="CUST-9", created_at="2026-10-17T09:00:00Z"))
    prioritize_pending(db_conn, engine, fixed_now)

    insert(db_conn, make_ticket(id="second", customer="CUST-9", created_at="2026-10-19T09:00:00Z"))
    prioritize_pending(db_conn, engine, fixed_now)

    repeat = next(row for row in rule_rows(db_conn, "second") if row["rule_name"] == "repeat_contact")
    assert repeat["score"] == 50
    assert "2 tickets" in repeat["reason"]


def test_abstaining_rule_is_saved_with_null_score(db_conn, make_ticket, fixed_now):
    from ticket_triage.priority.base import PriorityRule
    from ticket_triage.priority.engine import PriorityEngine

    class NoOpinion(PriorityRule):
        name = "no_opinion"

        def evaluate(self, ticket, ctx):
            return self.result(None, "skipped: nothing to say")

    insert(db_conn, make_ticket(id="a"))
    prioritize_pending(db_conn, PriorityEngine([NoOpinion()]), fixed_now)

    row = rule_rows(db_conn, "a")[0]
    assert row["score"] is None
    assert row["reason"] == "skipped: nothing to say"


# --- Reading -----------------------------------------------------------------


def test_fetch_pending_and_prioritized_split_by_status(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="p", created_at="2026-10-18T09:00:00Z"))
    insert(db_conn, make_ticket(id="done", created_at="2026-10-17T09:00:00Z"))
    mark_processed(db_conn, "done", PRIORITY_HIGH, fixed_now)

    assert [t.id for t in fetch_pending(db_conn)] == ["p"]
    assert [t.id for t in fetch_prioritized(db_conn)] == ["done"]
    assert get(db_conn, "p").status == STATUS_PENDING


def test_fetch_pending_is_oldest_first(db_conn, make_ticket):
    insert(db_conn, make_ticket(id="late", created_at="2026-10-19T09:00:00Z"))
    insert(db_conn, make_ticket(id="early", created_at="2026-10-18T09:00:00Z"))

    assert [t.id for t in fetch_pending(db_conn)] == ["early", "late"]


def test_load_customer_history_groups_by_customer(db_conn, make_ticket):
    insert(db_conn, make_ticket(customer="A", created_at="2026-10-19T09:00:00Z"))
    insert(db_conn, make_ticket(customer="A", created_at="2026-10-18T09:00:00Z"))
    insert(db_conn, make_ticket(customer="B", created_at="2026-10-18T09:00:00Z"))

    history = load_customer_history(db_conn, ["A", "C"])

    assert [dt.day for dt in history["A"]] == [18, 19]
    assert history["C"] == []
    assert "B" not in history


def test_load_customer_history_with_no_customers(db_conn):
    assert load_customer_history(db_conn, []) == {}


def test_save_results_never_overwrites_a_prioritized_ticket(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a", subject="Production outage"))
    engine = build_default_engine()
    results = prioritize_pending(db_conn, engine, fixed_now)

    # Saving the same result again (e.g. a second process) must not re-stamp the ticket.
    save_results(db_conn, results, fixed_now + DAY)

    assert get(db_conn, "a").classified_at == fixed_now.isoformat()


# --- Schema ------------------------------------------------------------------


def test_reset_drops_rule_results_too(tmp_path, make_ticket, fixed_now):
    path = tmp_path / "t.db"
    conn = init_db(path)
    insert(conn, make_ticket(id="a"))
    prioritize_pending(conn, build_default_engine(), fixed_now)
    conn.close()

    conn = init_db(path, reset=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM ticket_rule_results").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    finally:
        conn.close()
