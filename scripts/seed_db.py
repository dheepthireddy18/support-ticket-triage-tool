#!/usr/bin/env python3
"""
Convenience seed script for the Support Ticket Triage Tool.
Seeds data/tickets.db from data/sample_tickets.json, verifying 5 valid tickets
inserted into `tickets` and 3 invalid records logged into `rejected_tickets`.

Usage:
    python scripts/seed_db.py
"""

from pathlib import Path
import sys

# Ensure src/ is importable
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ticket_triage.db import DEFAULT_DB_PATH, seed_db


def main() -> int:
    sample_file = PROJECT_ROOT / "data" / "sample_tickets.json"
    inserted, rejected = seed_db(
        db_path=DEFAULT_DB_PATH,
        sample_path=sample_file,
        reset=True,
    )
    print(
        f"Database successfully seeded at {DEFAULT_DB_PATH}:\n"
        f"  - {inserted} valid ticket(s) inserted into 'tickets'\n"
        f"  - {rejected} bad record(s) logged into 'rejected_tickets'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

