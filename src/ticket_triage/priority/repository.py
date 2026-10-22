"""
All SQL for the priority module: reading open tickets and the history the rules
use, saving each run's results, and resolving tickets.

The hand-off with intake: intake stores valid tickets as PENDING with no
priority_level. Every run, prioritize_open() re-scores every open ticket (PENDING
or PROCESSED, and not resolved) against the current time, so a ticket becomes
more urgent the longer it waits. It sets priority_level, classified_at (when it
was last scored) and status = PROCESSED, and adds that run's per-rule scores and
reasons to ticket_rule_results, which therefore keeps the full history.

Rules marked score_once (the paid model rules) are only evaluated the first time
they give a ticket a score; later runs reuse that saved score.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import Enum
from typing import Iterable

from ticket_triage.models import STATUS_PENDING, STATUS_PROCESSED, Ticket
from ticket_triage.priority.base import RuleResult, TriageContext
from ticket_triage.priority.engine import PriorityEngine, PriorityResult


class ResolveOutcome(Enum):
    RESOLVED = "resolved"
    ALREADY_RESOLVED = "already_resolved"
    NOT_FOUND = "not_found"


def fetch_open(conn: sqlite3.Connection) -> list[Ticket]:
    """Tickets that still need scoring each run (pending or prioritized, not resolved), oldest first."""
    rows = conn.execute(
        """
        SELECT * FROM tickets
        WHERE status IN (?, ?) AND resolved_at IS NULL
        ORDER BY created_at, id
        """,
        (STATUS_PENDING, STATUS_PROCESSED),
    )
    return [Ticket.from_db_row(row) for row in rows]


def fetch_prioritized(conn: sqlite3.Connection) -> list[Ticket]:
    """Open tickets that already have a priority, oldest first."""
    rows = conn.execute(
        "SELECT * FROM tickets WHERE status = ? AND resolved_at IS NULL ORDER BY created_at, id",
        (STATUS_PROCESSED,),
    )
    return [Ticket.from_db_row(row) for row in rows]


def load_customer_history(
    conn: sqlite3.Connection, customers: Iterable[str]
) -> dict[str, list[datetime]]:
    """created_at of every stored ticket for the given customers, oldest first."""
    customers = sorted(set(customers))
    if not customers:
        return {}
    placeholders = ", ".join("?" for _ in customers)
    rows = conn.execute(
        f"SELECT * FROM tickets WHERE customer IN ({placeholders})",
        customers,
    )
    history: dict[str, list[datetime]] = {customer: [] for customer in customers}
    for row in rows:
        ticket = Ticket.from_db_row(row)
        history[ticket.customer].append(ticket.created_at_datetime)
    for times in history.values():
        times.sort()
    return history


def load_saved_results(
    conn: sqlite3.Connection, rule_names: Iterable[str]
) -> dict[tuple[str, str], RuleResult]:
    """
    The first real score (not a skip) each ticket got from each of the given
    rules, keyed by (ticket_id, rule_name).
    """
    rule_names = sorted(set(rule_names))
    if not rule_names:
        return {}
    placeholders = ", ".join("?" for _ in rule_names)
    rows = conn.execute(
        f"""
        SELECT r.ticket_id, r.rule_name, r.score, r.reason
        FROM ticket_rule_results r
        WHERE r.rule_name IN ({placeholders}) AND r.score IS NOT NULL
          AND r.id = (
              SELECT MIN(first.id) FROM ticket_rule_results first
              WHERE first.ticket_id = r.ticket_id AND first.rule_name = r.rule_name
                AND first.score IS NOT NULL
          )
        """,
        rule_names,
    )
    # The note marks reused rows in the history; the first row (reused next time) has none.
    return {
        (row["ticket_id"], row["rule_name"]): RuleResult(
            row["rule_name"], row["score"], f"{row['reason']} (saved from first scoring)"
        )
        for row in rows
    }


def save_results(
    conn: sqlite3.Connection, results: Iterable[PriorityResult], now: datetime
) -> None:
    """Store each ticket's new level and this run's per-rule reasons in one transaction."""
    timestamp = now.isoformat()
    with conn:
        for result in results:
            conn.execute(
                """
                UPDATE tickets
                SET priority_level = ?, classified_at = ?, status = ?
                WHERE id = ? AND resolved_at IS NULL
                """,
                (result.level, timestamp, STATUS_PROCESSED, result.ticket_id),
            )
            conn.executemany(
                """
                INSERT INTO ticket_rule_results (ticket_id, rule_name, score, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (result.ticket_id, r.rule_name, r.score, r.reason, timestamp)
                    for r in result.rule_results
                ],
            )


def prioritize_open(
    conn: sqlite3.Connection, engine: PriorityEngine, now: datetime
) -> list[PriorityResult]:
    """Re-score every open ticket against `now` and save the results. Returns them for logging."""
    tickets = fetch_open(conn)
    if not tickets:
        return []
    ctx = TriageContext(
        now=now,
        customer_history=load_customer_history(conn, (t.customer for t in tickets)),
        saved_results=load_saved_results(
            conn, (rule.name for rule in engine.rules if rule.enabled and rule.score_once)
        ),
    )
    results = [engine.score(ticket, ctx) for ticket in tickets]
    save_results(conn, results, now)
    return results


def resolve_ticket(
    conn: sqlite3.Connection, ticket_id: str, now: datetime
) -> tuple[ResolveOutcome, Ticket | None]:
    """
    Mark a ticket resolved by setting resolved_at, so later runs stop re-scoring
    it. `status` is left alone: it tracks processing, not whether support closed
    the ticket. Safe to repeat: resolved_at keeps its first value. The id must
    match exactly.
    """
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        return ResolveOutcome.NOT_FOUND, None

    ticket = Ticket.from_db_row(row)
    if ticket.is_resolved:
        return ResolveOutcome.ALREADY_RESOLVED, ticket

    with conn:
        conn.execute(
            "UPDATE tickets SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            (now.isoformat(), ticket_id),
        )
    return ResolveOutcome.RESOLVED, ticket
