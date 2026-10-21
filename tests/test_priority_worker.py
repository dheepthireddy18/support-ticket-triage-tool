"""Tests for the background escalation worker and its CLI wiring."""

import threading
from datetime import timedelta

import pytest
from conftest import insert_ticket

from ticket_triage import ticket_triage_cli
from ticket_triage.db import get_db_connection
from ticket_triage.models import PRIORITY_LOW, PRIORITY_MEDIUM, STATUS_PROCESSED
from ticket_triage.priority.worker import EscalationWorker

DAY = timedelta(hours=24)


def add_overdue_low_ticket(db_path, make_ticket, fixed_now, ticket_id="a"):
    conn = get_db_connection(db_path)
    try:
        insert_ticket(conn, make_ticket(id=ticket_id))
        with conn:
            conn.execute(
                "UPDATE tickets SET priority_level = ?, classified_at = ?, status = ? WHERE id = ?",
                (PRIORITY_LOW, (fixed_now - 2 * DAY).isoformat(), STATUS_PROCESSED, ticket_id),
            )
    finally:
        conn.close()


def level_of(db_path, ticket_id="a"):
    conn = get_db_connection(db_path)
    try:
        return conn.execute(
            "SELECT priority_level FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_run_once_escalates_and_reports(db_path, make_ticket, fixed_now):
    add_overdue_low_ticket(db_path, make_ticket, fixed_now)
    seen = []
    worker = EscalationWorker(db_path, 60, DAY, clock=lambda: fixed_now, on_escalate=seen.append)

    assert worker.run_once() == ["a"]
    assert seen == [["a"]]
    assert level_of(db_path) == PRIORITY_MEDIUM


def test_worker_thread_escalates_then_stops(db_path, make_ticket, fixed_now):
    add_overdue_low_ticket(db_path, make_ticket, fixed_now)
    escalated = threading.Event()
    worker = EscalationWorker(
        db_path, 0.01, DAY, clock=lambda: fixed_now, on_escalate=lambda ids: escalated.set()
    )

    worker.start()
    try:
        assert escalated.wait(timeout=5), "worker never escalated the ticket"
        assert worker.is_running
    finally:
        worker.stop()

    assert not worker.is_running
    assert level_of(db_path) == PRIORITY_MEDIUM


def test_worker_keeps_running_after_a_failed_check(db_path, fixed_now):
    calls = []

    def flaky_clock():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return fixed_now

    worker = EscalationWorker(db_path, 0.01, DAY, clock=flaky_clock)
    worker.start()
    try:
        for _ in range(500):
            if len(calls) >= 3:
                break
            threading.Event().wait(0.01)
    finally:
        worker.stop()

    assert len(calls) >= 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"check_interval": 0, "escalate_after": DAY},
        {"check_interval": 1, "escalate_after": timedelta(0)},
    ],
)
def test_worker_rejects_bad_intervals(db_path, kwargs):
    with pytest.raises(ValueError):
        EscalationWorker(db_path, **kwargs)


def test_worker_rejects_in_memory_database():
    with pytest.raises(ValueError):
        EscalationWorker(":memory:", 1, DAY)


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def cli_dirs(tmp_path, monkeypatch):
    """Point the CLI's data/ and reports/ folders at a temp dir with the sample file."""
    from conftest import SAMPLE_FILE

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sample_tickets.json").write_text(SAMPLE_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(ticket_triage_cli, "DATA_DIR", data_dir)
    monkeypatch.setattr(ticket_triage_cli, "REPORTS_DIR", tmp_path / "reports")
    return tmp_path


def test_cli_once_does_not_start_worker(cli_dirs, monkeypatch):
    started = []
    monkeypatch.setattr(EscalationWorker, "start", lambda self: started.append(self))

    code = ticket_triage_cli.main(
        ["--input", "sample_tickets.json", "--once", "--db", str(cli_dirs / "t.db")]
    )

    assert code == 0
    assert started == []
    assert (cli_dirs / "reports" / "daily_summary.txt").is_file()


def test_cli_starts_worker_and_stops_on_ctrl_c(cli_dirs, monkeypatch, capsys):
    workers = []
    real_start = EscalationWorker.start

    def recording_start(self):
        workers.append(self)
        real_start(self)

    def press_ctrl_c():
        raise KeyboardInterrupt

    monkeypatch.setattr(EscalationWorker, "start", recording_start)
    monkeypatch.setattr(ticket_triage_cli, "wait_for_interrupt", press_ctrl_c)

    code = ticket_triage_cli.main(
        [
            "--input", "sample_tickets.json", "--db", str(cli_dirs / "t.db"),
            "--check-interval", "5", "--escalate-after", "2",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert len(workers) == 1
    assert workers[0].check_interval == 5
    assert workers[0].escalate_after == timedelta(hours=2)
    assert not workers[0].is_running
    assert "Escalation worker running (every 5s, escalate after 2h)" in out
    assert "Escalation worker stopped." in out


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_cli_rejects_bad_intervals(value):
    with pytest.raises(SystemExit):
        ticket_triage_cli.main(["--input", "x.json", "--check-interval", value])
