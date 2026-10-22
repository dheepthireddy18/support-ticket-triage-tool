"""
Seed a demo database with dummy tickets so the report has real output to show.

The tickets are dated relative to `now`, so waiting times always look sensible.
They include a tie on created_at, more HIGH tickets than the default --top, and
two rejected records. Priorities are written directly (as if the rules had
already run), so every ticket is PROCESSED and no ticket is unclassified.

The seeder resets the database it writes to, so it refuses to touch a database
that holds tickets other than demo tickets unless --force is given.

Usage:
    python -m ticket_triage.demo                  # writes data/demo.db
    python -m ticket_triage --db data/demo.db     # report from the demo database
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ticket_triage.db import init_db
from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    STATUS_PROCESSED,
    RejectedRecord,
    Ticket,
)

DEMO_DB_PATH = Path("data/demo.db")

# (id, customer, subject, priority, age before now).
# demo-002 and demo-003 share an age on purpose, to show the id tie-break.
DEMO_TICKETS = (
    ("demo-001", "ACME", "Production outage: orders failing", PRIORITY_HIGH, timedelta(days=2, hours=3)),
    ("demo-002", "GLOBEX", "Cannot log in after password reset", PRIORITY_HIGH, timedelta(days=1, hours=6)),
    ("demo-003", "INITECH", "Customer charged twice", PRIORITY_HIGH, timedelta(days=1, hours=6)),
    ("demo-004", "UMBRELLA", "Data export shows wrong totals", PRIORITY_HIGH, timedelta(hours=5)),
    ("demo-005", "ACME", "Security alert on admin account", PRIORITY_HIGH, timedelta(hours=1)),
    ("demo-006", "HOOLI", "API rate limit reached", PRIORITY_HIGH, timedelta(minutes=20)),
    ("demo-007", "GLOBEX", "Invoice PDF will not download", PRIORITY_MEDIUM, timedelta(days=3)),
    ("demo-008", "INITECH", "Slow dashboard loading", PRIORITY_MEDIUM, timedelta(days=1)),
    ("demo-009", "UMBRELLA", "Update billing address", PRIORITY_MEDIUM, timedelta(hours=8)),
    ("demo-010", "HOOLI", "Feature request: dark mode", PRIORITY_LOW, timedelta(days=6)),
    ("demo-011", "ACME", "Question about the API docs", PRIORITY_LOW, timedelta(days=4)),
    ("demo-012", "GLOBEX", "Typo on the pricing page", PRIORITY_LOW, timedelta(days=2)),
)

DEMO_REJECTED = (
    ({"id": "demo-bad-1", "subject": "No customer given"}, "missing required field(s): customer"),
    ({"id": "demo-bad-2", "created_at": "yesterday"}, "invalid created_at: 'yesterday'"),
)


def seed_demo_db(conn: sqlite3.Connection, now: datetime) -> None:
    """Insert the demo tickets and rejected records, dated relative to `now`."""
    stamp = now.isoformat()
    with conn:
        for ticket_id, customer, subject, priority, age in DEMO_TICKETS:
            ticket = Ticket(
                id=ticket_id,
                subject=subject,
                body=f"Demo ticket from {customer}: {subject}",
                customer=customer,
                created_at=(now - age).isoformat(),
                inserted_at=stamp,
                status=STATUS_PROCESSED,
                priority_level=priority,
                classified_at=stamp,
            )
            conn.execute(
                "INSERT INTO tickets (id, content_hash, subject, body, customer, "
                "created_at, inserted_at, status, priority_level, classified_at, "
                "metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ticket.to_db_tuple(),
            )
        for raw_record, reason in DEMO_REJECTED:
            record = RejectedRecord(
                raw_record=raw_record, rejection_reason=reason, rejected_at=stamp
            )
            conn.execute(
                "INSERT INTO rejected_tickets (raw_record, rejection_reason, "
                "rejected_at) VALUES (?, ?, ?)",
                record.to_db_tuple(),
            )


def count_non_demo_tickets(db_path: Path | str) -> int:
    """Tickets in an existing database whose id does not start with "demo-" (0 if none)."""
    if str(db_path) == ":memory:" or not Path(db_path).is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT COUNT(*) FROM tickets WHERE id NOT LIKE 'demo-%'").fetchone()
        return row[0]
    except sqlite3.OperationalError:
        return 0  # No tickets table, so nothing to lose
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Create (or recreate) the demo database. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m ticket_triage.demo",
        description="Seed a demo database with dummy tickets.",
    )
    parser.add_argument(
        "--db",
        default=str(DEMO_DB_PATH),
        help=f"Path of the demo database (default: {DEMO_DB_PATH}). It is reset first.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reset the database even if it holds tickets that are not demo tickets.",
    )
    args = parser.parse_args(argv)

    try:
        other = 0 if args.force else count_non_demo_tickets(args.db)
        if other:
            print(
                f"error: {args.db} holds {other} ticket(s) that are not demo tickets and "
                "would be erased. Choose another --db, or pass --force to reset it anyway.",
                file=sys.stderr,
            )
            return 2
        conn = init_db(db_path=args.db, reset=True)
    except sqlite3.Error as exc:
        print(f"error: could not use database {args.db}: {exc}", file=sys.stderr)
        return 2
    try:
        seed_demo_db(conn, datetime.now(timezone.utc))
    finally:
        conn.close()
    print(f"Demo database created at {args.db}")
    print(f"Now run: python -m ticket_triage --db {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
