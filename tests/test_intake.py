"""
Pytest suite for data intake, schema validation, deduplication, and database routing.
Validates clean file ingestion (JSON & CSV), bad date handling, missing fields,
duplicate detection, metadata preservation, and audit table logging.
"""

import json
from pathlib import Path

import pytest

from ticket_triage.db import init_db
from ticket_triage.intake import (
    IngestResult,
    ingest_records,
    ingest_tickets_file,
    validate_record,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_JSON = PROJECT_ROOT / "data" / "sample_tickets.json"


@pytest.fixture
def conn(tmp_path):
    """Provide an isolated, schema-initialized SQLite connection for each test."""
    db_file = tmp_path / "test_intake.db"
    db_conn = init_db(db_file)
    yield db_conn
    db_conn.close()


def test_sample_tickets_json_ingestion(conn):
    """
    Verify data/sample_tickets.json:
    - 5 valid tickets inserted into `tickets`
    - 3 invalid records logged into `rejected_tickets` with explicit reasons
      (Ticket 6 missing customer, Ticket 7 bad date, Ticket 8 duplicate id)
    """
    result = ingest_tickets_file(conn, SAMPLE_JSON)
    assert result.inserted == 5
    assert result.rejected == 3

    # Check SQLite table row counts
    inserted_count = conn.execute("SELECT count(*) AS cnt FROM tickets;").fetchone()["cnt"]
    rejected_count = conn.execute("SELECT count(*) AS cnt FROM rejected_tickets;").fetchone()["cnt"]
    assert inserted_count == 5
    assert rejected_count == 3

    # Verify explicit rejection reasons in audit DLQ
    rejected_rows = conn.execute(
        "SELECT raw_record, rejection_reason FROM rejected_tickets ORDER BY id;"
    ).fetchall()

    # Ticket 6: Missing required field customer
    assert "Missing required field(s): customer" in rejected_rows[0]["rejection_reason"]

    # Ticket 7: Invalid date format
    assert "Invalid date format" in rejected_rows[1]["rejection_reason"]
    assert "yesterday afternoon" in rejected_rows[1]["rejection_reason"]

    # Ticket 8: Duplicate ID (reuses Ticket 1's UUID)
    assert "Duplicate ticket ID" in rejected_rows[2]["rejection_reason"]
    assert "e4eb2955-8d61-49a2-ac15-f016a366951a" in rejected_rows[2]["rejection_reason"]


def test_csv_ingestion_with_metadata(conn, tmp_path):
    """Verify CSV loading, unexpected column bundling into metadata, and empty field rejection."""
    csv_file = tmp_path / "valid_and_invalid.csv"
    csv_content = (
        "id,subject,body,customer,created_at,source,priority_tag\n"
        "csv-1,Network timeout,Cannot reach gateway,CUST-N,2026-10-22T08:00:00Z,CLI,P1\n"
        "csv-2,Missing body,,CUST-M,2026-10-22T08:30:00Z,Web,P3\n"
    )
    csv_file.write_text(csv_content, encoding="utf-8")

    result = ingest_tickets_file(conn, csv_file)
    assert result.inserted == 1
    assert result.rejected == 1

    # Verify valid ticket preserved unexpected columns in JSON metadata
    row = conn.execute("SELECT * FROM tickets WHERE id = 'csv-1';").fetchone()
    assert row is not None
    metadata = json.loads(row["metadata"])
    assert metadata["source"] == "CLI"
    assert metadata["priority_tag"] == "P1"

    # Verify rejected record for empty body in audit table
    rej = conn.execute("SELECT * FROM rejected_tickets;").fetchone()
    assert "Missing required field(s): body" in rej["rejection_reason"]


def test_in_batch_duplicate_id(conn):
    """Duplicate IDs submitted within the same batch should insert the first and reject the second."""
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
    result = ingest_records(conn, records)
    assert result.inserted == 1
    assert result.rejected == 1
    assert "Duplicate ticket ID in batch: dup-id-1" in result.rejected_records[0].rejection_reason


def test_in_database_duplicate_id(conn):
    """An ID that already exists in SQLite with different content must be rejected."""
    rec1 = [{
        "id": "db-dup-1",
        "subject": "Initial ticket",
        "body": "Original issue",
        "customer": "CUST-1",
        "created_at": "2026-10-20T10:00:00Z",
    }]
    res1 = ingest_records(conn, rec1)
    assert res1.inserted == 1

    # Second ingest with same ID but different issue content
    rec2 = [{
        "id": "db-dup-1",
        "subject": "New ticket with reused ID",
        "body": "Different issue",
        "customer": "CUST-2",
        "created_at": "2026-10-22T10:00:00Z",
    }]
    res2 = ingest_records(conn, rec2)
    assert res2.inserted == 0
    assert res2.rejected == 1
    assert "already exists in database: db-dup-1" in res2.rejected_records[0].rejection_reason


def test_content_hash_deduplication(conn):
    """Different UUIDs with identical customer, subject, and body in the same batch."""
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
    result = ingest_records(conn, records)
    assert result.inserted == 1
    assert result.rejected == 1
    assert "Duplicate content: identical ticket already submitted" in result.rejected_records[0].rejection_reason


def test_content_hash_cross_batch_deduplication(conn):
    """Duplicate content submitted in a subsequent batch under a new UUID."""
    rec1 = [{
        "id": "uuid-batch-1",
        "subject": "Payment failed",
        "body": "Card declined 402",
        "customer": "CUST-Y",
        "created_at": "2026-10-22T11:00:00Z",
    }]
    res1 = ingest_records(conn, rec1)
    assert res1.inserted == 1

    # Second batch with same content under new UUID
    rec2 = [{
        "id": "uuid-batch-2",
        "subject": "Payment failed",
        "body": "Card declined 402",
        "customer": "CUST-Y",
        "created_at": "2026-10-22T11:30:00Z",
    }]
    res2 = ingest_records(conn, rec2)
    assert res2.inserted == 0
    assert res2.rejected == 1
    assert "Duplicate content: identical ticket already exists in database" in res2.rejected_records[0].rejection_reason


def test_rerun_identical_tickets_does_not_duplicate_or_pollute_rejected_table(conn):
    """Re-ingesting identical tickets already in DB should skip them without error."""
    result1 = ingest_tickets_file(conn, SAMPLE_JSON)
    assert result1.inserted == 5
    assert result1.rejected == 3

    # Second ingestion of the exact same file
    result2 = ingest_tickets_file(conn, SAMPLE_JSON)
    assert result2.inserted == 0
    assert result2.rejected == 3  # The 3 bad records
    assert result2.skipped_existing == 5  # The 5 identical already-ingested tickets

    # Database total tickets should still be exactly 5
    total_valid = conn.execute("SELECT COUNT(*) AS cnt FROM tickets;").fetchone()["cnt"]
    assert total_valid == 5


def test_missing_multiple_fields(conn):
    """Missing mandatory fields are listed explicitly in the rejection reason."""
    bad_record = {
        "id": "bad-1",
        "body": "Only body is provided",
    }
    ticket, reason = validate_record(bad_record, set(), set(), conn)
    assert ticket is None
    assert "Missing required field(s):" in reason
    assert "created_at" in reason
    assert "customer" in reason
    assert "subject" in reason


@pytest.mark.parametrize(
    "bad_date",
    [
        "not-a-date",
        "yesterday afternoon",
        "2026/10/20",
        "2026-99-99",
        "10-20-2026",
    ],
)
def test_invalid_date_formats(conn, bad_date):
    """Any non-ISO-8601 UTC date string is rejected with an explicit error reason."""
    rec = {
        "id": f"date-{hash(bad_date)}",
        "subject": "Date test",
        "body": "Body",
        "customer": "Cust",
        "created_at": bad_date,
    }
    ticket, reason = validate_record(rec, set(), set(), conn)
    assert ticket is None
    assert "Invalid date format" in reason
    assert bad_date in reason
