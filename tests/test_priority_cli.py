"""Tests for the CLI's priority stage: storing tickets, scoring them, and the rules file."""

import json
from pathlib import Path

import pytest

from ticket_triage import ticket_triage_cli
from ticket_triage.db import get_db_connection

PROJECT_DATA = Path(__file__).resolve().parents[1] / "data"
OUTAGE_ID = "7c1d3a52-95b4-4f0e-8a6b-2d9e4f7a1b03"
VALID_IDS = {
    "e4eb2955-8d61-49a2-ac15-f016a366951a",
    OUTAGE_ID,
    "b2f8e6d1-3c47-4a95-9e10-6a5d8c2f7e44",
    "0a9c4b7e-1d2f-4e83-b5a6-9f3e7d1c8b25",
    "d5e3f1a8-6b2c-4d79-8c04-1e7a9b3f6d52",
}


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A temp data/ folder with the sample tickets and the shipped rules file."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("sample_tickets.json", "priority_rules.json"):
        (data_dir / name).write_text((PROJECT_DATA / name).read_text(encoding="utf-8"))
    monkeypatch.setattr(ticket_triage_cli, "DATA_DIR", data_dir)
    monkeypatch.setattr(ticket_triage_cli, "REPORTS_DIR", tmp_path / "reports")
    return tmp_path


def run(workdir, *extra):
    return ticket_triage_cli.main(
        ["--input", "sample_tickets.json", "--db", str(workdir / "t.db"), *extra]
    )


