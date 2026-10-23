"""Tests for the optional PDF report output."""

import importlib.util
import re
import sys
from datetime import datetime, timezone

import pytest

from ticket_triage.report import (
    PDF_INSTALL_HINT,
    PdfUnavailableError,
    build_report_data,
    require_pdf_support,
    write_report_pdf,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

needs_reportlab = pytest.mark.skipif(
    importlib.util.find_spec("reportlab") is None,
    reason="the optional reportlab package is not installed",
)


def pdf_bytes(conn, path, top_n=5):
    """Write the report for `conn` as a PDF and return the file's bytes."""
    write_report_pdf(build_report_data(conn, NOW, top_n), path)
    return path.read_bytes()


def page_count(data):
    return len(re.findall(rb"/Type /Page\b(?!s)", data))


@pytest.fixture
def no_reportlab(monkeypatch):
    """Make `import reportlab` fail, as if the package were not installed."""
    monkeypatch.setitem(sys.modules, "reportlab", None)


@needs_reportlab
class TestWriteReportPdf:
    def test_writes_a_valid_pdf(self, conn, tmp_path):
        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert data.startswith(b"%PDF")
        assert data.rstrip().endswith(b"%%EOF")

    def test_creates_missing_folder_and_returns_path(self, conn, tmp_path):
        path = tmp_path / "reports" / "daily.pdf"

        result = write_report_pdf(build_report_data(conn, NOW), path)

        assert result == path
        assert path.is_file()

    def test_has_the_banner_tiles_charts_and_sections(self, conn, add_ticket, tmp_path):
        add_ticket("t-1", "2026-10-17T09:00:00Z", "HIGH")

        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        for text in (
            b"Daily Support Ticket Summary",
            b"Date: 2026-10-19 12:00 UTC",
            b"Total tickets", b"Rejected records", b"High priority", b"Longest wait",
            b"Tickets by priority", b"Tickets by waiting time",
            b"Needs attention first", b"Counts by priority", b"Waiting longest",
        ):
            assert text in data

    def test_footer_has_the_note_and_page_number_but_no_sample_text(
        self, conn, tmp_path
    ):
        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert b"Confidential - internal use only" in data
        assert b"Page 1" in data
        assert b"SAMPLE DATA" not in data

    def test_ticket_rows_show_short_id_subject_customer_priority_and_waiting(
        self, conn, add_ticket, tmp_path
    ):
        add_ticket(
            "7c1d3a52-95b4-4f0e-8a6b-2d9e4f7a1b03", "2026-10-17T09:00:00Z", "HIGH",
            subject="Site is down", customer="ACME",
        )

        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert b"(7c1d3a52)" in data  # first 8 characters only
        assert b"95b4-4f0e" not in data
        for text in (b"Site is down", b"ACME", b"High", b"2d 3h"):
            assert text in data

    def test_counts_table_shows_shares_and_total(self, conn, add_ticket, tmp_path):
        add_ticket("a", "2026-10-19T08:00:00Z", "HIGH")
        add_ticket("b", "2026-10-19T09:00:00Z", "LOW")
        add_ticket("c", "2026-10-19T10:00:00Z", "LOW")
        add_ticket("d", "2026-10-19T11:00:00Z", "LOW")

        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert b"(25%)" in data and b"(75%)" in data
        assert b"(Total)" in data and b"(100%)" in data

    def test_unclassified_row_only_when_needed(self, conn, add_ticket, tmp_path):
        assert b"Unclassified" not in pdf_bytes(conn, tmp_path / "empty.pdf")

        add_ticket("a", "2026-10-19T08:00:00Z", None)

        assert b"Unclassified" in pdf_bytes(conn, tmp_path / "with.pdf")

    def test_empty_db_shows_empty_states_and_zero_tiles(self, conn, tmp_path):
        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert b"No urgent tickets today" in data
        assert b"No tickets to show" in data
        assert b"(0%)" in data  # the counts table total

    def test_longest_wait_tile_uses_the_oldest_ticket(self, conn, add_ticket, tmp_path):
        add_ticket("old", "2026-10-11T04:00:00Z", "LOW")  # 8 days 8 hours before NOW
        add_ticket("new", "2026-10-19T08:00:00Z", "LOW")

        assert b"(8d 8h)" in pdf_bytes(conn, tmp_path / "daily.pdf")

    def test_long_report_continues_on_more_pages_with_page_numbers(
        self, conn, add_ticket, tmp_path
    ):
        for i in range(120):
            add_ticket(f"t-{i:03d}", "2026-10-17T09:00:00Z", "HIGH")

        data = pdf_bytes(conn, tmp_path / "daily.pdf", top_n=120)

        assert page_count(data) > 1
        assert b"Page 2" in data

    def test_long_subject_wraps_instead_of_being_lost(self, conn, add_ticket, tmp_path):
        subject = "very long subject " * 20 + "ENDMARK"
        add_ticket("t-1", "2026-10-17T09:00:00Z", "HIGH", subject=subject)

        assert b"ENDMARK" in pdf_bytes(conn, tmp_path / "daily.pdf")

    def test_text_that_looks_like_markup_is_shown_as_text(self, conn, add_ticket, tmp_path):
        add_ticket(
            "t-1", "2026-10-17T09:00:00Z", "HIGH",
            subject="Refund <b>now</b> & more", customer="A&B <Corp>",
        )

        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        # The PDF writes the text in pieces; the "<" kept inside "(Refund <)" shows the
        # tag was not read as markup (which would have been swallowed).
        assert b"(Refund <)" in data and b"(now)" in data
        assert b"(Corp)" in data and b"(&)" in data

    def test_text_the_font_cannot_show_does_not_crash(self, conn, add_ticket, tmp_path):
        add_ticket("t-1", "2026-10-17T09:00:00Z", "HIGH", subject="Café 日本語")

        assert pdf_bytes(conn, tmp_path / "daily.pdf").startswith(b"%PDF")

    def test_charts_handle_all_zero_and_unclassified_data(self, conn, add_ticket, tmp_path):
        add_ticket("a", "2026-10-19T08:00:00Z", None)

        assert pdf_bytes(conn, tmp_path / "daily.pdf").startswith(b"%PDF")


class TestPdfUnavailable:
    def test_require_pdf_support_explains_how_to_install(self, no_reportlab):
        with pytest.raises(PdfUnavailableError, match="pip install"):
            require_pdf_support()

    def test_write_report_pdf_fails_clearly(self, conn, tmp_path, no_reportlab):
        path = tmp_path / "daily.pdf"

        with pytest.raises(PdfUnavailableError) as exc:
            write_report_pdf(build_report_data(conn, NOW), path)

        assert str(exc.value) == PDF_INSTALL_HINT
        assert not path.exists()
