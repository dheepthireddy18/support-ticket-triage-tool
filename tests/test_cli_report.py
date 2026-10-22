"""Tests for the CLI's report wiring and its stand-in intake."""

import json
from pathlib import Path

import pytest

from ticket_triage import ticket_triage_cli as cli

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_tickets.json"


def make_record(ticket_id="t-1", **overrides):
    """A valid raw ticket record; override or add fields as needed."""
    record = {
        "id": ticket_id,
        "subject": "Cannot log in",
        "body": f"Body of {ticket_id}",
        "customer": "CUST-1",
        "created_at": "2026-10-19T08:30:00Z",
    }
    record.update(overrides)
    return record


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Point the CLI's data/ and reports/ folders at a temporary directory."""
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path / "reports")
    (tmp_path / "data").mkdir()
    return tmp_path


def write_input(workdir, records, name="tickets.json"):
    (workdir / "data" / name).write_text(json.dumps(records), encoding="utf-8")
    return name


def run_cli(workdir, *args):
    """Run main() against a temporary database; returns (exit code, report text)."""
    code = cli.main(["--db", str(workdir / "t.db"), *args])
    report_path = workdir / "reports" / "daily_summary.txt"
    text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    return code, text


class TestLoadTickets:
    def test_stores_valid_and_rejects_invalid_sample_records(self, conn):
        inserted, rejected = cli.load_tickets(conn, SAMPLE_FILE)

        assert (inserted, rejected) == (5, 3)
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 5
        reasons = [r[0] for r in conn.execute("SELECT rejection_reason FROM rejected_tickets")]
        assert any("customer" in r for r in reasons)
        assert any("invalid created_at" in r for r in reasons)
        assert any("duplicate id" in r for r in reasons)

    def test_stored_tickets_have_no_priority(self, conn, tmp_path):
        path = tmp_path / "in.json"
        path.write_text(json.dumps([make_record(priority_level="HIGH")]), encoding="utf-8")

        cli.load_tickets(conn, path)

        stored = conn.execute("SELECT priority_level FROM tickets").fetchone()
        assert stored["priority_level"] is None

    def test_importing_the_same_ticket_again_is_skipped(self, conn, tmp_path):
        path = tmp_path / "in.json"
        path.write_text(json.dumps([make_record()]), encoding="utf-8")

        assert cli.load_tickets(conn, path) == (1, 0)
        assert cli.load_tickets(conn, path) == (0, 0)
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1

    def test_same_content_under_a_new_id_is_rejected(self, conn, tmp_path):
        path = tmp_path / "in.json"
        records = [make_record("t-1"), make_record("t-2", body="Body of t-1")]
        path.write_text(json.dumps(records), encoding="utf-8")

        assert cli.load_tickets(conn, path) == (1, 1)

    def test_non_object_record_is_rejected(self, conn, tmp_path):
        path = tmp_path / "in.json"
        path.write_text(json.dumps(["not a ticket"]), encoding="utf-8")

        assert cli.load_tickets(conn, path) == (0, 1)


class TestMain:
    def test_writes_report_from_input(self, workdir, capsys):
        name = write_input(workdir, [make_record("t-1"), make_record("t-2", body="other")])

        code, text = run_cli(workdir, "--input", name)

        assert code == 0
        assert "t-1 |" in text and "t-2 |" in text
        assert "Unclassified: 2" in text  # no rules have run yet
        assert "Loaded 2 new ticket(s), rejected 0." in capsys.readouterr().out

    def test_report_without_input_uses_the_database(self, workdir):
        write_input(workdir, [make_record("t-1")])
        run_cli(workdir, "--input", "tickets.json")

        code, text = run_cli(workdir)  # nothing to load, report what is stored

        assert code == 0
        assert "t-1 |" in text

    def test_empty_database_gives_report_with_empty_states(self, workdir):
        code, text = run_cli(workdir)

        assert code == 0
        assert "No urgent tickets today" in text
        assert "Total tickets: 0" in text

    def test_top_limits_the_lists(self, workdir):
        records = [make_record(f"t-{i}", body=f"body {i}") for i in range(4)]
        name = write_input(workdir, records)

        _, text = run_cli(workdir, "--input", name, "--top", "2")

        assert text.count("| waiting") == 2  # only the "Waiting longest" list has rows
        assert "Total tickets: 4" in text

    def test_missing_input_file_exits_with_error(self, workdir, capsys):
        code, _ = run_cli(workdir, "--input", "nope.json")

        assert code == 2
        assert "not found" in capsys.readouterr().err

    def test_invalid_json_exits_with_error(self, workdir, capsys):
        (workdir / "data" / "bad.json").write_text("{not json", encoding="utf-8")

        code, _ = run_cli(workdir, "--input", "bad.json")

        assert code == 2
        assert "could not read" in capsys.readouterr().err


class TestTopOption:
    def test_default_is_five(self):
        assert cli.build_parser().parse_args([]).top == 5

    def test_zero_is_allowed(self):
        assert cli.build_parser().parse_args(["--top", "0"]).top == 0

    @pytest.mark.parametrize("value", ["-1", "abc", "1.5"])
    def test_invalid_values_are_rejected(self, value, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["--top", value])

        assert exc.value.code == 2
        assert "--top" in capsys.readouterr().err
