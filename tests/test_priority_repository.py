"""Tests for saving priorities, escalation and resolving tickets in SQLite."""

from datetime import timedelta

import pytest
from conftest import insert_ticket

from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    STATUS_PENDING,
    STATUS_PROCESSED,
    STATUS_RESOLVED,
    Ticket,
)
from ticket_triage.priority.config import load_engine
from ticket_triage.priority.repository import (
    ResolveOutcome,
    escalate_overdue,
    fetch_pending,
    load_customer_history,
    prioritize_pending,
    resolve_ticket,
)

DAY = timedelta(hours=24)


def get(conn, ticket_id):
    return Ticket.from_db_row(conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())


def rule_rows(conn, ticket_id):
    return conn.execute(
        "SELECT rule_name, score, reason FROM ticket_rule_results WHERE ticket_id = ? ORDER BY id",
        (ticket_id,),
    ).fetchall()


def set_level(conn, ticket_id, level, classified_at, status=STATUS_PROCESSED):
    with conn:
        conn.execute(
            "UPDATE tickets SET priority_level = ?, classified_at = ?, status = ? WHERE id = ?",
            (level, classified_at.isoformat(), status, ticket_id),
        )


# --- Prioritizing ------------------------------------------------------------


def test_prioritize_pending_saves_level_score_and_reasons(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a", subject="Production outage"))

    results = prioritize_pending(db_conn, load_engine(db_conn), fixed_now)

    stored = get(db_conn, "a")
    assert [r.ticket_id for r in results] == ["a"]
    assert stored.status == STATUS_PROCESSED
    assert stored.priority_level == PRIORITY_HIGH
    assert stored.priority_score == pytest.approx(results[0].score)
    assert stored.classified_at == fixed_now.isoformat()
    names = [row["rule_name"] for row in rule_rows(db_conn, "a")]
    assert names == ["keyword", "repeat_contact", "waiting_time"]


def test_prioritize_skips_already_processed_tickets(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="old"))
    set_level(db_conn, "old", PRIORITY_MEDIUM, fixed_now - DAY)
    insert_ticket(db_conn, make_ticket(id="new"))

    results = prioritize_pending(db_conn, load_engine(db_conn), fixed_now)

    assert [r.ticket_id for r in results] == ["new"]
    assert get(db_conn, "old").priority_level == PRIORITY_MEDIUM
    assert rule_rows(db_conn, "old") == []


def test_fetch_pending_only_returns_pending(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="p"))
    insert_ticket(db_conn, make_ticket(id="done"))
    set_level(db_conn, "done", PRIORITY_LOW, fixed_now)

    assert [t.id for t in fetch_pending(db_conn)] == ["p"]
    assert get(db_conn, "p").status == STATUS_PENDING


def test_repeat_rule_counts_tickets_from_earlier_runs(db_conn, make_ticket, fixed_now):
    """A ticket already stored (and processed) in SQLite counts toward a new one."""
    engine = load_engine(db_conn)
    insert_ticket(db_conn, make_ticket(id="first", customer="CUST-9", created_at="2026-10-17T09:00:00Z"))
    prioritize_pending(db_conn, engine, fixed_now)

    insert_ticket(db_conn, make_ticket(id="second", customer="CUST-9", created_at="2026-10-19T09:00:00Z"))
    prioritize_pending(db_conn, engine, fixed_now)

    repeat = next(row for row in rule_rows(db_conn, "second") if row["rule_name"] == "repeat_contact")
    assert repeat["score"] == 50
    assert "2 tickets" in repeat["reason"]


def test_load_customer_history_groups_by_customer(db_conn, make_ticket):
    insert_ticket(db_conn, make_ticket(customer="A", created_at="2026-10-19T09:00:00Z"))
    insert_ticket(db_conn, make_ticket(customer="A", created_at="2026-10-18T09:00:00Z"))
    insert_ticket(db_conn, make_ticket(customer="B", created_at="2026-10-18T09:00:00Z"))

    history = load_customer_history(db_conn, ["A", "C"])

    assert [dt.day for dt in history["A"]] == [18, 19]
    assert history["C"] == []
    assert "B" not in history


# --- Escalation --------------------------------------------------------------


