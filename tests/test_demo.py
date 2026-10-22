"""Tests for the demo database seeder."""

from datetime import datetime, timedelta, timezone

from ticket_triage.db import get_db_connection, init_db
from ticket_triage.demo import DEMO_TICKETS, count_non_demo_tickets, main, seed_demo_db
from ticket_triage.models import Ticket
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


def stored_ids(db_path):
    conn = get_db_connection(db_path)
    try:
        return {row["id"] for row in conn.execute("SELECT id FROM tickets")}
    finally:
        conn.close()


def database_with_a_real_ticket(path):
    """Create a database holding one ticket that is not a demo ticket."""
    conn = init_db(path)
    ticket = Ticket(
        id="real-1", subject="Real", body="Real body", customer="ACME",
        created_at="2026-10-19T08:00:00Z",
    )
    with conn:
        conn.execute(
            "INSERT INTO tickets (id, content_hash, subject, body, customer, created_at, "
            "inserted_at, status, priority_level, classified_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ticket.to_db_tuple(),
        )
    conn.close()


def test_refuses_to_erase_a_database_with_real_tickets(tmp_path, capsys):
    db_path = tmp_path / "tickets.db"
    database_with_a_real_ticket(db_path)

    code = main(["--db", str(db_path)])

    assert code == 2
    assert "not demo tickets" in capsys.readouterr().err
    assert stored_ids(db_path) == {"real-1"}  # untouched


def test_force_resets_a_database_with_real_tickets(tmp_path):
    db_path = tmp_path / "tickets.db"
    database_with_a_real_ticket(db_path)

    assert main(["--db", str(db_path), "--force"]) == 0

    assert "real-1" not in stored_ids(db_path)
    assert len(stored_ids(db_path)) == len(DEMO_TICKETS)


def test_count_non_demo_tickets_ignores_missing_files_and_tables(tmp_path):
    assert count_non_demo_tickets(tmp_path / "missing.db") == 0
    assert count_non_demo_tickets(":memory:") == 0
    empty = tmp_path / "empty.db"
    get_db_connection(empty).close()  # a database with no tables
    assert count_non_demo_tickets(empty) == 0


def test_database_that_is_not_sqlite_exits_with_error(tmp_path, capsys):
    junk = tmp_path / "junk.db"
    junk.write_text("this is not sqlite", encoding="utf-8")

    code = main(["--db", str(junk)])

    assert code == 2
    assert "could not use database" in capsys.readouterr().err
