"""
Database connection manager and schema initialization for SQLite.
Configures WAL mode, busy timeout, and provides init_db() runner.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/tickets.db")
SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

# Columns added to `tickets` after the first schema version. CREATE TABLE IF NOT
# EXISTS does not add columns to an existing table, so init_db adds them.
ADDED_TICKET_COLUMNS = {
    "resolved_at": "TEXT",
}


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

    _add_missing_ticket_columns(conn)
    conn.executescript(schema_sql)
    conn.commit()
    return conn


def _add_missing_ticket_columns(conn: sqlite3.Connection) -> None:
    """Bring a `tickets` table created by an older schema up to date (no data lost)."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(tickets);")}
    if not existing:
        return  # No table yet: the schema script creates it in full
    for column, definition in ADDED_TICKET_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE tickets ADD COLUMN {column} {definition};")


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
    args = parser.parse_args(argv)

    conn = init_db(db_path=args.db, reset=args.reset)
    conn.close()
    print(f"Database successfully initialized at {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

