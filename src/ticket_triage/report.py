"""
Data layer for the daily summary report.

The query functions are read-only: each takes an open sqlite3.Connection (with
row_factory=sqlite3.Row, as returned by get_db_connection) and never writes.
Counting, ordering and limits are done in SQL. created_at is stored as
ISO-8601 text, so ORDER BY created_at is chronological as long as the stored
values share one format. Ties are broken by id so results are deterministic.

build_report_data() combines the queries into one ReportData. render_report()
turns that into manager-first plain text and write_report() saves it.
write_report_pdf() writes the same report as a PDF; it needs the optional
"reportlab" package and everything else works without it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    Ticket,
)

PRIORITY_LEVELS = (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)
DEFAULT_TOP_N = 5
MAX_TOP_N = 2**31 - 1  # Far more rows than a report holds; keeps LIMIT inside SQLite's range

REPORT_TITLE = "Daily Support Ticket Summary"
NO_URGENT_MESSAGE = "No urgent tickets today"
NO_TICKETS_MESSAGE = "No tickets to show"
UNCLASSIFIED_LABEL = "Unclassified"
NEEDS_ATTENTION_TITLE = "Needs attention first"
COUNTS_TITLE = "Counts by priority"
WAITING_LONGEST_TITLE = "Waiting longest"

PDF_INSTALL_HINT = (
    'PDF output needs the optional "reportlab" package. '
    'Install it with: pip install -e ".[pdf]"'
)

# Waiting-time buckets for the PDF chart, as (label, lower bound in days). The SQL in
# count_by_age() uses the same bounds.
AGE_BUCKETS = (
    ("Under 1 day", 0),
    ("1-3 days", 1),
    ("3-7 days", 3),
    ("7+ days", 7),
)


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
    waiting_buckets: dict[str, int] = field(default_factory=dict)  # Used by the PDF chart


class PdfUnavailableError(RuntimeError):
    """PDF output was requested but the optional reportlab package is missing."""


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


def count_by_age(conn: sqlite3.Connection, now: datetime) -> dict[str, int]:
    """
    Count tickets per waiting-time bucket (see AGE_BUCKETS), measured at `now`.

    Always returns every bucket, using 0 for an empty one. A ticket created after
    `now` counts as "Under 1 day"; one with an unreadable created_at is not counted.
    """
    counts = {label: 0 for label, _ in AGE_BUCKETS}
    rows = conn.execute(
        """
        SELECT CASE WHEN age < 1 THEN 0 WHEN age < 3 THEN 1 WHEN age < 7 THEN 2 ELSE 3 END
                   AS bucket,
               COUNT(*) AS n
        FROM (
            SELECT julianday(?) - julianday(created_at) AS age
            FROM tickets
            WHERE julianday(created_at) IS NOT NULL
        )
        GROUP BY bucket
        """,
        (now.isoformat(),),
    )
    labels = [label for label, _ in AGE_BUCKETS]
    for row in rows:
        counts[labels[row["bucket"]]] = row["n"]
    return counts


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
    list; 0 gives empty lists, and anything above MAX_TOP_N is treated as MAX_TOP_N.

    :raises ValueError: if `now` is timezone-naive or `top_n` is negative
        (SQLite treats a negative LIMIT as "no limit").
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if top_n < 0:
        raise ValueError(f"top_n must be 0 or more, got {top_n}")
    top_n = min(top_n, MAX_TOP_N)

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
        waiting_buckets=count_by_age(conn, now),
    )


# --- Rendering and output -----------------------------------------------------


def format_waiting(waiting: timedelta) -> str:
    """
    Format a waiting time for people: "2d 3h", "5h 12m", "42m" or "<1m".

    Only the two largest units are shown. Seconds are dropped, and a negative
    waiting time is shown as zero.
    """
    total_minutes = max(int(waiting.total_seconds() // 60), 0)
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return "<1m"


def _one_line(text: str) -> str:
    """Collapse all whitespace, including newlines, so a ticket stays on one line."""
    return " ".join(text.split())


def _ticket_row(entry: ReportTicket) -> str:
    """One report line: id, customer, subject, priority and waiting time."""
    ticket = entry.ticket
    priority = ticket.priority_level or UNCLASSIFIED_LABEL
    return (
        f"  {_one_line(ticket.id)} | {_one_line(ticket.customer)} "
        f"| {_one_line(ticket.subject)} | {priority} "
        f"| waiting {format_waiting(entry.waiting)}"
    )


def _ticket_section(title: str, entries: list[ReportTicket], empty: str) -> list[str]:
    """A titled block of ticket rows, or the empty message so it is never blank."""
    lines = [title, "-" * len(title)]
    lines += [_ticket_row(e) for e in entries] if entries else [f"  {empty}"]
    return lines


def render_report(data: ReportData) -> str:
    """
    Render the report as plain text, manager-first: header, then tickets that
    need attention, then counts by priority, then tickets waiting longest.
    The "Unclassified" line appears only when there are unclassified tickets.
    """
    lines = [
        REPORT_TITLE,
        f"Date: {data.generated_at.strftime('%Y-%m-%d %H:%M %Z').strip()}",
        f"Total tickets: {data.total_tickets}",
        f"Rejected records: {data.rejected_count}",
        "",
    ]
    lines += _ticket_section(
        NEEDS_ATTENTION_TITLE, data.top_urgent, NO_URGENT_MESSAGE
    )
    lines += ["", COUNTS_TITLE, "-" * len(COUNTS_TITLE)]
    for level in PRIORITY_LEVELS:
        lines.append(f"  {level}: {data.counts_by_priority[level]}")
    if data.unclassified_count > 0:
        lines.append(f"  {UNCLASSIFIED_LABEL}: {data.unclassified_count}")
    lines.append("")
    lines += _ticket_section(
        WAITING_LONGEST_TITLE, data.longest_waiting, NO_TICKETS_MESSAGE
    )
    return "\n".join(lines) + "\n"


def write_report(text: str, path: Path | str) -> Path:
    """Write the report to `path` as UTF-8, creating missing folders. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- PDF output (optional) ----------------------------------------------------


def require_pdf_support() -> None:
    """Raise PdfUnavailableError, with install instructions, if reportlab is missing."""
    try:
        import reportlab  # noqa: F401
    except ImportError as exc:
        raise PdfUnavailableError(PDF_INSTALL_HINT) from exc


def write_report_pdf(data: ReportData, path: Path | str) -> Path:
    """
    Write the report to `path` as a designed PDF (see ticket_triage.report_pdf).

    Characters the PDF fonts cannot show (outside Western European text) are
    replaced with "?". Returns the path.

    :raises PdfUnavailableError: if reportlab is not installed.
    """
    require_pdf_support()
    from ticket_triage.report_pdf import build_pdf  # Imports reportlab, so only when needed

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(data, path)
    return path
