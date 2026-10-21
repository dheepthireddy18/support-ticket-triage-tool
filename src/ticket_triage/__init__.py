"""Support Ticket Triage Tool package."""

from ticket_triage.db import DEFAULT_DB_PATH, get_db_connection, init_db
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

__all__ = [
    "DEFAULT_DB_PATH",
    "PRIORITY_HIGH",
    "PRIORITY_LOW",
    "PRIORITY_MEDIUM",
    "RejectedRecord",
    "STATUS_PENDING",
    "STATUS_PROCESSED",
    "STATUS_PROCESSING",
    "Ticket",
    "compute_content_hash",
    "get_db_connection",
    "init_db",
]
