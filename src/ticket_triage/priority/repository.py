"""
All SQL for the priority module: reading tickets that still need a priority and
the customer history the rules use, and saving each ticket's result.

The hand-off with intake: intake stores valid tickets as PENDING with no
priority_level. prioritize_pending() scores every PENDING ticket once, then
sets priority_level, classified_at and status = PROCESSED, and records each
rule's score and reason in ticket_rule_results.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable

from ticket_triage.models import STATUS_PENDING, STATUS_PROCESSED, Ticket
from ticket_triage.priority.base import TriageContext
from ticket_triage.priority.engine import PriorityEngine, PriorityResult


def fetch_pending(conn: sqlite3.Connection) -> list[Ticket]:
    """Tickets waiting for a priority, oldest first."""
    rows = conn.execute(
        "SELECT * FROM tickets WHERE status = ? ORDER BY created_at, id", (STATUS_PENDING,)
    )
    return [Ticket.from_db_row(row) for row in rows]


def fetch_prioritized(conn: sqlite3.Connection) -> list[Ticket]:
    """Tickets that already have a priority, oldest first."""
    rows = conn.execute(
        "SELECT * FROM tickets WHERE status = ? ORDER BY created_at, id", (STATUS_PROCESSED,)
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


def save_results(
    conn: sqlite3.Connection, results: Iterable[PriorityResult], now: datetime
) -> None:
    """Store each ticket's level and per-rule reasons in one transaction."""
    timestamp = now.isoformat()
    with conn:
        for result in results:
            conn.execute(
                """
                UPDATE tickets
                SET priority_level = ?, classified_at = ?, status = ?
                WHERE id = ? AND status = ?
                """,
                (result.level, timestamp, STATUS_PROCESSED, result.ticket_id, STATUS_PENDING),
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
    """Score every PENDING ticket once and save the results. Returns them for logging."""
    tickets = fetch_pending(conn)
    if not tickets:
        return []
    ctx = TriageContext(
        now=now, customer_history=load_customer_history(conn, (t.customer for t in tickets))
    )
    results = [engine.score(ticket, ctx) for ticket in tickets]
    save_results(conn, results, now)
    return results
