"""
Ticket validation and ingestion engine.
Validates raw ticket records, performs batch and database deduplication,
inserts valid tickets into the `tickets` table, and logs bad records into `rejected_tickets`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ticket_triage.models import RejectedRecord, Ticket, compute_content_hash
from ticket_triage.readers import read_tickets_file

REQUIRED_FIELDS = ("id", "subject", "body", "customer", "created_at")


@dataclass
class IngestResult:
    """Summary of tickets ingested and rejected during an intake run."""

    inserted: int = 0
    rejected: int = 0
    skipped_existing: int = 0
    valid_tickets: list[Ticket] = field(default_factory=list)
    rejected_records: list[RejectedRecord] = field(default_factory=list)


def validate_record(
    record: dict[str, Any],
    seen_ids: set[str],
    seen_hashes: set[str],
    conn: sqlite3.Connection,
) -> tuple[Ticket | None, str | None]:
    """
    Validate a single raw ticket dictionary.

    Checks:
    1. Mandatory fields exist and are non-empty.
    2. created_at is a valid ISO-8601 timestamp.
    3. Ticket ID is not duplicated in the current batch.
    4. Ticket ID does not already exist in SQLite.
    5. Content hash is not duplicated in the current batch.
    6. Content hash does not already exist in SQLite.

    :return: (Ticket, None) if valid, or (None, rejection_reason) if invalid.
    """
    if not isinstance(record, dict):
        return None, f"Invalid record format: expected dict, got {type(record).__name__}"

    # 1. Required fields check
    missing = [
        f for f in REQUIRED_FIELDS
        if not record.get(f) or not str(record.get(f)).strip()
    ]
    if missing:
        return None, f"Missing required field(s): {', '.join(sorted(missing))}"

    # 2. Date parsing check
    created_at_raw = str(record["created_at"]).strip()
    try:
        normalized_dt = created_at_raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized_dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None, f"Invalid date format for created_at: '{created_at_raw}' (must be ISO-8601 UTC)"

    ticket_id = str(record["id"]).strip()

    # Compute content hash
    subject_str = str(record["subject"]).strip()
    body_str = str(record["body"]).strip()
    customer_str = str(record["customer"]).strip()
    content_hash = compute_content_hash(customer_str, subject_str, body_str)

    # 3. In-batch duplicate ID check
    if ticket_id in seen_ids:
        return None, f"Duplicate ticket ID in batch: {ticket_id}"

    # 4. In-database duplicate ID check
    existing_id = conn.execute(
        "SELECT id, content_hash FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if existing_id is not None:
        db_hash = existing_id["content_hash"] if hasattr(existing_id, "keys") else existing_id[1]
        if db_hash == content_hash:
            # Identical ticket already ingested in a previous run; skip cleanly
            return None, None
        return None, f"Duplicate ticket ID already exists in database: {ticket_id}"

    # 5. In-batch duplicate content hash check
    if content_hash in seen_hashes:
        return None, "Duplicate content: identical ticket already submitted in this batch"

    # 6. In-database duplicate content hash check
    existing_hash = conn.execute(
        "SELECT id FROM tickets WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if existing_hash is not None:
        return None, "Duplicate content: identical ticket already exists in database"

    # Record is valid
    metadata = dict(record.get("metadata", {}))
    ticket = Ticket(
        id=ticket_id,
        subject=subject_str,
        body=body_str,
        customer=customer_str,
        created_at=created_at_raw,
        content_hash=content_hash,
        status="PENDING",
        metadata=metadata,
    )

    seen_ids.add(ticket_id)
    seen_hashes.add(content_hash)
    return ticket, None


def ingest_records(
    conn: sqlite3.Connection, records: list[dict[str, Any]]
) -> IngestResult:
    """
    Validate records, insert valid tickets into `tickets`, and log invalid
    records into `rejected_tickets`.

    :param conn: SQLite database connection.
    :param records: List of record dictionaries (e.g. from read_tickets_file).
    :return: IngestResult with counts and created objects.
    """
    result = IngestResult()
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()

    for record in records:
        ticket, reason = validate_record(record, seen_ids, seen_hashes, conn)

        if ticket is not None:
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
                result.inserted += 1
                result.valid_tickets.append(ticket)
            except sqlite3.IntegrityError as exc:
                # Fallback for unexpected concurrency integrity conflicts
                reason = f"Database constraint violation: {exc}"
                ticket = None

        if ticket is None and reason is not None:
            rejected = RejectedRecord(raw_record=record, rejection_reason=reason)
            with conn:
                conn.execute(
                    """
                    INSERT INTO rejected_tickets (raw_record, rejection_reason, rejected_at)
                    VALUES (?, ?, ?)
                    """,
                    rejected.to_db_tuple(),
                )
            result.rejected += 1
            result.rejected_records.append(rejected)
        elif ticket is None and reason is None:
            result.skipped_existing += 1

    return result


def ingest_tickets_file(
    conn: sqlite3.Connection, file_path: Path | str
) -> IngestResult:
    """
    Read ticket records from a JSON or CSV file, validate each record,
    and persist valid and rejected records into SQLite.

    :param conn: Active SQLite connection.
    :param file_path: Path to the ticket data file (.json or .csv).
    :return: IngestResult summarizing counts and records.
    """
    records = read_tickets_file(file_path)
    return ingest_records(conn, records)

