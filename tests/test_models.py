"""
Unit tests for Ticket and RejectedRecord domain models and SQLite schema.
Compatible with both `pytest` and Python's built-in `unittest`.
"""

import sqlite3
import unittest
from datetime import datetime
from pathlib import Path

from ticket_triage.models import RejectedRecord, Ticket, compute_content_hash

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "src" / "ticket_triage" / "schema.sql"


class TestTicketAndSchema(unittest.TestCase):
    def setUp(self):
        """Set up an in-memory SQLite database initialized with schema.sql."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        with SCHEMA_PATH.open(encoding="utf-8") as f:
            self.conn.executescript(f.read())

    def tearDown(self):
        self.conn.close()

    def test_ticket_creation_and_auto_content_hash(self):
        ticket = Ticket(
            id="t-100",
            subject="Login error",
            body="Cannot login with my credentials",
            customer="CUST-1",
            created_at="2026-10-19T08:30:00Z",
        )
        self.assertEqual(ticket.id, "t-100")
        self.assertEqual(ticket.status, "PENDING")
        self.assertIsNone(ticket.priority_level)
        self.assertTrue(ticket.content_hash != "")
        self.assertTrue(ticket.inserted_at != "")
        expected_hash = compute_content_hash(
            "CUST-1", "Login error", "Cannot login with my credentials"
        )
        self.assertEqual(ticket.content_hash, expected_hash)

    def test_ticket_datetime_properties(self):
        ticket = Ticket(
            id="t-101",
            subject="Payment failed",
            body="Card declined",
            customer="CUST-2",
            created_at="2026-10-19T10:15:30Z",
            inserted_at="2026-10-20T12:00:00Z",
        )
        created_dt = ticket.created_at_datetime
        self.assertIsInstance(created_dt, datetime)
        self.assertEqual(created_dt.year, 2026)
        self.assertEqual(created_dt.month, 10)
        self.assertEqual(created_dt.day, 19)
        self.assertEqual(created_dt.hour, 10)
        self.assertEqual(created_dt.minute, 15)
        self.assertEqual(created_dt.second, 30)
        self.assertIsNotNone(created_dt.tzinfo)

        inserted_dt = ticket.inserted_at_datetime
        self.assertIsInstance(inserted_dt, datetime)
        self.assertEqual(inserted_dt.day, 20)

    def test_ticket_dict_roundtrip_with_extra_metadata(self):
        raw_data = {
            "id": "t-102",
            "subject": "Billing inquiry",
            "body": "Need past receipt",
            "customer": "CUST-3",
            "created_at": "2026-10-18T12:00:00Z",
            "inserted_at": "2026-10-20T12:30:00Z",
            "department": "Finance",
            "tags": ["vip", "urgent"],
        }
        ticket = Ticket.from_dict(raw_data)
        self.assertEqual(ticket.id, "t-102")
        self.assertEqual(ticket.inserted_at, "2026-10-20T12:30:00Z")
        self.assertEqual(ticket.metadata.get("department"), "Finance")
        self.assertEqual(ticket.metadata.get("tags"), ["vip", "urgent"])

        as_dict = ticket.to_dict()
        self.assertEqual(as_dict["id"], "t-102")
        self.assertEqual(as_dict["metadata"]["department"], "Finance")
        self.assertEqual(as_dict["metadata"]["tags"], ["vip", "urgent"])

    def test_ticket_sqlite_roundtrip(self):
        ticket = Ticket(
            id="t-103",
            subject="Database lock issue",
            body="Receiving SQLite busy error",
            customer="CUST-4",
            created_at="2026-10-20T14:00:00Z",
            inserted_at="2026-10-20T14:05:00Z",
            priority_level="HIGH",
            metadata={"client_version": "2.4.1"},
        )

        self.conn.execute(
            """
            INSERT INTO tickets (
                id, content_hash, subject, body, customer, created_at, inserted_at, status,
                priority_level, classified_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ticket.to_db_tuple(),
        )
        self.conn.commit()

        cursor = self.conn.execute("SELECT * FROM tickets WHERE id = ?", ("t-103",))
        row = cursor.fetchone()
        self.assertIsNotNone(row)

        hydrated = Ticket.from_db_row(row)
        self.assertEqual(hydrated.id, ticket.id)
        self.assertEqual(hydrated.subject, ticket.subject)
        self.assertEqual(hydrated.body, ticket.body)
        self.assertEqual(hydrated.customer, ticket.customer)
        self.assertEqual(hydrated.created_at, ticket.created_at)
        self.assertEqual(hydrated.inserted_at, ticket.inserted_at)
        self.assertEqual(hydrated.content_hash, ticket.content_hash)
        self.assertEqual(hydrated.status, "PENDING")
        self.assertEqual(hydrated.priority_level, "HIGH")
        self.assertEqual(hydrated.metadata, {"client_version": "2.4.1"})

    def test_sqlite_content_hash_uniqueness(self):
        t1 = Ticket(
            id="uuid-1",
            subject="Same issue",
            body="Same content description",
            customer="CUST-9",
            created_at="2026-10-20T10:00:00Z",
        )
        t2 = Ticket(
            id="uuid-2",
            subject="Same issue",
            body="Same content description",
            customer="CUST-9",
            created_at="2026-10-20T10:05:00Z",
        )

        self.conn.execute(
            """
            INSERT INTO tickets (
                id, content_hash, subject, body, customer, created_at, inserted_at, status,
                priority_level, classified_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            t1.to_db_tuple(),
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO tickets (
                    id, content_hash, subject, body, customer, created_at, inserted_at, status,
                    priority_level, classified_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                t2.to_db_tuple(),
            )

    def test_rejected_record_sqlite_roundtrip(self):
        rejected = RejectedRecord(
            raw_record={"bad_row": True, "reason": "missing customer"},
            rejection_reason="Missing required field: customer",
        )

        cursor = self.conn.execute(
            """
            INSERT INTO rejected_tickets (raw_record, rejection_reason, rejected_at)
            VALUES (?, ?, ?)
            """,
            rejected.to_db_tuple(),
        )
        self.conn.commit()
        inserted_id = cursor.lastrowid

        cursor = self.conn.execute(
            "SELECT * FROM rejected_tickets WHERE id = ?", (inserted_id,)
        )
        row = cursor.fetchone()
        self.assertIsNotNone(row)

        hydrated = RejectedRecord.from_db_row(row)
        self.assertEqual(hydrated.id, inserted_id)
        self.assertEqual(hydrated.raw_record, {"bad_row": True, "reason": "missing customer"})
        self.assertEqual(hydrated.rejection_reason, "Missing required field: customer")
        self.assertTrue(hydrated.rejected_at != "")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()

