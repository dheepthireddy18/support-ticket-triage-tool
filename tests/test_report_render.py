"""Tests for rendering the report text and writing it to a file."""

from datetime import datetime, timedelta, timezone

import pytest

from ticket_triage.models import Ticket
from ticket_triage.report import (
    NO_TICKETS_MESSAGE,
    NO_URGENT_MESSAGE,
    ReportData,
    ReportTicket,
    build_report_data,
    format_waiting,
    render_report,
    write_report,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def entry(ticket_id, priority, waiting, subject="Cannot log in", customer="CUST-1"):
    """A ReportTicket with the given id, priority and waiting time."""
    ticket = Ticket(
        id=ticket_id,
        subject=subject,
        body="body",
        customer=customer,
        created_at="2026-10-17T09:00:00Z",
        priority_level=priority,
    )
    return ReportTicket(ticket, waiting)


@pytest.fixture
def data():
    """A report with one urgent ticket, one other ticket and one unclassified."""
    urgent = entry("t-high", "HIGH", timedelta(days=2, hours=3), "Site is down", "ACME")
    other = entry("t-low", "LOW", timedelta(hours=5, minutes=12))
    return ReportData(
        generated_at=NOW,
        total_tickets=3,
        counts_by_priority={"HIGH": 1, "MEDIUM": 0, "LOW": 1},
        unclassified_count=1,
        top_urgent=[urgent],
        longest_waiting=[urgent, other],
        rejected_count=2,
    )


class TestFormatWaiting:
    @pytest.mark.parametrize(
        "waiting, expected",
        [
            (timedelta(days=2, hours=3, minutes=40), "2d 3h"),
            (timedelta(days=1), "1d 0h"),
            (timedelta(hours=5, minutes=12), "5h 12m"),
            (timedelta(minutes=42), "42m"),
            (timedelta(seconds=59), "<1m"),
            (timedelta(0), "<1m"),
            (timedelta(minutes=59, seconds=59), "59m"),
            (timedelta(minutes=60), "1h 0m"),
            (timedelta(hours=23, minutes=59), "23h 59m"),
            (timedelta(hours=24), "1d 0h"),
            (timedelta(days=30, hours=7), "30d 7h"),
            (timedelta(minutes=-5), "<1m"),  # never negative
        ],
    )
    def test_formats(self, waiting, expected):
        assert format_waiting(waiting) == expected


class TestRenderReport:
    def test_header(self, data):
        text = render_report(data)

        assert "Daily Support Ticket Summary" in text
        assert "Date: 2026-10-19 12:00 UTC" in text
        assert "Total tickets: 3" in text
        assert "Rejected records: 2" in text

    def test_date_uses_the_timezone_of_generated_at(self, data):
        data.generated_at = datetime(
            2026, 10, 19, 14, 0, tzinfo=timezone(timedelta(hours=2))
        )

        assert "Date: 2026-10-19 14:00 UTC+02:00" in render_report(data)

    def test_no_section_is_blank(self, conn):
        lines = render_report(build_report_data(conn, NOW)).splitlines()

        for title in ("Needs attention first", "Counts by priority", "Waiting longest"):
            body = lines[lines.index(title) + 2]  # skip the underline
            assert body.strip(), f"{title} has no content"

    def test_one_row_per_ticket(self, data):
        text = render_report(data)

        assert text.count("t-high |") == 2  # urgent list and waiting list
        assert text.count("t-low |") == 1

    def test_sections_are_in_manager_first_order(self, data):
        text = render_report(data)

        positions = [
            text.index("Needs attention first"),
            text.index("Counts by priority"),
            text.index("Waiting longest"),
        ]
        assert positions == sorted(positions)
        assert text.index("Total tickets") < positions[0]

    def test_ticket_row_has_all_fields(self, data):
        text = render_report(data)

        assert "t-high | ACME | Site is down | HIGH | waiting 2d 3h" in text

    def test_counts_by_priority(self, data):
        text = render_report(data)

        assert "HIGH: 1" in text
        assert "MEDIUM: 0" in text
        assert "LOW: 1" in text

    def test_unclassified_line_shown_when_positive(self, data):
        assert "Unclassified: 1" in render_report(data)

    def test_unclassified_line_hidden_when_zero(self, data):
        data.unclassified_count = 0

        assert "Unclassified:" not in render_report(data)

    def test_unclassified_ticket_row_is_labelled(self, data):
        data.longest_waiting = [entry("t-new", None, timedelta(hours=1))]

        assert "t-new | CUST-1 | Cannot log in | Unclassified | waiting 1h 0m" in render_report(data)

    def test_multiline_subject_stays_on_one_line(self, data):
        data.top_urgent = [entry("t-1", "HIGH", timedelta(hours=1), "Line one\nline  two")]

        assert "Line one line two" in render_report(data)

    def test_day_with_no_urgent_tickets(self, data):
        data.top_urgent = []

        text = render_report(data)

        assert NO_URGENT_MESSAGE in text
        assert "HIGH: 1" in text  # counts are unaffected

    def test_empty_db_has_no_blank_section(self, conn):
        text = render_report(build_report_data(conn, NOW))

        assert "Total tickets: 0" in text
        assert "Rejected records: 0" in text
        assert NO_URGENT_MESSAGE in text
        assert NO_TICKETS_MESSAGE in text
        assert "HIGH: 0" in text
        assert "Unclassified" not in text

    def test_report_from_database(self, conn, add_ticket):
        add_ticket("a", "2026-10-17T09:00:00Z", "HIGH", subject="Site is down")

        text = render_report(build_report_data(conn, NOW))

        assert "a | CUST-a | Site is down | HIGH | waiting 2d 3h" in text

    def test_ends_with_single_newline(self, data):
        text = render_report(data)

        assert text.endswith("\n")
        assert not text.endswith("\n\n")


class TestWriteReport:
    def test_creates_missing_folder_and_writes_file(self, tmp_path):
        path = tmp_path / "reports" / "daily.txt"

        result = write_report("hello\n", path)

        assert result == path
        assert path.read_text(encoding="utf-8") == "hello\n"

    def test_writes_utf8(self, tmp_path):
        path = tmp_path / "daily.txt"

        write_report("Café – 日本語\n", str(path))

        assert path.read_bytes().decode("utf-8") == "Café – 日本語\n"

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "daily.txt"
        path.write_text("old", encoding="utf-8")

        write_report("new", path)

        assert path.read_text(encoding="utf-8") == "new"
