"""
Command-line entry point for the Support Ticket Triage Tool.
The tool loads tickets into SQLite (when --input is given) and writes the daily
report from the database. Intake is still a temporary stand-in that the real
intake and priority modules will replace: it stores valid tickets without a
priority, so they show up as "Unclassified" in the report.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from ticket_triage.db import DEFAULT_DB_PATH, init_db
from ticket_triage.models import RejectedRecord, Ticket
from ticket_triage.report import (
    DEFAULT_TOP_N,
    build_report_data,
    render_report,
    write_report,
)

# TODO(after Rachit's intake and Natalie's rules are merged): delete everything
# from REQUIRED_FIELDS to load_tickets() below and call Rachit's intake instead.
# Then call Natalie's prioritize_pending() so tickets get a real priority
# (this stand-in leaves it empty, so every ticket shows as "Unclassified").
# Natalie's CLI also rewrote this file: expect merge conflicts here, and her
# escalation worker blocks until Ctrl+C unless --once is passed.
REQUIRED_FIELDS = ("id", "subject", "body", "customer", "created_at")


def _parse_record(record: object) -> tuple[Ticket | None, str | None]:
    """Turn one raw record into a Ticket, or explain why it is rejected."""
    if not isinstance(record, dict):
        return None, "record is not an object"
    missing = [name for name in REQUIRED_FIELDS if not record.get(name)]
    if missing:
        return None, f"missing required field(s): {', '.join(missing)}"

    ticket = Ticket.from_dict(record)
    try:
        ticket.created_at_datetime
    except ValueError:
        return None, f"invalid created_at: {record['created_at']!r}"
    ticket.priority_level = None  # Priorities come from the rules, not the input
    return ticket, None


def _store_ticket(conn: sqlite3.Connection, ticket: Ticket) -> tuple[bool, str | None]:
    """
    Insert a ticket. Returns (inserted, rejection_reason). Importing a ticket
    that is already stored is skipped silently: (False, None).
    """
    stored = conn.execute(
        "SELECT content_hash FROM tickets WHERE id = ?", (ticket.id,)
    ).fetchone()
    if stored is not None:
        if stored["content_hash"] == ticket.content_hash:
            return False, None
        return False, f"duplicate id: {ticket.id}"
    try:
        with conn:
            conn.execute(
                "INSERT INTO tickets (id, content_hash, subject, body, customer, "
                "created_at, inserted_at, status, priority_level, classified_at, "
                "metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ticket.to_db_tuple(),
            )
    except sqlite3.IntegrityError:
        return False, "duplicate content of an existing ticket"
    return True, None


def load_tickets(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
    """
    Store valid tickets from a JSON file as PENDING with no priority, and record
    the others in rejected_tickets (stand-in: JSON only, basic checks).
    Returns (inserted, rejected).
    """
    with path.open(encoding="utf-8") as f:
        records = json.load(f)

    inserted = rejected = 0
    for record in records:
        ticket, reason = _parse_record(record)
        if ticket is not None:
            was_inserted, reason = _store_ticket(conn, ticket)
            inserted += was_inserted
        if reason is not None:
            with conn:
                conn.execute(
                    "INSERT INTO rejected_tickets (raw_record, rejection_reason, "
                    "rejected_at) VALUES (?, ?, ?)",
                    RejectedRecord(
                        raw_record=record, rejection_reason=reason
                    ).to_db_tuple(),
                )
            rejected += 1
    return inserted, rejected


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


def non_negative_int(value: str) -> int:
    """argparse type: a whole number that is 0 or more."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a whole number")
    if number < 0:
        raise argparse.ArgumentTypeError(f"'{value}' must be 0 or more")
    return number


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
        help=(
            f"Name of the tickets file (CSV or JSON) inside {DATA_DIR}/. "
            "Optional: without it, the report is built from what is already in the database."
        ),
    )
    parser.add_argument(
        "--output",
        default="daily_summary.txt",
        type=file_name,
        help=f"Name of the report file, saved inside {REPORTS_DIR}/.",
    )
    parser.add_argument(
        "--top",
        type=non_negative_int,
        default=DEFAULT_TOP_N,
        metavar="N",
        help=(
            "How many tickets to list under 'Needs attention first' and "
            f"'Waiting longest' (default: {DEFAULT_TOP_N})."
        ),
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

    # Automatically ensure SQLite database and schema exist on startup
    conn = init_db(db_path=args.db)
    try:
        loaded = ""
        # TODO(after merge): keep --input optional next to Natalie's --resolve.
        if args.input:
            input_path = DATA_DIR / args.input
            if not input_path.is_file():
                print(f"error: input file not found: {input_path}", file=sys.stderr)
                return 2
            try:
                inserted, rejected = load_tickets(conn, input_path)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"error: could not read {input_path}: {exc}", file=sys.stderr)
                return 2
            loaded = f"Loaded {inserted} new ticket(s), rejected {rejected}. "

        # The clock is read here and nowhere in the report module.
        data = build_report_data(conn, datetime.now(timezone.utc), args.top)
    finally:
        conn.close()

    output_path = write_report(render_report(data), REPORTS_DIR / args.output)
    print(f"{loaded}Report saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
