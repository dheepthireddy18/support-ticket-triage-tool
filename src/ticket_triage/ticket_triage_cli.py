"""
Command-line entry point for the Support Ticket Triage Tool.
The tool runs three stages in order: load tickets, prioritize them, and write
a daily report. Each stage is a small function so the real intake, priority
and report modules can replace the temporary stand-ins below one at a time.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from ticket_triage.db import DEFAULT_DB_PATH, init_db


def load_tickets(path: Path) -> list[dict]:
    """Read tickets from a JSON file (stand-in: no validation, no CSV)."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def prioritize(tickets: list[dict]) -> list[tuple[dict, str]]:
    """Assign every ticket the same priority (stand-in for the real rules)."""
    return [(ticket, "UNRATED") for ticket in tickets]


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

    # Automatically ensure SQLite database and schema exist on startup
    init_db(db_path=args.db)

    input_path = DATA_DIR / args.input
    output_path = REPORTS_DIR / args.output

    if not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 2

    try:
        tickets = load_tickets(input_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: could not read {input_path}: {exc}", file=sys.stderr)
        return 2

    prioritized = prioritize(tickets)
    report = build_report(prioritized)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"Processed {len(prioritized)} tickets. Report saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
