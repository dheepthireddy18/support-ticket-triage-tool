"""
Database connection manager and schema initialization for SQLite.
Configures WAL mode, busy timeout, and provides init_db() runner.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from ticket_triage.intake import ingest_tickets_file

DEFAULT_DB_PATH = Path("data/tickets.db")
SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def get_db_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """
    Open an SQLite connection configured with Row factory, WAL mode, and busy timeout.

    :param db_path: Path to SQLite database file, or ':memory:'. Defaults to data/tickets.db.
    :return: sqlite3.Connection instance with row_factory set to sqlite3.Row.
    """
    resolved_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    is_memory = str(resolved_path) == ":memory:"

    if not is_memory:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(resolved_path), timeout=10.0)
    conn.row_factory = sqlite3.Row

    # Performance and concurrency pragmas
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")

    # In-memory databases do not support or need WAL mode
    if not is_memory:
        conn.execute("PRAGMA journal_mode = WAL;")

    return conn


def init_db(
    db_path: Path | str | None = None,
    schema_path: Path | str | None = None,
    reset: bool = False,
) -> sqlite3.Connection:
    """
    Initialize SQLite database tables using schema.sql.

    :param db_path: Path to database file. Defaults to data/tickets.db.
    :param schema_path: Path to SQL schema file. Defaults to schema.sql in package.
    :param reset: If True, drops existing tickets, rejected_tickets and ticket_rule_results tables first.
    :return: Active sqlite3.Connection to the initialized database.
    """
    conn = get_db_connection(db_path)
    schema_file = Path(schema_path) if schema_path is not None else SCHEMA_FILE

    if not schema_file.is_file():
        raise FileNotFoundError(f"Schema file not found at {schema_file}")

    if reset:
        conn.execute("DROP TABLE IF EXISTS ticket_rule_results;")  # References tickets
        conn.execute("DROP TABLE IF EXISTS tickets;")
        conn.execute("DROP TABLE IF EXISTS rejected_tickets;")
        conn.commit()

    with schema_file.open("r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn.executescript(schema_sql)
    conn.commit()
    return conn


def seed_db(
    db_path: Path | str | None = None,
    sample_path: Path | str | None = None,
    reset: bool = True,
) -> tuple[int, int]:
    """
    Seed SQLite database with sample tickets.
    Initializes tables (resetting them by default) and ingests sample tickets.

    :param db_path: Path to database file. Defaults to data/tickets.db.
    :param sample_path: Path to sample tickets JSON/CSV. Defaults to data/sample_tickets.json.
    :param reset: If True, drops existing tables before seeding (default: True).
    :return: (inserted_count, rejected_count) tuple.
    """
    if sample_path is not None:
        resolved_sample = Path(sample_path)
    elif Path("data/sample_tickets.json").is_file():
        resolved_sample = Path("data/sample_tickets.json")
    else:
        resolved_sample = Path(__file__).resolve().parents[2] / "data" / "sample_tickets.json"

    if not resolved_sample.is_file():
        raise FileNotFoundError(f"Sample tickets file not found at {resolved_sample}")

    conn = init_db(db_path=db_path, reset=reset)
    try:
        result = ingest_tickets_file(conn, resolved_sample)
        return result.inserted, result.rejected
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Standalone CLI helper for python -m ticket_triage.db."""
    parser = argparse.ArgumentParser(
        prog="python -m ticket_triage.db",
        description="Initialize the SQLite database for Support Ticket Triage.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite database file (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop existing tables before recreating schema.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed database with data/sample_tickets.json (5 valid inserted, 3 rejected).",
    )
    args = parser.parse_args(argv)

    if args.seed:
        inserted, rejected = seed_db(db_path=args.db, reset=True)
        print(
            f"Database successfully seeded at {args.db}: "
            f"{inserted} valid ticket(s) inserted, {rejected} rejected."
        )
        return 0

    conn = init_db(db_path=args.db, reset=args.reset)
    conn.close()
    print(f"Database successfully initialized at {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

