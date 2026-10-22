"""
Data layer for the daily summary report.

The query functions are read-only: each takes an open sqlite3.Connection (with
row_factory=sqlite3.Row, as returned by get_db_connection) and never writes.
Counting, ordering and limits are done in SQL. created_at is stored as
ISO-8601 text, so ORDER BY created_at is chronological as long as the stored
values share one format. Ties are broken by id so results are deterministic.

build_report_data() combines the queries into one ReportData for rendering.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    Ticket,
)

PRIORITY_LEVELS = (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)
DEFAULT_TOP_N = 5


@dataclass(frozen=True)
class ReportTicket:
    """A ticket on the report, with how long it has been waiting at `now`."""

    ticket: Ticket
    waiting: timedelta


@dataclass
class ReportData:
    """Everything the rendered report needs, gathered in one place."""

    generated_at: datetime
    total_tickets: int
    counts_by_priority: dict[str, int]
    unclassified_count: int
    top_urgent: list[ReportTicket] = field(default_factory=list)
    longest_waiting: list[ReportTicket] = field(default_factory=list)
    rejected_count: int = 0


# --- Database queries ---------------------------------------------------------


def count_by_priority(conn: sqlite3.Connection) -> dict[str, int]:
    """
    Count tickets per priority level.

    Always returns HIGH, MEDIUM and LOW keys, using 0 for a level with no
    tickets. Tickets with a NULL priority_level are not counted here; see
    count_unclassified().
    """
    counts = {level: 0 for level in PRIORITY_LEVELS}
    rows = conn.execute(
        """
        SELECT priority_level, COUNT(*) AS n
        FROM tickets
        WHERE priority_level IS NOT NULL
        GROUP BY priority_level
        """
    )
    for row in rows:
        if row["priority_level"] in counts:
            counts[row["priority_level"]] = row["n"]
    return counts


def count_unclassified(conn: sqlite3.Connection) -> int:
    """Number of tickets that have no priority_level yet."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE priority_level IS NULL"
    ).fetchone()
    return row["n"]


def fetch_top_urgent(conn: sqlite3.Connection, limit: int) -> list[Ticket]:
    """HIGH priority tickets, oldest created_at first, at most `limit`."""
    rows = conn.execute(
        """
        SELECT * FROM tickets
        WHERE priority_level = ?
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (PRIORITY_HIGH, limit),
    )
    return [Ticket.from_db_row(row) for row in rows]


def fetch_longest_waiting(conn: sqlite3.Connection, limit: int) -> list[Ticket]:
    """Tickets of any priority, oldest created_at first, at most `limit`."""
    rows = conn.execute(
        """
        SELECT * FROM tickets
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (limit,),
    )
    return [Ticket.from_db_row(row) for row in rows]


def count_rejected(conn: sqlite3.Connection) -> int:
    """Number of rows in rejected_tickets."""
    row = conn.execute("SELECT COUNT(*) AS n FROM rejected_tickets").fetchone()
    return row["n"]


# --- Report assembly ----------------------------------------------------------


def _with_waiting(tickets: list[Ticket], now: datetime) -> list[ReportTicket]:
    """Attach now - created_at to each ticket (never negative)."""
    return [
        ReportTicket(t, max(now - t.created_at_datetime, timedelta(0)))
        for t in tickets
    ]


def build_report_data(
    conn: sqlite3.Connection, now: datetime, top_n: int = DEFAULT_TOP_N
) -> ReportData:
    """
    Collect the report's data from the database.

    `now` is passed in so the report never reads the clock; it must be
    timezone-aware. `top_n` limits both the urgent list and the longest-waiting
    list; 0 gives empty lists.

    :raises ValueError: if `now` is timezone-naive or `top_n` is negative
        (SQLite treats a negative LIMIT as "no limit").
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if top_n < 0:
        raise ValueError(f"top_n must be 0 or more, got {top_n}")

    counts = count_by_priority(conn)
    unclassified = count_unclassified(conn)
    return ReportData(
        generated_at=now,
        total_tickets=sum(counts.values()) + unclassified,
        counts_by_priority=counts,
        unclassified_count=unclassified,
        top_urgent=_with_waiting(fetch_top_urgent(conn, top_n), now),
        longest_waiting=_with_waiting(fetch_longest_waiting(conn, top_n), now),
        rejected_count=count_rejected(conn),
    )
