"""Integration test: run the whole tool on the sample ticket file."""

import subprocess
import sys
from pathlib import Path

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


def test_full_tool_on_sample_file_writes_report(tmp_path):
    report_path = tmp_path / "reports" / "daily_summary.txt"

    # Run the tool the way a user would: as a separate process.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ticket_triage",
            "--input",
            str(SAMPLE_FILE),
            "--output",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    assert result.returncode == 0, result.stderr

    # The report file was created (including its missing parent folder).
    assert report_path.is_file()
    report = report_path.read_text(encoding="utf-8")
    assert report.strip()

    # Every valid ticket from the sample file shows up in the report.
    for ticket_id in VALID_TICKET_IDS:
        assert ticket_id in report
