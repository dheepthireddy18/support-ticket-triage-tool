"""Integration test: run the whole tool on the sample ticket file."""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ticket_triage.db import init_db
from ticket_triage.demo import seed_demo_db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_FILE = PROJECT_ROOT / "data" / "sample_tickets.json"

# Ids of the five valid tickets in data/sample_tickets.json.
VALID_TICKET_IDS = [
    "e4eb2955-8d61-49a2-ac15-f016a366951a",
    "7c1d3a52-95b4-4f0e-8a6b-2d9e4f7a1b03",
    "b2f8e6d1-3c47-4a95-9e10-6a5d8c2f7e44",
    "0a9c4b7e-1d2f-4e83-b5a6-9f3e7d1c8b25",
    "d5e3f1a8-6b2c-4d79-8c04-1e7a9b3f6d52",
]


def run_tool(*args):
    """Run the tool the way a user would: as a separate process."""
    return subprocess.run(
        [sys.executable, "-m", "ticket_triage", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


# TODO: the sample-file test below uses the shared default data/tickets.db. The
# report lists only --top (5) tickets, so it finds all five sample ids only while
# that database holds nothing else; consider passing --db with a temporary file.
# TODO(if Natalie's escalation worker lands): if it blocks the CLI until Ctrl+C,
# every run_tool() call needs an opt-out flag (e.g. "--once") or the tests hang.
def test_full_tool_on_sample_file_writes_report():
    # The tool reads data/<name> and writes reports/<name>.
    report_path = PROJECT_ROOT / "reports" / "integration_test_report.txt"
    report_path.unlink(missing_ok=True)

    try:
        result = run_tool(
            "--input", SAMPLE_FILE.name, "--output", report_path.name
        )

        assert result.returncode == 0, result.stderr

        assert report_path.is_file()
        report = report_path.read_text(encoding="utf-8")
        assert report.strip()
    finally:
        report_path.unlink(missing_ok=True)

    # Every valid ticket from the sample file shows up in the report.
    for ticket_id in VALID_TICKET_IDS:
        assert ticket_id in report


def test_paths_are_rejected_file_names_only():
    result = run_tool("--input", "data/sample_tickets.json")

    assert result.returncode != 0
    assert "file name only" in result.stderr


def test_missing_input_file_exits_with_error():
    result = run_tool("--input", "does_not_exist.json")

    assert result.returncode == 2
    assert "not found" in result.stderr


def test_cli_reports_from_a_seeded_database(tmp_path):
    db_path = tmp_path / "demo.db"
    conn = init_db(db_path)
    seed_demo_db(conn, datetime.now(timezone.utc))
    conn.close()
    report_path = PROJECT_ROOT / "reports" / "integration_demo_report.txt"
    report_path.unlink(missing_ok=True)

    try:
        result = run_tool(
            "--db", str(db_path), "--output", report_path.name, "--top", "3"
        )

        assert result.returncode == 0, result.stderr
        report = report_path.read_text(encoding="utf-8")
    finally:
        report_path.unlink(missing_ok=True)

    # Header and counts come from the whole database.
    assert "Total tickets: 12" in report
    assert "Rejected records: 2" in report
    assert "HIGH: 6" in report
    assert "Unclassified" not in report
    # --top 3 lists the three oldest HIGH tickets; the 4th and 5th are left out.
    for ticket_id in ("demo-001", "demo-002", "demo-003"):
        assert ticket_id in report
    assert "demo-004" not in report
    assert "demo-005" not in report
