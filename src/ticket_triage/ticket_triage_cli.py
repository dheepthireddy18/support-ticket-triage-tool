"""
Command-line entry point for the Support Ticket Triage Tool.
The tool runs three stages in order: load tickets into SQLite, prioritize them,
and write a daily report from the database. Then a background worker keeps
escalating tickets that stay open too long, until Ctrl+C (skip it with --once).
Intake and the report are still temporary stand-ins that the real intake and
report modules will replace.
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from ticket_triage.db import DEFAULT_DB_PATH, init_db
from ticket_triage.models import RejectedRecord, Ticket
from ticket_triage.priority.base import parse_timestamp, utc_now
from ticket_triage.priority.config import import_rules, load_engine, load_worker_settings
from ticket_triage.priority.repository import (
    ResolveOutcome,
    prioritize_pending,
    resolve_ticket,
)
from ticket_triage.priority.worker import EscalationWorker

REQUIRED_FIELDS = ("id", "subject", "body", "customer", "created_at")


def load_tickets(conn: sqlite3.Connection, path: Path) -> tuple[int, int]:
    """
    Insert valid tickets from a JSON file as PENDING (stand-in for the real intake:
    JSON only, basic checks). Returns (inserted, rejected). Re-importing a ticket
    that is already stored is skipped silently.
    """
    with path.open(encoding="utf-8") as f:
        records = json.load(f)

    inserted = rejected = 0
    for record in records:
        missing = [name for name in REQUIRED_FIELDS if not record.get(name)]
        reason = f"missing required field(s): {', '.join(missing)}" if missing else None
        if reason is None:
            try:
                parse_timestamp(str(record["created_at"]))
            except ValueError:
                reason = f"invalid created_at: {record['created_at']!r}"

        if reason is None:
            ticket = Ticket.from_dict(record)
            stored = conn.execute(
                "SELECT content_hash FROM tickets WHERE id = ?", (ticket.id,)
            ).fetchone()
            if stored is not None:
                if stored["content_hash"] == ticket.content_hash:
                    continue  # Same ticket imported again
                reason = f"duplicate id: {ticket.id}"
            else:
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
                    inserted += 1
                    continue
                except sqlite3.IntegrityError:
                    reason = "duplicate content of an existing ticket"

        with conn:
            conn.execute(
                "INSERT INTO rejected_tickets (raw_record, rejection_reason, rejected_at) VALUES (?, ?, ?)",
                RejectedRecord(raw_record=record, rejection_reason=reason).to_db_tuple(),
            )
        rejected += 1
    return inserted, rejected


def build_report(conn: sqlite3.Connection) -> str:
    """Build a plain-text report of open tickets from the database (stand-in)."""
    rows = conn.execute(
        """
        SELECT id, subject, priority_level, priority_score
        FROM tickets
        WHERE status = 'PROCESSED'
        ORDER BY CASE priority_level WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                 priority_score DESC, created_at
        """
    ).fetchall()
    lines = [
        "Daily Support Ticket Summary",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Open tickets: {len(rows)}",
        "",
    ]
    for row in rows:
        lines.append(
            f"[{row['priority_level']}] {row['priority_score']:5.1f}  {row['id']} - {row['subject']}"
        )
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


def positive_float(value: str) -> float:
    """argparse type: a number greater than 0."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a number")
    if number <= 0:
        raise argparse.ArgumentTypeError(f"'{value}' must be more than 0")
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
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--input",
        type=file_name,
        help=f"Name of the tickets file (CSV or JSON) inside {DATA_DIR}/.",
    )
    action.add_argument(
        "--resolve",
        metavar="TICKET_ID",
        help="Mark one ticket as resolved so it is no longer escalated, then exit.",
    )
    action.add_argument(
        "--import-rules",
        type=file_name,
        metavar="FILE",
        help=f"Apply priority rule settings from a JSON file inside {DATA_DIR}/, then exit.",
    )
    parser.add_argument(
        "--output",
        default="daily_summary.txt",
        type=file_name,
        help=f"Name of the report file, saved inside {REPORTS_DIR}/.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Exit after writing the report instead of running the escalation worker.",
    )
    parser.add_argument(
        "--check-interval",
        type=positive_float,
        metavar="SECONDS",
        help="How often the escalation worker checks open tickets (default from settings: 60).",
    )
    parser.add_argument(
        "--escalate-after",
        type=positive_float,
        metavar="HOURS",
        help="Raise an open ticket one level after this many hours at its level (default from settings: 24).",
    )
    return parser


def run_resolve(conn: sqlite3.Connection, ticket_id: str) -> int:
    outcome, ticket = resolve_ticket(conn, ticket_id, utc_now())
    if outcome is ResolveOutcome.NOT_FOUND:
        print(f"error: no ticket with id {ticket_id}", file=sys.stderr)
        return 1
    if outcome is ResolveOutcome.ALREADY_RESOLVED:
        print(f"Ticket {ticket_id} is already resolved.")
        return 0
    level = ticket.priority_level or "unprioritized"
    print(f"Resolved ticket {ticket_id} (was {level}).")
    return 0


def wait_for_interrupt() -> None:
    """Block until Ctrl+C. Short sleeps keep KeyboardInterrupt responsive everywhere."""
    while True:
        time.sleep(0.5)


def run_worker(args: argparse.Namespace, conn: sqlite3.Connection) -> int:
    settings = load_worker_settings(conn)
    interval = args.check_interval or settings.check_interval_seconds
    after = (
        timedelta(hours=args.escalate_after) if args.escalate_after else settings.escalate_after
    )

    def report_escalations(ticket_ids: list[str]) -> None:
        print(f"Escalated {len(ticket_ids)} ticket(s): {', '.join(ticket_ids)}", flush=True)

    worker = EscalationWorker(args.db, interval, after, on_escalate=report_escalations)
    worker.start()
    print(
        f"Escalation worker running (every {interval:g}s, escalate after "
        f"{after.total_seconds() / 3600:g}h). Press Ctrl+C to stop.",
        flush=True,
    )
    try:
        wait_for_interrupt()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
    print("Escalation worker stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the full pipeline. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.init_db:
        conn = init_db(db_path=args.db)
        conn.close()
        print(f"Database successfully initialized at {args.db}")
        return 0

    if not (args.input or args.resolve or args.import_rules):
        parser.error("the following arguments are required: --input")

    # Automatically ensure SQLite database, schema and default settings exist on startup
    conn = init_db(db_path=args.db)
    try:
        if args.resolve:
            return run_resolve(conn, args.resolve)

        if args.import_rules:
            rules_path = DATA_DIR / args.import_rules
            try:
                import_rules(conn, rules_path)
            except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                print(f"error: could not import {rules_path}: {exc}", file=sys.stderr)
                return 2
            print(f"Priority rules updated from {rules_path}")
            return 0

        input_path = DATA_DIR / args.input
        output_path = REPORTS_DIR / args.output

        if not input_path.is_file():
            print(f"error: input file not found: {input_path}", file=sys.stderr)
            return 2

        try:
            inserted, rejected = load_tickets(conn, input_path)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"error: could not read {input_path}: {exc}", file=sys.stderr)
            return 2

        try:
            engine = load_engine(conn)
        except ValueError as exc:
            print(f"error: invalid priority rule settings: {exc}", file=sys.stderr)
            return 2
        prioritized = prioritize_pending(conn, engine, utc_now())

        report = build_report(conn)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")

        print(
            f"Loaded {inserted} new ticket(s), rejected {rejected}. "
            f"Prioritized {len(prioritized)}. Report saved to {output_path}",
            flush=True,
        )

        if args.once:
            return 0
        return run_worker(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