def query(workdir, sql):
    conn = get_db_connection(workdir / "t.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_run_gives_every_valid_ticket_a_real_priority(workdir, capsys):
    assert run(workdir) == 0

    rows = query(workdir, "SELECT id, status, priority_level FROM tickets")
    assert {row["id"] for row in rows} == VALID_IDS
    assert {row["status"] for row in rows} == {"PROCESSED"}
    assert {row["priority_level"] for row in rows} <= {"HIGH", "MEDIUM", "LOW"}
    assert dict((row["id"], row["priority_level"]) for row in rows)[OUTAGE_ID] == "HIGH"

    report = (workdir / "reports" / "daily_summary.txt").read_text(encoding="utf-8")
    # The report lists the outage as one row: "<id> | <customer> | <subject> | HIGH | waiting ..."
    assert any(OUTAGE_ID in line and "| HIGH |" in line for line in report.splitlines())
    assert "UNRATED" not in report
    assert "Stored 5 new ticket(s), skipped 3." in capsys.readouterr().out


def test_each_rule_reason_is_saved(workdir):
    run(workdir)

    rows = query(
        workdir,
        f"SELECT rule_name FROM ticket_rule_results WHERE ticket_id = '{OUTAGE_ID}' ORDER BY id",
    )
    assert [row["rule_name"] for row in rows] == ["keyword", "repeat_contact", "waiting_time"]


def test_rerun_rescores_without_duplicating_tickets(workdir, capsys):
    run(workdir)
    capsys.readouterr()

    assert run(workdir) == 0

    out = capsys.readouterr().out
    assert "Stored 0 new ticket(s), skipped 3. 5 prioritized ticket(s)." in out
    assert query(workdir, "SELECT COUNT(*) AS n FROM tickets")[0]["n"] == 5
    # Each run adds every open ticket's reasons to the history: 5 tickets x 3 rules x 2 runs.
    assert query(workdir, "SELECT COUNT(*) AS n FROM ticket_rule_results")[0]["n"] == 30


def test_resolve_prints_previous_level_and_keeps_status(workdir, capsys):
    run(workdir)
    capsys.readouterr()

    code = ticket_triage_cli.main(["--db", str(workdir / "t.db"), "--resolve", OUTAGE_ID])

    assert code == 0
    assert capsys.readouterr().out.strip() == f"Resolved ticket {OUTAGE_ID} (was HIGH)."
    row = query(workdir, f"SELECT status, resolved_at FROM tickets WHERE id = '{OUTAGE_ID}'")[0]
    assert row["status"] == "PROCESSED"
    assert row["resolved_at"] is not None


def test_resolved_ticket_is_not_rescored_or_reported(workdir):
    run(workdir)
    ticket_triage_cli.main(["--db", str(workdir / "t.db"), "--resolve", OUTAGE_ID])

    run(workdir)

    rows = query(workdir, f"SELECT COUNT(*) AS n FROM ticket_rule_results WHERE ticket_id = '{OUTAGE_ID}'")
    assert rows[0]["n"] == 3  # Only the first run's reasons
    report = (workdir / "reports" / "daily_summary.txt").read_text(encoding="utf-8")
    assert OUTAGE_ID not in report
    assert "Total tickets: 4" in report  # The report covers open tickets only


def test_resolve_twice_still_succeeds(workdir, capsys):
    run(workdir)
    ticket_triage_cli.main(["--db", str(workdir / "t.db"), "--resolve", OUTAGE_ID])
    capsys.readouterr()

    code = ticket_triage_cli.main(["--db", str(workdir / "t.db"), "--resolve", OUTAGE_ID])

    assert code == 0
    assert "already resolved" in capsys.readouterr().out


def test_resolve_unknown_id_fails(workdir, capsys):
    run(workdir)
    capsys.readouterr()

    code = ticket_triage_cli.main(["--db", str(workdir / "t.db"), "--resolve", "no-such-id"])

    assert code == 1
    assert "error: no ticket with id no-such-id" in capsys.readouterr().err


def test_resolve_cannot_be_combined_with_input(capsys):
    with pytest.raises(SystemExit) as exc:
        ticket_triage_cli.main(["--input", "sample_tickets.json", "--resolve", OUTAGE_ID])

    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_rules_file_changes_the_result(workdir):
    # With HIGH at 1 point and above, every ticket comes out HIGH.
    (workdir / "data" / "priority_rules.json").write_text(
        json.dumps({"thresholds": {"high": 1, "medium": 0}}), encoding="utf-8"
    )

    run(workdir)

    assert {row[0] for row in query(workdir, "SELECT priority_level FROM tickets")} == {"HIGH"}


def test_invalid_rules_file_stops_before_any_work(workdir, capsys):
    (workdir / "data" / "priority_rules.json").write_text(
        json.dumps({"rules": {"keyword": {"weight": -2}}}), encoding="utf-8"
    )

    code = run(workdir)

    err = capsys.readouterr().err
    assert code == 2
    assert "invalid priority rules in" in err and "priority_rules.json" in err
    assert not (workdir / "t.db").exists()  # Nothing was stored
    assert not (workdir / "reports" / "daily_summary.txt").exists()


def test_missing_rules_file_falls_back_with_notice(workdir, capsys):
    (workdir / "data" / "priority_rules.json").unlink()

    assert run(workdir) == 0

    assert "using the built-in priority rules" in capsys.readouterr().err
    assert len(query(workdir, "SELECT id FROM tickets WHERE priority_level IS NOT NULL")) == 5


@pytest.mark.parametrize(
    "record",
    [
        {"id": "x", "subject": "s", "body": "b", "created_at": "2026-10-19T08:00:00Z"},  # no customer
        {"id": "x", "subject": "s", "body": "b", "customer": "C", "created_at": "yesterday"},
        "not a ticket",
    ],
)
def test_bad_records_are_skipped(workdir, record):
    (workdir / "data" / "bad.json").write_text(json.dumps([record]), encoding="utf-8")

    code = ticket_triage_cli.main(["--input", "bad.json", "--db", str(workdir / "t.db")])

    assert code == 0
    assert query(workdir, "SELECT COUNT(*) AS n FROM tickets")[0]["n"] == 0


def test_llm_rule_on_without_a_key_still_prioritizes(workdir, monkeypatch):
    """Turning the LLM rule on without credentials must not break a run."""
    from ticket_triage.priority import llm_rule

    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", *llm_rule.AZURE_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_rule, "load_dotenv_file", lambda: None)  # Never read the real .env
    (workdir / "data" / "priority_rules.json").write_text(
        json.dumps({"rules": {"llm_urgency": {"enabled": True}}}), encoding="utf-8"
    )

    assert run(workdir) == 0

    rows = query(
        workdir,
        "SELECT score, reason FROM ticket_rule_results WHERE rule_name = 'llm_urgency'",
    )
    assert len(rows) == 5
    assert {row["score"] for row in rows} == {None}
    assert {row["reason"] for row in rows} == {"skipped: no Azure or Anthropic API key settings found"}
    assert query(workdir, f"SELECT priority_level FROM tickets WHERE id = '{OUTAGE_ID}'")[0][0] == "HIGH"
