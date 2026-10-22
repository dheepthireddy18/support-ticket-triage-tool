"""
Command-line entry point for the Support Ticket Triage Tool.
The tool runs three stages in order: load tickets, prioritize them, and write
a daily report. Each stage is a small function so the real intake and report
modules can replace the temporary stand-ins below one at a time.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from ticket_triage.db import DEFAULT_DB_PATH, init_db
from ticket_triage.models import Ticket
from ticket_triage.priority.config import (
    DEFAULT_RULES_FILE,
    PriorityConfig,
    build_engine,
    load_config,
)
from ticket_triage.priority.engine import PriorityEngine
from ticket_triage.priority.repository import fetch_prioritized, prioritize_pending

REQUIRED_FIELDS = ("id", "subject", "body", "customer", "created_at")


def load_tickets(path: Path) -> list[dict]:
    """Read tickets from a JSON file (stand-in: no validation, no CSV)."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def store_tickets(conn: sqlite3.Connection, records: list[dict]) -> tuple[int, int]:
    """
    Store valid records as PENDING tickets with no priority (stand-in for the real
    intake: basic checks only, rejected records are skipped, not recorded).
    A ticket already stored with the same content is skipped silently.
    Returns (stored, skipped).
    """
    stored = skipped = 0
    for record in records:
        if not isinstance(record, dict) or any(not record.get(f) for f in REQUIRED_FIELDS):
            skipped += 1
            continue
        try:
            ticket = Ticket.from_dict(record)
            ticket.created_at_datetime  # Reject unparseable dates
        except (ValueError, TypeError):
            skipped += 1
            continue
        ticket.priority_level = None  # Priorities come from the rules, not the input

        existing = conn.execute(
            "SELECT content_hash FROM tickets WHERE id = ?", (ticket.id,)
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] != ticket.content_hash:
                skipped += 1  # Same id, different content: a duplicate id
            continue
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO tickets (
                        id, content_hash, subject, body, customer, created_at,
                        inserted_at, status, priority_level, classified_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ticket.to_db_tuple(),
                )
            stored += 1
        except sqlite3.IntegrityError:
            skipped += 1  # Same content as an existing ticket
    return stored, skipped


def load_rules() -> PriorityConfig:
    """
    Read data/priority_rules.json. If it is missing, use the built-in rules and
    say so. Raises ValueError if the file is invalid.
    """
    path = DATA_DIR / DEFAULT_RULES_FILE
    if not path.is_file():
        print(f"note: {path} not found; using the built-in priority rules", file=sys.stderr)
        return load_config(None)
    try:
        return load_config(path)
    except ValueError as exc:
        raise ValueError(f"invalid priority rules in {path}: {exc}") from exc


def prioritize(conn: sqlite3.Connection, engine: PriorityEngine) -> list[tuple[dict, str]]:
    """Give every PENDING ticket a priority, then return all prioritized tickets."""
    prioritize_pending(conn, engine, datetime.now(timezone.utc))
    return [(ticket.to_dict(), ticket.priority_level) for ticket in fetch_prioritized(conn)]


def build_report(prioritized: list[tuple[dict, str]]) -> str:
    """Build a plain-text report (stand-in for the real report builder)."""
    lines = [
        "Daily Support Ticket Summary",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Total tickets: {len(prioritized)}",
        "",
    ]
    for ticket, priority in prioritized:
        lines.append(f"[{priority}] {ticket.get('id', '?')} - {ticket.get('subject', '')}")
    return "\n".join(lines) + "\n"


# --- CLI ---------------------------------------------------------------------

DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")


def file_name(value: str) -> str:
    """argparse type: accept a bare file name, reject anything with a folder."""
    if not value or Path(value).name != value:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be a file name only, not a path"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ticket-triage",
        description="Prioritize support tickets and generate a daily report.",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize the SQLite database tables and exit.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite database file (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--input",
        type=file_name,
        help=f"Name of the tickets file (CSV or JSON) inside {DATA_DIR}/.",
    )
    parser.add_argument(
        "--output",
        default="daily_summary.txt",
        type=file_name,
        help=f"Name of the report file, saved inside {REPORTS_DIR}/.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the full pipeline. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.init_db:
        conn = init_db(db_path=args.db)
        conn.close()
        print(f"Database successfully initialized at {args.db}")
        return 0

    if not args.input:
        parser.error("the following arguments are required: --input")

    input_path = DATA_DIR / args.input
    output_path = REPORTS_DIR / args.output

    if not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 2

    # Read (and so validate) the priority rules before doing any work
    try:
        engine = build_engine(load_rules())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        tickets = load_tickets(input_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: could not read {input_path}: {exc}", file=sys.stderr)
        return 2

    # Automatically ensure SQLite database and schema exist on startup
    conn = init_db(db_path=args.db)
    try:
        stored, skipped = store_tickets(conn, tickets)
        prioritized = prioritize(conn, engine)
    finally:
        conn.close()
    report = build_report(prioritized)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(
        f"Stored {stored} new ticket(s), skipped {skipped}. "
        f"{len(prioritized)} prioritized ticket(s). Report saved to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
