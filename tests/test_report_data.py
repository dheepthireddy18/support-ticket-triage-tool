"""Tests for assembling the report data (fixtures live in conftest.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from ticket_triage.report import ReportData, build_report_data

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def test_empty_db(conn):
    data = build_report_data(conn, NOW)

    assert isinstance(data, ReportData)
    assert data.generated_at == NOW
    assert data.total_tickets == 0
    assert data.counts_by_priority == {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    assert data.unclassified_count == 0
    assert data.top_urgent == []
    assert data.longest_waiting == []
    assert data.rejected_count == 0


def test_totals_counts_and_rejected(conn, add_ticket, add_rejected):
    add_ticket("a", "2026-10-19T08:00:00Z", "HIGH")
    add_ticket("b", "2026-10-19T09:00:00Z", "MEDIUM")
    add_ticket("c", "2026-10-19T10:00:00Z", "LOW")
    add_ticket("d", "2026-10-19T11:00:00Z", None)
    add_rejected()

    data = build_report_data(conn, NOW)

    # The total includes the unclassified ticket even though it is in no level.
    assert data.total_tickets == 4
    assert data.counts_by_priority == {"HIGH": 1, "MEDIUM": 1, "LOW": 1}
    assert data.unclassified_count == 1
    assert data.rejected_count == 1


def test_waiting_time_is_now_minus_created_at(conn, add_ticket):
    # 2026-10-17 09:00 to 2026-10-19 12:00 is 2 days 3 hours.
    add_ticket("a", "2026-10-17T09:00:00Z", "HIGH")

    data = build_report_data(conn, NOW)

    assert data.top_urgent[0].waiting == timedelta(days=2, hours=3)
    assert data.longest_waiting[0].waiting == timedelta(days=2, hours=3)


def test_waiting_time_handles_offset_and_naive_created_at(conn, add_ticket):
    add_ticket("a", "2026-10-19T10:00:00+00:00", "HIGH")
    add_ticket("b", "2026-10-19T08:00:00", "LOW")  # no timezone: treated as UTC

    waits = {r.ticket.id: r.waiting for r in build_report_data(conn, NOW).longest_waiting}

    assert waits == {"a": timedelta(hours=2), "b": timedelta(hours=4)}


def test_future_created_at_waits_zero(conn, add_ticket):
    add_ticket("a", "2026-10-20T12:00:00Z", "HIGH")

    assert build_report_data(conn, NOW).top_urgent[0].waiting == timedelta(0)


def test_fewer_tickets_than_top_n(conn, add_ticket):
    add_ticket("a", "2026-10-19T08:00:00Z", "HIGH")
    add_ticket("b", "2026-10-19T09:00:00Z", "HIGH")

    data = build_report_data(conn, NOW)  # default top_n=5

    assert [r.ticket.id for r in data.top_urgent] == ["a", "b"]
    assert [r.ticket.id for r in data.longest_waiting] == ["a", "b"]


def test_exactly_top_n(conn, add_ticket):
    for i in range(5):
        add_ticket(f"t{i}", f"2026-10-19T0{i}:00:00Z", "HIGH")

    data = build_report_data(conn, NOW, top_n=5)

    assert len(data.top_urgent) == 5
    assert len(data.longest_waiting) == 5


def test_top_n_limits_both_lists(conn, add_ticket):
    for i in range(6):
        add_ticket(f"t{i}", f"2026-10-19T0{i}:00:00Z", "HIGH")

    data = build_report_data(conn, NOW, top_n=3)

    assert [r.ticket.id for r in data.top_urgent] == ["t0", "t1", "t2"]
    assert [r.ticket.id for r in data.longest_waiting] == ["t0", "t1", "t2"]
    assert data.total_tickets == 6  # only the lists are limited, not the totals


def test_default_top_n_is_five(conn, add_ticket):
    for i in range(7):
        add_ticket(f"t{i}", f"2026-10-19T0{i}:00:00Z", "HIGH")

    data = build_report_data(conn, NOW)

    assert len(data.top_urgent) == 5
    assert len(data.longest_waiting) == 5


def test_top_n_zero_gives_empty_lists(conn, add_ticket):
    add_ticket("a", "2026-10-19T08:00:00Z", "HIGH")

    data = build_report_data(conn, NOW, top_n=0)

    assert data.top_urgent == []
    assert data.longest_waiting == []
    assert data.total_tickets == 1


def test_negative_top_n_is_rejected(conn):
    # SQLite treats LIMIT -1 as "no limit", which would silently return everything.
    with pytest.raises(ValueError, match="top_n"):
        build_report_data(conn, NOW, top_n=-1)


def test_naive_now_is_rejected(conn):
    with pytest.raises(ValueError, match="timezone"):
        build_report_data(conn, datetime(2026, 10, 19, 12, 0), top_n=5)


def test_day_with_no_high_tickets(conn, add_ticket):
    add_ticket("a", "2026-10-19T08:00:00Z", "MEDIUM")
    add_ticket("b", "2026-10-19T09:00:00Z", "LOW")

    data = build_report_data(conn, NOW)

    assert data.top_urgent == []
    assert data.counts_by_priority["HIGH"] == 0
    assert [r.ticket.id for r in data.longest_waiting] == ["a", "b"]


def test_waiting_buckets_cover_every_ticket_even_beyond_top_n(conn, add_ticket):
    add_ticket("new", "2026-10-19T08:00:00Z", "LOW")  # 4h
    add_ticket("mid", "2026-10-17T09:00:00Z", "LOW")  # 2d 3h
    add_ticket("old", "2026-10-01T09:00:00Z", "LOW")  # 18d 3h

    data = build_report_data(conn, NOW, top_n=1)

    assert data.waiting_buckets == {
        "Under 1 day": 1, "1-3 days": 1, "3-7 days": 0, "7+ days": 1,
    }


def test_waiting_buckets_leave_out_resolved_tickets(conn, add_ticket):
    add_ticket("open", "2026-10-17T09:00:00Z", "LOW")
    add_ticket("closed", "2026-10-17T09:00:00Z", "LOW")
    conn.execute("UPDATE tickets SET resolved_at = '2026-10-18T09:00:00Z' WHERE id = 'closed'")
    conn.commit()

    data = build_report_data(conn, NOW)

    assert sum(data.waiting_buckets.values()) == data.total_tickets == 1


def test_huge_top_n_returns_everything_instead_of_overflowing(conn, add_ticket):
    add_ticket("a", "2026-10-19T08:00:00Z", "HIGH")

    data = build_report_data(conn, NOW, top_n=10**30)  # beyond SQLite's integer range

    assert [r.ticket.id for r in data.top_urgent] == ["a"]
    assert [r.ticket.id for r in data.longest_waiting] == ["a"]
