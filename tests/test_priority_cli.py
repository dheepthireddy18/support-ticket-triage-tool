"""Tests for the CLI's --resolve and --import-rules commands."""

import json

import pytest

from ticket_triage import ticket_triage_cli
from ticket_triage.db import get_db_connection
from ticket_triage.models import STATUS_RESOLVED

OUTAGE_ID = "7c1d3a52-95b4-4f0e-8a6b-2d9e4f7a1b03"


@pytest.fixture
def loaded_db(tmp_path, monkeypatch):
    """A database holding the prioritized sample tickets; returns its path."""
    from conftest import SAMPLE_FILE

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sample_tickets.json").write_text(SAMPLE_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(ticket_triage_cli, "DATA_DIR", data_dir)
    monkeypatch.setattr(ticket_triage_cli, "REPORTS_DIR", tmp_path / "reports")

    db = tmp_path / "t.db"
    assert ticket_triage_cli.main(["--input", "sample_tickets.json", "--once", "--db", str(db)]) == 0
    return db


def status_of(db, ticket_id):
    conn = get_db_connection(db)
    try:
        return conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0]
    finally:
        conn.close()


def test_resolve_prints_previous_level(loaded_db, capsys):
    code = ticket_triage_cli.main(["--db", str(loaded_db), "--resolve", OUTAGE_ID])

    assert code == 0
    assert capsys.readouterr().out.strip() == f"Resolved ticket {OUTAGE_ID} (was HIGH)."
    assert status_of(loaded_db, OUTAGE_ID) == STATUS_RESOLVED


def test_resolve_twice_still_succeeds(loaded_db, capsys):
    ticket_triage_cli.main(["--db", str(loaded_db), "--resolve", OUTAGE_ID])
    capsys.readouterr()

    code = ticket_triage_cli.main(["--db", str(loaded_db), "--resolve", OUTAGE_ID])

    assert code == 0
    assert "already resolved" in capsys.readouterr().out


def test_resolve_unknown_id_fails(loaded_db, capsys):
    code = ticket_triage_cli.main(["--db", str(loaded_db), "--resolve", "no-such-id"])

    assert code == 1
    assert "error: no ticket with id no-such-id" in capsys.readouterr().err


def test_resolve_cannot_be_combined_with_input(capsys):
    with pytest.raises(SystemExit) as exc:
        ticket_triage_cli.main(["--input", "sample_tickets.json", "--resolve", OUTAGE_ID])

    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_resolved_ticket_leaves_the_report(loaded_db, tmp_path):
    ticket_triage_cli.main(["--db", str(loaded_db), "--resolve", OUTAGE_ID])
    ticket_triage_cli.main(["--input", "sample_tickets.json", "--once", "--db", str(loaded_db)])

    report = (tmp_path / "reports" / "daily_summary.txt").read_text(encoding="utf-8")
    assert OUTAGE_ID not in report
    assert "Open tickets: 4" in report


def test_rerunning_same_file_does_not_duplicate_or_reject(loaded_db, capsys):
    ticket_triage_cli.main(["--input", "sample_tickets.json", "--once", "--db", str(loaded_db)])

    assert "Loaded 0 new ticket(s), rejected 3. Prioritized 0." in capsys.readouterr().out


def test_import_rules_from_data_folder(loaded_db, tmp_path, capsys):
    (tmp_path / "data" / "rules.json").write_text(
        json.dumps({"settings": {"escalate_after_hours": 6}}), encoding="utf-8"
    )

    code = ticket_triage_cli.main(["--db", str(loaded_db), "--import-rules", "rules.json"])

    assert code == 0
    assert "Priority rules updated" in capsys.readouterr().out


def test_import_rules_reports_bad_file(loaded_db, tmp_path, capsys):
    (tmp_path / "data" / "rules.json").write_text(
        json.dumps({"rules": {"keyword": {"weight": -2}}}), encoding="utf-8"
    )

    code = ticket_triage_cli.main(["--db", str(loaded_db), "--import-rules", "rules.json"])

    assert code == 2
    assert "could not import" in capsys.readouterr().err
