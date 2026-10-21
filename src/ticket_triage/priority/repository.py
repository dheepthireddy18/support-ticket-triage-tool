"""
All SQL for the priority module: reading pending tickets and customer history,
saving engine results, escalating overdue tickets, and resolving tickets.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable

from ticket_triage.models import (
    PRIORITY_HIGH,
    STATUS_PENDING,
    STATUS_PROCESSED,
    STATUS_RESOLVED,
    Ticket,
)
from ticket_triage.priority.base import TriageContext, next_level, parse_timestamp
from ticket_triage.priority.engine import PriorityEngine, PriorityResult

ESCALATION_RULE_NAME = "escalation"


class ResolveOutcome(Enum):
    RESOLVED = "resolved"
    ALREADY_RESOLVED = "already_resolved"
    NOT_FOUND = "not_found"


def fetch_pending(conn: sqlite3.Connection) -> list[Ticket]:
    rows = conn.execute(
        "SELECT * FROM tickets WHERE status = ? ORDER BY created_at, id", (STATUS_PENDING,)
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
        f"SELECT customer, created_at FROM tickets WHERE customer IN ({placeholders})",
        customers,
    )
    history: dict[str, list[datetime]] = {customer: [] for customer in customers}
    for row in rows:
        history[row["customer"]].append(parse_timestamp(row["created_at"]))
    for times in history.values():
        times.sort()
    return history


def save_results(
    conn: sqlite3.Connection, results: Iterable[PriorityResult], now: datetime
) -> None:
    """Store each ticket's level, score and per-rule reasons in one transaction."""
    timestamp = now.isoformat()
    with conn:
        for result in results:
            conn.execute(
                """
                UPDATE tickets
                SET priority_level = ?, priority_score = ?, classified_at = ?, status = ?
                WHERE id = ?
                """,
                (result.level, result.score, timestamp, STATUS_PROCESSED, result.ticket_id),
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


def prioritize_pending(
    conn: sqlite3.Connection, engine: PriorityEngine, now: datetime
) -> list[PriorityResult]:
    """Score every PENDING ticket and save the results. Returns them for logging."""
    tickets = fetch_pending(conn)
    if not tickets:
        return []
    ctx = TriageContext(
        now=now, customer_history=load_customer_history(conn, (t.customer for t in tickets))
    )
    results = [engine.score(ticket, ctx) for ticket in tickets]
    save_results(conn, results, now)
    return results


def escalate_overdue(
    conn: sqlite3.Connection, now: datetime, after: timedelta
) -> list[str]:
    """
    Raise open tickets one level when they have sat at their level for `after`.
    Open means PROCESSED (prioritized, not RESOLVED). Never lowers a level.
    Returns the ids of escalated tickets.
    """
    rows = conn.execute(
        """
        SELECT id, priority_level, classified_at, last_escalated_at
        FROM tickets
        WHERE status = ? AND priority_level IS NOT NULL AND priority_level != ?
        """,
        (STATUS_PROCESSED, PRIORITY_HIGH),
    ).fetchall()

    hours = after.total_seconds() / 3600
    timestamp = now.isoformat()
    escalated = []
    with conn:
        for row in rows:
            since_text = row["last_escalated_at"] or row["classified_at"]
            if not since_text or now - parse_timestamp(since_text) < after:
                continue
            old_level = row["priority_level"]
            new_level = next_level(old_level)
            # The status/level guard skips tickets resolved or changed since the SELECT.
            cursor = conn.execute(
                """
                UPDATE tickets
                SET priority_level = ?, last_escalated_at = ?,
                    escalation_count = escalation_count + 1
                WHERE id = ? AND status = ? AND priority_level = ?
                """,
                (new_level, timestamp, row["id"], STATUS_PROCESSED, old_level),
            )
            if cursor.rowcount == 0:
                continue
            conn.execute(
                """
                INSERT INTO ticket_rule_results (ticket_id, rule_name, score, reason, created_at)
                VALUES (?, ?, NULL, ?, ?)
                """,
                (
                    row["id"],
                    ESCALATION_RULE_NAME,
                    f"open {hours:g}h at {old_level} -> {new_level}",
                    timestamp,
                ),
            )
            escalated.append(row["id"])
    return escalated


def resolve_ticket(
    conn: sqlite3.Connection, ticket_id: str, now: datetime
) -> tuple[ResolveOutcome, Ticket | None]:
    """Mark a ticket RESOLVED. Safe to repeat: resolved_at keeps its first value."""
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        return ResolveOutcome.NOT_FOUND, None

    ticket = Ticket.from_db_row(row)
    if ticket.status == STATUS_RESOLVED:
        return ResolveOutcome.ALREADY_RESOLVED, ticket

    with conn:
        conn.execute(
            "UPDATE tickets SET status = ?, resolved_at = ? WHERE id = ? AND status != ?",
            (STATUS_RESOLVED, now.isoformat(), ticket_id, STATUS_RESOLVED),
        )
    return ResolveOutcome.RESOLVED, ticket
