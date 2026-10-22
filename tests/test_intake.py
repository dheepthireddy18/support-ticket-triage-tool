"""
Unit tests for validation engine, deduplication logic, and database routing.
Tests sample file ingestion, bad record detection, duplicate rejection, and CSV intake.
Compatible with both `pytest` and Python's built-in `unittest`.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ticket_triage.db import init_db
from ticket_triage.intake import (
    IngestResult,
    ingest_records,
    ingest_tickets_file,
    validate_record,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_JSON = PROJECT_ROOT / "data" / "sample_tickets.json"


class TestIntakeValidation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_intake.db"
        self.conn = init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_sample_tickets_json_ingestion(self):
        """
        data/sample_tickets.json contains 5 valid tickets and 3 invalid tickets:
        - Ticket 6: missing customer
        - Ticket 7: bad date ('yesterday afternoon')
        - Ticket 8: duplicate id (reuses Ticket 1's ID)
        """
        result = ingest_tickets_file(self.conn, SAMPLE_JSON)
        self.assertEqual(result.inserted, 5)
        self.assertEqual(result.rejected, 3)

        # Check SQLite counts
        inserted_count = self.conn.execute("SELECT count(*) AS cnt FROM tickets;").fetchone()["cnt"]
        rejected_count = self.conn.execute("SELECT count(*) AS cnt FROM rejected_tickets;").fetchone()["cnt"]
        self.assertEqual(inserted_count, 5)
        self.assertEqual(rejected_count, 3)

        # Verify rejection reasons in audit table
        rejected_rows = self.conn.execute(
            "SELECT raw_record, rejection_reason FROM rejected_tickets ORDER BY id;"
        ).fetchall()

        # Ticket 6: Missing customer
        self.assertIn("Missing required field(s): customer", rejected_rows[0]["rejection_reason"])

        # Ticket 7: Invalid date format
        self.assertIn("Invalid date format", rejected_rows[1]["rejection_reason"])
        self.assertIn("yesterday afternoon", rejected_rows[1]["rejection_reason"])

        # Ticket 8: Duplicate ID
        self.assertIn("Duplicate ticket ID", rejected_rows[2]["rejection_reason"])
        self.assertIn("e4eb2955-8d61-49a2-ac15-f016a366951a", rejected_rows[2]["rejection_reason"])

    def test_csv_ingestion_with_metadata(self):
        csv_file = Path(self.temp_dir.name) / "valid_and_invalid.csv"
        csv_content = (
            "id,subject,body,customer,created_at,source,priority_tag\n"
            "csv-1,Network timeout,Cannot reach gateway,CUST-N,2026-10-22T08:00:00Z,CLI,P1\n"
            "csv-2,Missing body,,CUST-M,2026-10-22T08:30:00Z,Web,P3\n"
        )
        csv_file.write_text(csv_content, encoding="utf-8")

        result = ingest_tickets_file(self.conn, csv_file)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.rejected, 1)

        # Verify valid ticket preserved metadata
        row = self.conn.execute("SELECT * FROM tickets WHERE id = 'csv-1';").fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["source"], "CLI")
        self.assertEqual(metadata["priority_tag"], "P1")

        # Verify rejected record for empty body
        rej = self.conn.execute("SELECT * FROM rejected_tickets;").fetchone()
        self.assertIn("Missing required field(s): body", rej["rejection_reason"])

    def test_in_batch_duplicate_id(self):
        records = [
            {
                "id": "dup-id-1",
                "subject": "First",
                "body": "Body one",
                "customer": "CUST-1",
                "created_at": "2026-10-22T09:00:00Z",
            },
            {
                "id": "dup-id-1",
                "subject": "Second",
                "body": "Body two",
                "customer": "CUST-2",
                "created_at": "2026-10-22T09:05:00Z",
            },
        ]
        result = ingest_records(self.conn, records)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.rejected, 1)
        self.assertIn("Duplicate ticket ID in batch: dup-id-1", result.rejected_records[0].rejection_reason)

    def test_in_database_duplicate_id(self):
        rec1 = [{
            "id": "db-dup-1",
            "subject": "Initial ticket",
            "body": "Original issue",
            "customer": "CUST-1",
            "created_at": "2026-10-20T10:00:00Z",
        }]
        res1 = ingest_records(self.conn, rec1)
        self.assertEqual(res1.inserted, 1)

        # Second ingest with same ID but different content
        rec2 = [{
            "id": "db-dup-1",
            "subject": "New ticket with reused ID",
            "body": "Different issue",
            "customer": "CUST-2",
            "created_at": "2026-10-22T10:00:00Z",
        }]
        res2 = ingest_records(self.conn, rec2)
        self.assertEqual(res2.inserted, 0)
        self.assertEqual(res2.rejected, 1)
        self.assertIn("already exists in database: db-dup-1", res2.rejected_records[0].rejection_reason)

    def test_content_hash_deduplication(self):
        """Different UUIDs with identical customer, subject, and body."""
        records = [
            {
                "id": "uuid-first",
                "subject": "Locked out",
                "body": "Cannot login to portal",
                "customer": "CUST-X",
                "created_at": "2026-10-22T10:00:00Z",
            },
            {
                "id": "uuid-second",
                "subject": "Locked out",
                "body": "Cannot login to portal",
                "customer": "CUST-X",
                "created_at": "2026-10-22T10:02:00Z",
            },
        ]
        result = ingest_records(self.conn, records)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.rejected, 1)
        self.assertIn("Duplicate content: identical ticket already submitted", result.rejected_records[0].rejection_reason)

    def test_content_hash_cross_batch_deduplication(self):
        rec1 = [{
            "id": "uuid-batch-1",
            "subject": "Payment failed",
            "body": "Card declined 402",
            "customer": "CUST-Y",
            "created_at": "2026-10-22T11:00:00Z",
        }]
        res1 = ingest_records(self.conn, rec1)
        self.assertEqual(res1.inserted, 1)

        # Second batch with same content under new UUID
        rec2 = [{
            "id": "uuid-batch-2",
            "subject": "Payment failed",
            "body": "Card declined 402",
            "customer": "CUST-Y",
            "created_at": "2026-10-22T11:30:00Z",
        }]
        res2 = ingest_records(self.conn, rec2)
        self.assertEqual(res2.inserted, 0)
        self.assertEqual(res2.rejected, 1)
        self.assertIn("Duplicate content: identical ticket already exists in database", res2.rejected_records[0].rejection_reason)

    def test_missing_multiple_fields(self):
        bad_record = {
            "id": "bad-1",
            "body": "Only body is provided",
        }
        ticket, reason = validate_record(bad_record, set(), set(), self.conn)
        self.assertIsNone(ticket)
        self.assertIn("Missing required field(s):", reason)
        self.assertIn("created_at", reason)
        self.assertIn("customer", reason)
        self.assertIn("subject", reason)

    def test_invalid_date_formats(self):
        for bad_date in ("not-a-date", "yesterday afternoon", "2026/10/20", "2026-99-99"):
            rec = {
                "id": f"date-{bad_date[:4]}",
                "subject": "Date test",
                "body": "Body",
                "customer": "Cust",
                "created_at": bad_date,
            }
            ticket, reason = validate_record(rec, set(), set(), self.conn)
            self.assertIsNone(ticket)
            self.assertIn("Invalid date format", reason)


if __name__ == "__main__":
    unittest.main()

