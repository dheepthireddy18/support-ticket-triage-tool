"""
Unit tests for SQLite database connection, WAL mode, migrations, and CLI flag.
Compatible with both `pytest` and Python's built-in `unittest`.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ticket_triage.db import DEFAULT_DB_PATH, get_db_connection, init_db, seed_db
from ticket_triage.ticket_triage_cli import main as cli_main


class TestDatabaseConnectionAndInit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_tickets.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_db_connection_in_memory(self):
        conn = get_db_connection(":memory:")
        self.assertIsInstance(conn, sqlite3.Connection)
        self.assertEqual(conn.row_factory, sqlite3.Row)
        conn.close()

    def test_get_db_connection_file_has_wal_mode(self):
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

            cursor = conn.execute("PRAGMA busy_timeout;")
            timeout = cursor.fetchone()[0]
            self.assertEqual(timeout, 5000)
        finally:
            conn.close()

    def test_init_db_creates_tables_and_indexes(self):
        conn = init_db(self.db_path)
        try:
            # Verify tables exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            )
            tables = [row["name"] for row in cursor.fetchall()]
            self.assertIn("tickets", tables)
            self.assertIn("rejected_tickets", tables)

            # Verify indexes exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name;"
            )
            indexes = [row["name"] for row in cursor.fetchall()]
            self.assertIn("idx_tickets_status", indexes)
            self.assertIn("idx_tickets_created_at", indexes)
            self.assertIn("idx_tickets_inserted_at", indexes)
            self.assertIn("idx_rejected_tickets_rejected_at", indexes)
        finally:
            conn.close()

    def test_init_db_is_idempotent(self):
        # Calling init_db multiple times should not fail
        conn1 = init_db(self.db_path)
        conn1.close()

        conn2 = init_db(self.db_path)
        cursor = conn2.execute("SELECT COUNT(*) AS cnt FROM tickets;")
        self.assertEqual(cursor.fetchone()["cnt"], 0)
        conn2.close()

    def test_init_db_reset_drops_and_recreates_tables(self):
        conn = init_db(self.db_path)
        conn.execute(
            """
            INSERT INTO tickets (id, content_hash, subject, body, customer, created_at, inserted_at)
            VALUES ('t-1', 'hash1', 'Sub', 'Body', 'Cust', '2026-10-19T08:00:00Z', '2026-10-20T10:00:00Z')
            """
        )
        conn.commit()

        # Re-initialize with reset=True
        conn_reset = init_db(self.db_path, reset=True)
        cursor = conn_reset.execute("SELECT COUNT(*) AS cnt FROM tickets;")
        self.assertEqual(cursor.fetchone()["cnt"], 0)
        conn_reset.close()

    def test_cli_init_db_flag(self):
        custom_db = Path(self.temp_dir.name) / "cli_init.db"
        exit_code = cli_main(["--init-db", "--db", str(custom_db)])
        self.assertEqual(exit_code, 0)
        self.assertTrue(custom_db.is_file())

        # Verify tables exist in the initialized DB
        conn = sqlite3.connect(custom_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tickets';"
        )
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_seed_db(self):
        inserted, rejected = seed_db(db_path=self.db_path, reset=True)
        self.assertEqual(inserted, 5)
        self.assertEqual(rejected, 3)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        valid_count = conn.execute("SELECT COUNT(*) AS cnt FROM tickets;").fetchone()["cnt"]
        rejected_count = conn.execute("SELECT COUNT(*) AS cnt FROM rejected_tickets;").fetchone()["cnt"]
        conn.close()

        self.assertEqual(valid_count, 5)
        self.assertEqual(rejected_count, 3)

    def test_cli_seed_flag(self):
        custom_db = Path(self.temp_dir.name) / "cli_seed.db"
        exit_code = cli_main(["--seed", "--db", str(custom_db)])
        self.assertEqual(exit_code, 0)
        self.assertTrue(custom_db.is_file())

        conn = sqlite3.connect(custom_db)
        conn.row_factory = sqlite3.Row
        valid_count = conn.execute("SELECT COUNT(*) AS cnt FROM tickets;").fetchone()["cnt"]
        conn.close()
        self.assertEqual(valid_count, 5)


if __name__ == "__main__":
    unittest.main()

