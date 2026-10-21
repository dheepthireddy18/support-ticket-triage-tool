"""Tests for the CLI's --resolve command and --rules file handling."""

import json

import pytest

from ticket_triage import ticket_triage_cli
from ticket_triage.db import get_db_connection
from ticket_triage.models import STATUS_RESOLVED

OUTAGE_ID = "7c1d3a52-95b4-4f0e-8a6b-2d9e4f7a1b03"


@pytest.fixture
def loaded_db(tmp_path, monkeypatch):
    """A database holding the prioritized sample tickets; returns its path."""
    from conftest import RULES_FILE, SAMPLE_FILE

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sample_tickets.json").write_text(SAMPLE_FILE.read_text(encoding="utf-8"))
    (data_dir / "priority_rules.json").write_text(RULES_FILE.read_text(encoding="utf-8"))
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


def run_once(db, *extra):
    return ticket_triage_cli.main(["--input", "sample_tickets.json", "--once", "--db", str(db), *extra])


def levels(db):
    conn = get_db_connection(db)
    try:
        return dict(conn.execute("SELECT id, priority_level FROM tickets").fetchall())
    finally:
        conn.close()


def test_named_rules_file_is_used(tmp_path, monkeypatch):
    from conftest import SAMPLE_FILE

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sample_tickets.json").write_text(SAMPLE_FILE.read_text(encoding="utf-8"))
    # Everything at least 1 point is HIGH: proves this file, not the default, was read.
    (data_dir / "strict.json").write_text(
        json.dumps({"thresholds": {"high": 1, "medium": 0}}), encoding="utf-8"
    )
    monkeypatch.setattr(ticket_triage_cli, "DATA_DIR", data_dir)
    monkeypatch.setattr(ticket_triage_cli, "REPORTS_DIR", tmp_path / "reports")

    assert run_once(tmp_path / "t.db", "--rules", "strict.json") == 0
    assert set(levels(tmp_path / "t.db").values()) == {"HIGH"}


def test_invalid_rules_file_stops_before_any_work(loaded_db, tmp_path, capsys):
    (tmp_path / "data" / "priority_rules.json").write_text(
        json.dumps({"rules": {"keyword": {"weight": -2}}}), encoding="utf-8"
    )
    capsys.readouterr()

    code = ticket_triage_cli.main(["--input", "sample_tickets.json", "--db", str(loaded_db)])

    err = capsys.readouterr().err
    assert code == 2
    assert "invalid priority rules in" in err
    assert "priority_rules.json" in err


def test_bad_worker_setting_fails_cleanly_before_any_work(loaded_db, tmp_path, capsys):
    (tmp_path / "data" / "priority_rules.json").write_text(
        json.dumps({"worker": {"escalate_after_hours": "abc"}}), encoding="utf-8"
    )
    capsys.readouterr()

    # No --once: a bad value must not get as far as starting the worker.
    code = ticket_triage_cli.main(["--input", "sample_tickets.json", "--db", str(loaded_db)])

    assert code == 2
    assert "worker.escalate_after_hours must be a number" in capsys.readouterr().err


def test_missing_named_rules_file_is_an_error(loaded_db, capsys):
    code = run_once(loaded_db, "--rules", "nope.json")

    assert code == 2
    assert "rules file not found" in capsys.readouterr().err


def test_missing_default_rules_file_falls_back_with_notice(loaded_db, tmp_path, capsys):
    (tmp_path / "data" / "priority_rules.json").unlink()
    capsys.readouterr()

    code = run_once(loaded_db)

    assert code == 0
    assert "using the built-in priority rules" in capsys.readouterr().err


def test_rules_file_must_be_a_name_not_a_path():
    with pytest.raises(SystemExit):
        ticket_triage_cli.main(["--input", "sample_tickets.json", "--rules", "data/priority_rules.json"])


def test_import_rules_flag_is_gone(capsys):
    with pytest.raises(SystemExit):
        ticket_triage_cli.main(["--import-rules", "rules.json"])
