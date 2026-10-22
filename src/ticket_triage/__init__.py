"""Support Ticket Triage Tool package."""

from ticket_triage.db import DEFAULT_DB_PATH, get_db_connection, init_db
from ticket_triage.intake import (
    IngestResult,
    ingest_records,
    ingest_tickets_file,
    validate_record,
)
from ticket_triage.models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    STATUS_PENDING,
    STATUS_PROCESSED,
    STATUS_PROCESSING,
    RejectedRecord,
    Ticket,
    compute_content_hash,
)
from ticket_triage.readers import (
    CorruptedFileError,
    EmptyFileError,
    FileReadError,
    UnsupportedFormatError,
    bundle_metadata,
    read_csv_tickets,
    read_json_tickets,
    read_tickets_file,
)

__all__ = [
    "CorruptedFileError",
    "DEFAULT_DB_PATH",
    "EmptyFileError",
    "FileReadError",
    "IngestResult",
    "PRIORITY_HIGH",
    "PRIORITY_LOW",
    "PRIORITY_MEDIUM",
    "RejectedRecord",
    "STATUS_PENDING",
    "STATUS_PROCESSED",
    "STATUS_PROCESSING",
    "Ticket",
    "UnsupportedFormatError",
    "bundle_metadata",
    "compute_content_hash",
    "get_db_connection",
    "ingest_records",
    "ingest_tickets_file",
    "init_db",
    "read_csv_tickets",
    "read_json_tickets",
    "read_tickets_file",
    "validate_record",
]
