"""Tests for the demo database seeder."""

from datetime import datetime, timedelta, timezone

from ticket_triage.demo import DEMO_TICKETS, main, seed_demo_db
from ticket_triage.db import get_db_connection
from ticket_triage.report import build_report_data

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def test_seeded_database_has_every_report_case(conn):
    seed_demo_db(conn, NOW)

    data = build_report_data(conn, NOW)

    assert data.total_tickets == len(DEMO_TICKETS) == 12
    assert data.counts_by_priority == {"HIGH": 6, "MEDIUM": 3, "LOW": 3}
    assert data.unclassified_count == 0  # priorities are seeded directly
    assert data.rejected_count == 2


def test_more_high_tickets_than_default_top(conn):
    seed_demo_db(conn, NOW)

    data = build_report_data(conn, NOW)

    # 6 HIGH tickets exist, so the default top 5 cuts one off.
    assert [r.ticket.id for r in data.top_urgent] == [
        "demo-001", "demo-002", "demo-003", "demo-004", "demo-005",
    ]


def test_tie_on_created_at_is_ordered_by_id(conn):
    seed_demo_db(conn, NOW)

    data = build_report_data(conn, NOW)

    first, second = data.top_urgent[1], data.top_urgent[2]
    assert first.ticket.created_at == second.ticket.created_at
    assert (first.ticket.id, second.ticket.id) == ("demo-002", "demo-003")


def test_tickets_are_dated_relative_to_now(conn):
    seed_demo_db(conn, NOW)

    data = build_report_data(conn, NOW)

    assert data.top_urgent[0].waiting == timedelta(days=2, hours=3)


def test_main_creates_database_and_can_be_rerun(tmp_path, capsys):
    db_path = tmp_path / "demo.db"

    assert main(["--db", str(db_path)]) == 0
    assert main(["--db", str(db_path)]) == 0  # resets first, so no duplicates

    conn = get_db_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    finally:
        conn.close()
    assert count == len(DEMO_TICKETS)
    assert "python -m ticket_triage --db" in capsys.readouterr().out
