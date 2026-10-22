"""
Domain models for the Support Ticket Triage Tool.

Defines the Ticket domain object and RejectedRecord audit object, including
serialization to/from dictionaries and SQLite rows.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

# Canonical workflow statuses
STATUS_PENDING = "PENDING"
STATUS_PROCESSING = "PROCESSING"
STATUS_PROCESSED = "PROCESSED"

# Canonical priority levels
PRIORITY_HIGH = "HIGH"
PRIORITY_MEDIUM = "MEDIUM"
PRIORITY_LOW = "LOW"


def compute_content_hash(customer: str, subject: str, body: str) -> str:
    """Compute a deterministic SHA-256 hash of normalized ticket content."""
    normalized = f"{customer.lower().strip()}|{subject.strip()}|{body.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class Ticket:
    """Represents a validated, actionable support ticket."""

    id: str
    subject: str
    body: str
    customer: str
    created_at: str  # Source timestamp from CSV/JSON input (ISO-8601 UTC)
    inserted_at: str = ""  # Timestamp when record was ingested into DB
    content_hash: str = ""
    status: str = "PENDING"  # PENDING, PROCESSING, PROCESSED
    priority_level: str | None = None  # HIGH, MEDIUM, LOW
    classified_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    resolved_at: str | None = None  # Set when support closes the ticket; independent of status

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = compute_content_hash(
                self.customer, self.subject, self.body
            )
        if not self.inserted_at:
            self.inserted_at = datetime.now(timezone.utc).isoformat()

    @property
    def created_at_datetime(self) -> datetime:
        """Parse source created_at into a timezone-aware datetime object."""
        normalized = self.created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @property
    def inserted_at_datetime(self) -> datetime:
        """Parse inserted_at into a timezone-aware datetime object."""
        normalized = self.inserted_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def to_dict(self) -> dict[str, Any]:
        """Convert Ticket instance to a standard dictionary."""
        return {
            "id": self.id,
            "content_hash": self.content_hash,
            "subject": self.subject,
            "body": self.body,
            "customer": self.customer,
            "created_at": self.created_at,
            "inserted_at": self.inserted_at,
            "status": self.status,
            "priority_level": self.priority_level,
            "classified_at": self.classified_at,
            "metadata": dict(self.metadata),
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ticket:
        """
        Instantiate a Ticket from a dictionary.
        Any unexpected fields outside the core schema are preserved in metadata.
        """
        known_keys = {
            "id",
            "subject",
            "body",
            "customer",
            "created_at",
            "inserted_at",
            "content_hash",
            "status",
            "priority_level",
            "classified_at",
            "metadata",
            "resolved_at",
        }
        metadata = dict(data.get("metadata", {}))
        for key, value in data.items():
            if key not in known_keys:
                metadata[key] = value

        return cls(
            id=str(data["id"]),
            subject=str(data["subject"]),
            body=str(data["body"]),
            customer=str(data["customer"]),
            created_at=str(data["created_at"]),
            inserted_at=str(data.get("inserted_at", "")),
            content_hash=str(data.get("content_hash", "")),
            status=str(data.get("status", "PENDING")),
            priority_level=data.get("priority_level"),
            classified_at=data.get("classified_at"),
            metadata=metadata,
            resolved_at=data.get("resolved_at"),
        )

    @property
    def is_resolved(self) -> bool:
        """True once support has closed the ticket (`status` tracks processing only)."""
        return self.resolved_at is not None

    def to_db_tuple(self) -> tuple[Any, ...]:
        """
        Return values tuple matching columns in schema.sql:
        (id, content_hash, subject, body, customer, created_at, inserted_at,
         status, priority_level, classified_at, metadata)
        """
        metadata_json = json.dumps(self.metadata) if self.metadata else None
        return (
            self.id,
            self.content_hash,
            self.subject,
            self.body,
            self.customer,
            self.created_at,
            self.inserted_at,
            self.status,
            self.priority_level,
            self.classified_at,
            metadata_json,
        )

    @classmethod
    def from_db_row(cls, row: sqlite3.Row | Mapping[str, Any]) -> Ticket:
        """Construct a Ticket instance from an SQLite database row."""
        raw_metadata = row["metadata"] if "metadata" in row.keys() else None
        metadata: dict[str, Any] = {}
        if raw_metadata:
            if isinstance(raw_metadata, str):
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    metadata = {"_raw_metadata": raw_metadata}
            elif isinstance(raw_metadata, dict):
                metadata = raw_metadata

        content_hash = row["content_hash"] if "content_hash" in row.keys() else ""
        inserted_at = row["inserted_at"] if "inserted_at" in row.keys() else ""

        return cls(
            id=str(row["id"]),
            subject=str(row["subject"]),
            body=str(row["body"]),
            customer=str(row["customer"]),
            created_at=str(row["created_at"]),
            inserted_at=str(inserted_at),
            content_hash=str(content_hash) if content_hash else "",
            status=str(row["status"]) if "status" in row.keys() else "PENDING",
            priority_level=row["priority_level"]
            if "priority_level" in row.keys()
            else None,
            classified_at=row["classified_at"]
            if "classified_at" in row.keys()
            else None,
            metadata=metadata,
            resolved_at=row["resolved_at"] if "resolved_at" in row.keys() else None,
        )


@dataclass
class RejectedRecord:
    """Represents a raw record that failed validation and was routed to the audit table."""

    raw_record: dict[str, Any] | str
    rejection_reason: str
    rejected_at: str = field(default="")
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.rejected_at:
            self.rejected_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "raw_record": self.raw_record,
            "rejection_reason": self.rejection_reason,
            "rejected_at": self.rejected_at,
        }

    def to_db_tuple(self) -> tuple[str, str, str]:
        """Return values tuple matching: (raw_record, rejection_reason, rejected_at)."""
        raw_str = (
            json.dumps(self.raw_record)
            if isinstance(self.raw_record, dict)
            else str(self.raw_record)
        )
        return (raw_str, self.rejection_reason, self.rejected_at)

    @classmethod
    def from_db_row(cls, row: sqlite3.Row | Mapping[str, Any]) -> RejectedRecord:
        """Construct a RejectedRecord from an SQLite row."""
        raw_val = row["raw_record"]
        raw_record: dict[str, Any] | str = raw_val
        if isinstance(raw_val, str):
            try:
                raw_record = json.loads(raw_val)
            except json.JSONDecodeError:
                raw_record = raw_val

        return cls(
            id=int(row["id"]) if "id" in row.keys() and row["id"] is not None else None,
            raw_record=raw_record,
            rejection_reason=str(row["rejection_reason"]),
            rejected_at=str(row["rejected_at"]),
        )
