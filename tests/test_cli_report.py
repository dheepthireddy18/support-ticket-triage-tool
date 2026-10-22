"""Tests for the CLI's report wiring: --input, --top, and prioritizing before reporting."""

import json

import pytest

from ticket_triage import ticket_triage_cli as cli
from ticket_triage.db import get_db_connection
from ticket_triage.models import Ticket

OUTAGE_SUBJECT = "URGENT: production outage"  # the keyword rule makes this HIGH


def make_record(ticket_id="t-1", **overrides):
    """A valid raw ticket record; override or add fields as needed."""
    record = {
        "id": ticket_id,
        "subject": "Cannot log in",
        "body": f"Body of {ticket_id}",
        "customer": f"CUST-{ticket_id}",
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


def section(text, title):
    """The lines of one report section: after its underline, up to the next blank line."""
    lines = text.splitlines()
    start = lines.index(title) + 2
    end = lines.index("", start) if "" in lines[start:] else len(lines)
    return lines[start:end]


class TestReportFromInput:
    def test_writes_a_report_with_real_priorities(self, workdir, capsys):
        name = write_input(
            workdir,
            [make_record("t-1", subject=OUTAGE_SUBJECT), make_record("t-2", body="other")],
        )

        code, text = run_cli(workdir, "--input", name)

        assert code == 0
        assert "Total tickets: 2" in text
        assert "Unclassified" not in text  # every ticket was scored before reporting
        assert any("t-1 |" in row for row in section(text, "Needs attention first"))
        out = capsys.readouterr().out
        assert "Stored 2 new ticket(s), skipped 0. 2 prioritized ticket(s)." in out
        assert "Report saved to" in out

    def test_rejected_records_are_not_in_the_ticket_total(self, workdir):
        name = write_input(workdir, [make_record("t-1"), {"id": "bad", "subject": "no customer"}])

        _, text = run_cli(workdir, "--input", name)

        assert "Total tickets: 1" in text

    def test_missing_input_file_exits_with_error(self, workdir, capsys):
        code, _ = run_cli(workdir, "--input", "nope.json")

        assert code == 2
        assert "not found" in capsys.readouterr().err

    def test_invalid_json_exits_with_error(self, workdir, capsys):
        (workdir / "data" / "bad.json").write_text("{not json", encoding="utf-8")

        code, _ = run_cli(workdir, "--input", "bad.json")

        assert code == 2
        assert "could not read" in capsys.readouterr().err


class TestReportWithoutInput:
    def test_reports_what_is_already_in_the_database(self, workdir, capsys):
        name = write_input(workdir, [make_record("t-1")])
        run_cli(workdir, "--input", name)
        capsys.readouterr()

        code, text = run_cli(workdir)

        assert code == 0
        assert "t-1 |" in text
        out = capsys.readouterr().out
        assert "Stored" not in out  # nothing was loaded
        assert "1 prioritized ticket(s)." in out

    def test_still_scores_tickets_that_are_waiting_for_a_priority(self, workdir):
        run_cli(workdir)  # creates the empty database
        conn = get_db_connection(workdir / "t.db")
        ticket = Ticket(**make_record("t-1", subject=OUTAGE_SUBJECT))
        with conn:
            conn.execute(
                "INSERT INTO tickets (id, content_hash, subject, body, customer, created_at, "
                "inserted_at, status, priority_level, classified_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ticket.to_db_tuple(),
            )
        conn.close()

        _, text = run_cli(workdir)  # no --input, but t-1 is still PENDING

        assert any("t-1 |" in row for row in section(text, "Needs attention first"))
        assert "Unclassified" not in text

    def test_empty_database_gives_a_report_with_empty_states(self, workdir):
        code, text = run_cli(workdir)

        assert code == 0
        assert "No urgent tickets today" in text
        assert "No tickets to show" in text
        assert "Total tickets: 0" in text


class TestTopOption:
    def test_top_limits_both_lists(self, workdir):
        records = [make_record(f"t-{i}", subject=OUTAGE_SUBJECT, body=f"body {i}") for i in range(4)]
        name = write_input(workdir, records)

        _, text = run_cli(workdir, "--input", name, "--top", "2")

        assert len(section(text, "Needs attention first")) == 2
        assert len(section(text, "Waiting longest")) == 2
        assert "Total tickets: 4" in text  # totals are not limited

    def test_default_is_five(self):
        assert cli.build_parser().parse_args([]).top == 5

    def test_one_is_allowed(self):
        assert cli.build_parser().parse_args(["--top", "1"]).top == 1

    @pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
    def test_invalid_values_are_rejected(self, value, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["--top", value])

        assert exc.value.code == 2
        assert "--top" in capsys.readouterr().err
