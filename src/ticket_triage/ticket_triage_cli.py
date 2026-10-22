"""
Command-line entry point for the Support Ticket Triage Tool.
The tool runs three stages in order: load tickets into SQLite (when --input is
given), prioritize every ticket still waiting for a priority, and write the daily
report from the database, as text or (optionally) PDF.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from ticket_triage.db import DEFAULT_DB_PATH, init_db, seed_db
from ticket_triage.intake import ingest_tickets_file
from ticket_triage.priority.base import utc_now
from ticket_triage.priority.config import (
    DEFAULT_RULES_FILE,
    PriorityConfig,
    build_engine,
    load_config,
)
from ticket_triage.priority.repository import prioritize_pending
from ticket_triage.readers import FileReadError
from ticket_triage.report import (
    DEFAULT_TOP_N,
    PdfUnavailableError,
    build_report_data,
    render_report,
    require_pdf_support,
    write_report,
    write_report_pdf,
)


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


# --- CLI ---------------------------------------------------------------------

REPORT_FORMATS = ("txt", "pdf")
DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")


def file_name(value: str) -> str:
    """argparse type: accept a bare file name, reject anything with a folder."""
    if not value or Path(value).name != value:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be a file name only, not a path"
        )
    return value


def positive_int(value: str) -> int:
    """argparse type: a whole number that is 1 or more."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a whole number")
    if number < 1:
        raise argparse.ArgumentTypeError(f"'{value}' must be 1 or more")
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
        "--seed",
        action="store_true",
        help="Seed the SQLite database with data/sample_tickets.json and exit.",
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
            "Optional: without it, the report is built from the tickets already in the database."
        ),
    )
    parser.add_argument(
        "--output",
        type=file_name,
        help=(
            f"Name of the report file, saved inside {REPORTS_DIR}/ "
            "(default: daily_summary.txt, or daily_summary.pdf with --format pdf)."
        ),
    )
    parser.add_argument(
        "--format",
        choices=REPORT_FORMATS,
        default="txt",
        help='Report format: "txt" (default) or "pdf" (needs: pip install -e ".[pdf]").',
    )
    parser.add_argument(
        "--top",
        type=positive_int,
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

    if args.seed:
        inserted, rejected = seed_db(db_path=args.db)
        print(
            f"Database successfully seeded at {args.db}: "
            f"{inserted} valid ticket(s) inserted, {rejected} rejected."
        )
        return 0

    # Fail before touching the database if PDF output cannot work.
    if args.format == "pdf":
        try:
            require_pdf_support()
        except PdfUnavailableError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    output_path = REPORTS_DIR / (args.output or f"daily_summary.{args.format}")

    input_path = DATA_DIR / args.input if args.input else None
    if input_path is not None and not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 2

    # Read (and so validate) the priority rules before doing any work
    try:
        engine = build_engine(load_rules())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Automatically ensure SQLite database and schema exist on startup
    db_is_new = args.db != ":memory:" and not Path(args.db).exists()
    try:
        conn = init_db(db_path=args.db)
    except sqlite3.Error as exc:
        print(f"error: could not open database {args.db}: {exc}", file=sys.stderr)
        return 2

    if db_is_new and input_path is None:
        # Most likely a mistyped --db: say so instead of reporting an empty day silently.
        print(
            f"note: {args.db} did not exist, so an empty database was created. "
            "Use --input to load tickets.",
            file=sys.stderr,
        )

    ingest_result = None
    try:
        if input_path is not None:
            try:
                ingest_result = ingest_tickets_file(conn, input_path)
            except (FileReadError, json.JSONDecodeError, OSError) as exc:
                print(f"error: could not read {input_path}: {exc}", file=sys.stderr)
                return 2

        # Always score tickets still waiting for a priority, then report from the
        # database. One shared "now" keeps the scoring and the report consistent.
        now = utc_now()
        prioritize_pending(conn, engine, now)
        data = build_report_data(conn, now, args.top)
    finally:
        conn.close()

    try:
        if args.format == "pdf":
            write_report_pdf(data, output_path)
        else:
            write_report(render_report(data), output_path)
    except OSError as exc:
        print(f"error: could not write the report to {output_path}: {exc}", file=sys.stderr)
        return 2

    loaded = (
        f"Stored {ingest_result.inserted} new ticket(s), skipped {ingest_result.rejected}. "
        if ingest_result is not None
        else ""
    )
    print(
        f"{loaded}{sum(data.counts_by_priority.values())} prioritized ticket(s). "
        f"Report saved to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
