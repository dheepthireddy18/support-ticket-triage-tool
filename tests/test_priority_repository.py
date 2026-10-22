"""Tests for re-scoring open tickets each run, saving their history, and resolving tickets."""

import sqlite3
from datetime import timedelta

import pytest

from ticket_triage.db import init_db
from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    STATUS_PENDING,
    STATUS_PROCESSED,
    Ticket,
)
from ticket_triage.priority.base import PriorityRule
from ticket_triage.priority.engine import PriorityEngine, build_default_engine
from ticket_triage.priority.repository import (
    ResolveOutcome,
    fetch_open,
    fetch_prioritized,
    load_customer_history,
    load_saved_results,
    prioritize_open,
    resolve_ticket,
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


def rule_rows(conn, ticket_id, rule_name=None):
    sql = "SELECT rule_name, score, reason, created_at FROM ticket_rule_results WHERE ticket_id = ?"
    params = [ticket_id]
    if rule_name:
        sql += " AND rule_name = ?"
        params.append(rule_name)
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def set_resolved(conn, ticket_id, when):
    with conn:
        conn.execute("UPDATE tickets SET resolved_at = ? WHERE id = ?", (when.isoformat(), ticket_id))


class CountingModelRule(PriorityRule):
    """Stands in for a paid model rule: counts calls; scores come from a list (None = skip)."""

    name = "model"
    score_once = True

    def __init__(self, scores):
        super().__init__()
        self.scores = list(scores)
        self.calls = 0

    def evaluate(self, ticket, ctx):
        self.calls += 1
        score = self.scores.pop(0)
        return self.result(score, "skipped: no key" if score is None else f"model says {score}")


# --- prioritize_open ---------------------------------------------------------


def test_first_run_saves_level_status_and_reasons(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a", subject="Production outage"))

    results = prioritize_open(db_conn, build_default_engine(), fixed_now)

    ticket = get(db_conn, "a")
    assert [r.ticket_id for r in results] == ["a"]
    assert ticket.status == STATUS_PROCESSED
    assert ticket.priority_level == PRIORITY_HIGH
    assert ticket.classified_at == fixed_now.isoformat()
    rows = rule_rows(db_conn, "a")
    assert [row["rule_name"] for row in rows] == ["keyword", "repeat_contact", "waiting_time"]
    assert rows[0]["score"] == 90


def test_urgency_rises_on_later_runs_as_the_ticket_waits(db_conn, make_ticket, fixed_now):
    created = fixed_now - timedelta(hours=2)
    insert(db_conn, make_ticket(id="a", subject="Question about invoice", created_at=created.isoformat()))
    engine = build_default_engine()

    prioritize_open(db_conn, engine, fixed_now)
    assert get(db_conn, "a").priority_level == PRIORITY_LOW  # (30 + 0 + ~3) / 3

    later = fixed_now + 3 * DAY
    prioritize_open(db_conn, engine, later)

    ticket = get(db_conn, "a")
    assert ticket.priority_level == PRIORITY_MEDIUM  # (30 + 0 + 100) / 3
    assert ticket.classified_at == later.isoformat()  # When it was last scored


def test_every_run_adds_to_the_history(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    engine = build_default_engine()

    prioritize_open(db_conn, engine, fixed_now)
    prioritize_open(db_conn, engine, fixed_now + DAY)

    rows = rule_rows(db_conn, "a", "waiting_time")
    assert [row["created_at"] for row in rows] == [fixed_now.isoformat(), (fixed_now + DAY).isoformat()]
    assert rows[1]["score"] > rows[0]["score"]
    assert len(rule_rows(db_conn, "a")) == 6


def test_level_can_go_down_when_the_rules_change(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    strict = PriorityEngine(build_default_engine().rules, high_threshold=1, medium_threshold=0)

    prioritize_open(db_conn, strict, fixed_now)
    assert get(db_conn, "a").priority_level == PRIORITY_HIGH

    prioritize_open(db_conn, build_default_engine(), fixed_now)
    assert get(db_conn, "a").priority_level == PRIORITY_LOW


def test_resolved_tickets_are_not_rescored(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    engine = build_default_engine()
    prioritize_open(db_conn, engine, fixed_now)
    set_resolved(db_conn, "a", fixed_now)

    assert prioritize_open(db_conn, engine, fixed_now + DAY) == []
    assert len(rule_rows(db_conn, "a")) == 3
    assert get(db_conn, "a").classified_at == fixed_now.isoformat()


def test_no_open_tickets_returns_empty(db_conn, fixed_now):
    assert prioritize_open(db_conn, build_default_engine(), fixed_now) == []


def test_repeat_rule_counts_tickets_from_earlier_runs(db_conn, make_ticket, fixed_now):
    engine = build_default_engine()
    insert(db_conn, make_ticket(id="first", customer="CUST-9", created_at="2026-10-17T09:00:00Z"))
    prioritize_open(db_conn, engine, fixed_now)

    insert(db_conn, make_ticket(id="second", customer="CUST-9", created_at="2026-10-19T09:00:00Z"))
    prioritize_open(db_conn, engine, fixed_now)

    repeat = rule_rows(db_conn, "second", "repeat_contact")[-1]
    assert repeat["score"] == 50
    assert "2 tickets" in repeat["reason"]


def test_abstaining_rule_is_saved_with_null_score(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    prioritize_open(db_conn, PriorityEngine([CountingModelRule([None])]), fixed_now)

    row = rule_rows(db_conn, "a")[0]
    assert row["score"] is None
    assert row["reason"] == "skipped: no key"


# --- Model rules are scored once ------------------------------------------------


def test_model_rule_is_called_only_on_the_first_run(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    model = CountingModelRule([70])
    engine = PriorityEngine([model])

    prioritize_open(db_conn, engine, fixed_now)
    prioritize_open(db_conn, engine, fixed_now + DAY)
    prioritize_open(db_conn, engine, fixed_now + 2 * DAY)

    assert model.calls == 1
    rows = rule_rows(db_conn, "a", "model")
    assert [row["score"] for row in rows] == [70, 70, 70]
    assert rows[0]["reason"] == "model says 70"
    assert rows[1]["reason"] == rows[2]["reason"] == "model says 70 (saved from first scoring)"


def test_model_rule_skip_is_retried_on_the_next_run(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    model = CountingModelRule([None, 60])  # No key on day 1, key added by day 2
    engine = PriorityEngine([model])

    prioritize_open(db_conn, engine, fixed_now)
    prioritize_open(db_conn, engine, fixed_now + DAY)
    prioritize_open(db_conn, engine, fixed_now + 2 * DAY)

    assert model.calls == 2  # Retried once, then reused
    assert [row["score"] for row in rule_rows(db_conn, "a", "model")] == [None, 60, 60]


def test_a_new_ticket_gets_its_own_model_call(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a", created_at="2026-10-18T09:00:00Z"))
    model = CountingModelRule([40, 55])  # Day 1: ticket a. Day 2: only the new ticket b.
    engine = PriorityEngine([model])
    prioritize_open(db_conn, engine, fixed_now)

    insert(db_conn, make_ticket(id="b", created_at="2026-10-19T09:00:00Z"))
    prioritize_open(db_conn, engine, fixed_now + DAY)

    assert model.calls == 2
    assert [row["score"] for row in rule_rows(db_conn, "a", "model")] == [40, 40]
    assert [row["score"] for row in rule_rows(db_conn, "b", "model")] == [55]


# --- Reading -----------------------------------------------------------------


def test_fetch_open_includes_pending_and_prioritized_but_not_resolved(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="pending", created_at="2026-10-18T09:00:00Z"))
    insert(db_conn, make_ticket(id="done", created_at="2026-10-17T09:00:00Z"))
    insert(db_conn, make_ticket(id="closed", created_at="2026-10-16T09:00:00Z"))
    with db_conn:
        db_conn.execute("UPDATE tickets SET status = ? WHERE id IN ('done', 'closed')", (STATUS_PROCESSED,))
    set_resolved(db_conn, "closed", fixed_now)

    assert [t.id for t in fetch_open(db_conn)] == ["done", "pending"]  # Oldest first
    assert [t.id for t in fetch_prioritized(db_conn)] == ["done"]
    assert get(db_conn, "pending").status == STATUS_PENDING


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


def test_load_saved_results_returns_first_real_score_per_rule(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    prioritize_open(db_conn, PriorityEngine([CountingModelRule([None])]), fixed_now)
    prioritize_open(db_conn, PriorityEngine([CountingModelRule([35])]), fixed_now + DAY)

    saved = load_saved_results(db_conn, ["model", "not_a_rule"])

    assert set(saved) == {("a", "model")}
    assert saved[("a", "model")].score == 35
    assert load_saved_results(db_conn, []) == {}


# --- Resolve -----------------------------------------------------------------


def test_resolve_open_ticket_keeps_status_and_history(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a", subject="Production outage"))
    prioritize_open(db_conn, build_default_engine(), fixed_now)

    outcome, before = resolve_ticket(db_conn, "a", fixed_now + DAY)

    ticket = get(db_conn, "a")
    assert outcome is ResolveOutcome.RESOLVED
    assert before.priority_level == PRIORITY_HIGH
    assert ticket.is_resolved
    assert ticket.resolved_at == (fixed_now + DAY).isoformat()
    assert ticket.status == STATUS_PROCESSED  # status tracks processing only
    assert ticket.priority_level == PRIORITY_HIGH
    assert len(rule_rows(db_conn, "a")) == 3


def test_resolve_pending_ticket(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))

    outcome, _ = resolve_ticket(db_conn, "a", fixed_now)

    assert outcome is ResolveOutcome.RESOLVED
    assert get(db_conn, "a").status == STATUS_PENDING
    assert prioritize_open(db_conn, build_default_engine(), fixed_now) == []  # Never scored


def test_resolve_twice_keeps_first_timestamp(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="a"))
    resolve_ticket(db_conn, "a", fixed_now)

    outcome, _ = resolve_ticket(db_conn, "a", fixed_now + DAY)

    assert outcome is ResolveOutcome.ALREADY_RESOLVED
    assert get(db_conn, "a").resolved_at == fixed_now.isoformat()


def test_resolve_unknown_or_partial_id_is_not_found(db_conn, make_ticket, fixed_now):
    insert(db_conn, make_ticket(id="abcdef"))

    assert resolve_ticket(db_conn, "nope", fixed_now) == (ResolveOutcome.NOT_FOUND, None)
    assert resolve_ticket(db_conn, "abc", fixed_now)[0] is ResolveOutcome.NOT_FOUND
    assert not get(db_conn, "abcdef").is_resolved


# --- Schema ------------------------------------------------------------------


def test_reset_drops_rule_results_too(tmp_path, make_ticket, fixed_now):
    path = tmp_path / "t.db"
    conn = init_db(path)
    insert(conn, make_ticket(id="a"))
    prioritize_open(conn, build_default_engine(), fixed_now)
    conn.close()

    conn = init_db(path, reset=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM ticket_rule_results").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    finally:
        conn.close()


def test_older_database_gets_resolved_at_column(tmp_path, make_ticket):
    """A tickets table from before resolved_at existed is upgraded without losing rows."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY, content_hash TEXT UNIQUE NOT NULL, subject TEXT NOT NULL,
            body TEXT NOT NULL, customer TEXT NOT NULL, created_at TEXT NOT NULL,
            inserted_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
            priority_level TEXT, classified_at TEXT, metadata TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        make_ticket(id="kept").to_db_tuple(),
    )
    conn.commit()
    conn.close()

    upgraded = init_db(path)
    try:
        columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(tickets)")}
        assert "resolved_at" in columns
        assert not get(upgraded, "kept").is_resolved
    finally:
        upgraded.close()
