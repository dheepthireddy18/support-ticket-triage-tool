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


@pytest.fixture
def no_reportlab(monkeypatch):
    """Make `import reportlab` fail, as if the package were not installed."""
    monkeypatch.setitem(sys.modules, "reportlab", None)


@needs_reportlab
class TestWriteReportPdf:
    def test_writes_a_valid_non_empty_pdf(self, conn, tmp_path):
        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert data.startswith(b"%PDF")
        assert data.rstrip().endswith(b"%%EOF")

    def test_contains_the_report_sections_and_rows(self, conn, add_ticket, tmp_path):
        add_ticket("t-1", "2026-10-17T09:00:00Z", "HIGH", subject="Site is down")

        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        for text in (
            b"Daily Support Ticket Summary",
            b"Needs attention first",
            b"Counts by priority",
            b"Waiting longest",
            b"t-1 | CUST-t-1 | Site is down | HIGH | waiting 2d 3h",
        ):
            assert text in data

    def test_empty_db_shows_empty_states(self, conn, tmp_path):
        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert b"No urgent tickets today" in data
        assert b"No tickets to show" in data
        assert b"Unclassified" not in data

    def test_unclassified_line_only_when_needed(self, conn, add_ticket, tmp_path):
        add_ticket("t-1", "2026-10-17T09:00:00Z", None)

        assert b"Unclassified: 1" in pdf_bytes(conn, tmp_path / "daily.pdf")

    def test_creates_missing_folder_and_returns_path(self, conn, tmp_path):
        path = tmp_path / "reports" / "daily.pdf"

        result = write_report_pdf(build_report_data(conn, NOW), path)

        assert result == path
        assert path.is_file()

    def test_long_report_continues_on_more_pages(self, conn, add_ticket, tmp_path):
        for i in range(120):
            add_ticket(f"t-{i:03d}", "2026-10-17T09:00:00Z", "HIGH")

        data = pdf_bytes(conn, tmp_path / "daily.pdf", top_n=120)

        assert len(re.findall(rb"/Type /Page\b(?!s)", data)) > 1

    def test_long_row_is_wrapped_not_lost(self, conn, add_ticket, tmp_path):
        subject = "very long subject " * 20 + "ENDMARK"
        add_ticket("t-1", "2026-10-17T09:00:00Z", "HIGH", subject=subject)

        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert b"ENDMARK" in data

    def test_text_the_font_cannot_show_does_not_crash(self, conn, add_ticket, tmp_path):
        add_ticket("t-1", "2026-10-17T09:00:00Z", "HIGH", subject="Café 日本語")

        data = pdf_bytes(conn, tmp_path / "daily.pdf")

        assert data.startswith(b"%PDF")


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