def test_overdue_low_ticket_becomes_medium(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))
    set_level(db_conn, "a", PRIORITY_LOW, fixed_now - DAY)

    escalated = escalate_overdue(db_conn, fixed_now, DAY)

    ticket = get(db_conn, "a")
    assert escalated == ["a"]
    assert ticket.priority_level == PRIORITY_MEDIUM
    assert ticket.escalation_count == 1
    assert ticket.last_escalated_at == fixed_now.isoformat()
    row = rule_rows(db_conn, "a")[-1]
    assert row["rule_name"] == "escalation"
    assert row["reason"] == "open 24h at LOW -> MEDIUM"


def test_ticket_not_yet_overdue_is_left_alone(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))
    set_level(db_conn, "a", PRIORITY_LOW, fixed_now - DAY + timedelta(minutes=1))

    assert escalate_overdue(db_conn, fixed_now, DAY) == []
    assert get(db_conn, "a").priority_level == PRIORITY_LOW


def test_escalation_waits_a_full_interval_before_bumping_again(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))
    set_level(db_conn, "a", PRIORITY_LOW, fixed_now - 3 * DAY)

    assert escalate_overdue(db_conn, fixed_now, DAY) == ["a"]
    assert escalate_overdue(db_conn, fixed_now + timedelta(hours=1), DAY) == []
    assert escalate_overdue(db_conn, fixed_now + DAY, DAY) == ["a"]

    ticket = get(db_conn, "a")
    assert ticket.priority_level == PRIORITY_HIGH
    assert ticket.escalation_count == 2


def test_high_is_never_escalated(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))
    set_level(db_conn, "a", PRIORITY_HIGH, fixed_now - 10 * DAY)

    assert escalate_overdue(db_conn, fixed_now, DAY) == []
    assert get(db_conn, "a").escalation_count == 0


@pytest.mark.parametrize("status", [STATUS_RESOLVED, STATUS_PENDING])
def test_resolved_and_pending_tickets_are_not_escalated(db_conn, make_ticket, fixed_now, status):
    insert_ticket(db_conn, make_ticket(id="a"))
    set_level(db_conn, "a", PRIORITY_LOW, fixed_now - 10 * DAY, status=status)

    assert escalate_overdue(db_conn, fixed_now, DAY) == []
    assert get(db_conn, "a").priority_level == PRIORITY_LOW


# --- Resolve -----------------------------------------------------------------


def test_resolve_open_ticket(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))
    set_level(db_conn, "a", PRIORITY_MEDIUM, fixed_now - DAY)
    with db_conn:
        db_conn.execute("UPDATE tickets SET priority_score = 55, escalation_count = 1 WHERE id = 'a'")

    outcome, before = resolve_ticket(db_conn, "a", fixed_now)

    ticket = get(db_conn, "a")
    assert outcome is ResolveOutcome.RESOLVED
    assert before.priority_level == PRIORITY_MEDIUM
    assert ticket.status == STATUS_RESOLVED
    assert ticket.resolved_at == fixed_now.isoformat()
    # Priority history is kept
    assert ticket.priority_level == PRIORITY_MEDIUM
    assert ticket.priority_score == 55
    assert ticket.escalation_count == 1


def test_resolve_pending_ticket(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))

    outcome, _ = resolve_ticket(db_conn, "a", fixed_now)

    assert outcome is ResolveOutcome.RESOLVED
    assert get(db_conn, "a").status == STATUS_RESOLVED


def test_resolve_twice_keeps_first_timestamp(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))
    resolve_ticket(db_conn, "a", fixed_now)

    outcome, _ = resolve_ticket(db_conn, "a", fixed_now + DAY)

    assert outcome is ResolveOutcome.ALREADY_RESOLVED
    assert get(db_conn, "a").resolved_at == fixed_now.isoformat()


def test_resolve_unknown_or_partial_id_is_not_found(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="abcdef"))

    assert resolve_ticket(db_conn, "nope", fixed_now) == (ResolveOutcome.NOT_FOUND, None)
    assert resolve_ticket(db_conn, "abc", fixed_now)[0] is ResolveOutcome.NOT_FOUND
    assert get(db_conn, "abcdef").status == STATUS_PENDING


def test_resolved_ticket_is_not_escalated_afterwards(db_conn, make_ticket, fixed_now):
    insert_ticket(db_conn, make_ticket(id="a"))
    set_level(db_conn, "a", PRIORITY_LOW, fixed_now - 10 * DAY)
    resolve_ticket(db_conn, "a", fixed_now)

    assert escalate_overdue(db_conn, fixed_now, DAY) == []
