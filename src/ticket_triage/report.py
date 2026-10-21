"""
Read-only database queries that feed the daily summary report.

Every function takes an open sqlite3.Connection (with row_factory=sqlite3.Row,
as returned by get_db_connection) and never writes. Counting happens in SQL;
ordering and limits are done in SQL too. created_at is stored as ISO-8601 text,
so ORDER BY created_at is chronological as long as the stored values share one
format. Ties are broken by id so results are deterministic.
"""

from __future__ import annotations

import sqlite3

from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    Ticket,
)

PRIORITY_LEVELS = (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)


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
