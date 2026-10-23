"""
Designed PDF version of the daily report. Needs the optional "reportlab" package;
it is only imported by write_report_pdf() in ticket_triage.report.

Layout (US Letter, portrait): a title banner on the first page, four headline
tiles, two small charts (tickets by priority, tickets by waiting time), then the
"Needs attention first", "Counts by priority" and "Waiting longest" tables. Every
page has a footer with the page number. Ticket ids are shortened to their first
8 characters so the table stays narrow.

Only the standard PDF fonts are used, so characters outside Western European
text are replaced with "?".
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from ticket_triage.models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM
from ticket_triage.report import (
    COUNTS_TITLE,
    NEEDS_ATTENTION_TITLE,
    NO_TICKETS_MESSAGE,
    NO_URGENT_MESSAGE,
    REPORT_TITLE,
    UNCLASSIFIED_LABEL,
    WAITING_LONGEST_TITLE,
    ReportData,
    ReportTicket,
    format_waiting,
)

# --- Look and feel ------------------------------------------------------------

NAVY = HexColor("#1F3A5F")
ACCENT = HexColor("#E07B0D")  # Rule under the title banner
TEXT = HexColor("#1A1A1A")
MUTED = HexColor("#6B7280")
PANEL = HexColor("#F4F6FA")  # Tiles and alternate table rows
LINE = HexColor("#DDE2EA")
RED = HexColor("#B3261E")
BANNER_SUBTITLE = HexColor("#C9D3E3")

PRIORITY_COLORS = {
    PRIORITY_HIGH: HexColor("#DB7312"),
    PRIORITY_MEDIUM: HexColor("#C9A100"),
    PRIORITY_LOW: HexColor("#3B7DD8"),
    UNCLASSIFIED_LABEL: MUTED,
}
PRIORITY_NAMES = {
    PRIORITY_HIGH: "High",
    PRIORITY_MEDIUM: "Medium",
    PRIORITY_LOW: "Low",
    UNCLASSIFIED_LABEL: UNCLASSIFIED_LABEL,
}

FONT = "Helvetica"
BOLD = "Helvetica-Bold"
PAGE_WIDTH, PAGE_HEIGHT = LETTER
CONTENT_LEFT = 60
CONTENT_WIDTH = 504
EDGE_MARGIN = 54  # Banner text and footer
BANNER_HEIGHT = 80
FIRST_PAGE_TOP = 103  # Distance from the top of the page to the first tile
LATER_PAGE_TOP = 50
FOOTER_RULE_Y = 51
FOOTER_TEXT_Y = 39
FOOTER_LEFT_TEXT = "Confidential - internal use only"

ID_LENGTH = 8  # Ticket ids are shown as their first characters

# Column widths (points) for the three tables.
URGENT_COLUMNS = [65, 216, 58, 107, 58]  # Ticket, Subject, Priority, Customer, Waiting
COUNTS_COLUMNS = [123, 72, 65]  # Priority, Tickets, Share
WAITING_COLUMNS = [65, 208, 79, 58, 66]  # Ticket, Subject, Customer, Priority, Waiting

CHART_HEIGHT = 118
CHART_AXIS_Y = 84  # Distance from the top of a chart to its baseline

SECTION_STYLE = ParagraphStyle(
    "section", fontName=BOLD, fontSize=12.5, leading=15, textColor=NAVY,
    spaceBefore=16, spaceAfter=6, keepWithNext=1,
)
CELL_STYLE = ParagraphStyle("cell", fontName=FONT, fontSize=9, leading=11, textColor=TEXT)
HEADER_STYLE = ParagraphStyle(
    "header", parent=CELL_STYLE, fontName=BOLD, textColor=white
)
EMPTY_STYLE = ParagraphStyle(
    "empty", parent=CELL_STYLE, fontName="Helvetica-Oblique", textColor=MUTED
)
TILE_LABEL_STYLE = ParagraphStyle(
    "tile_label", fontName=FONT, fontSize=8.5, leading=10, textColor=MUTED
)


# --- Small helpers ------------------------------------------------------------


def _safe(text: str) -> str:
    """Replace characters the standard PDF fonts cannot show."""
    return text.encode("cp1252", "replace").decode("cp1252")


def _paragraph(text: str, style: ParagraphStyle = CELL_STYLE) -> Paragraph:
    """A paragraph for arbitrary ticket text (escaped, one line of whitespace)."""
    return Paragraph(escape(_safe(" ".join(text.split()))), style)


def _priority_label(level: str | None) -> str:
    return level if level else UNCLASSIFIED_LABEL


def _priority_paragraph(level: str | None) -> Paragraph:
    """The priority name in bold, in its colour."""
    key = _priority_label(level)
    style = ParagraphStyle(
        f"priority_{key}", parent=CELL_STYLE, fontName=BOLD,
        textColor=PRIORITY_COLORS.get(key, MUTED),
    )
    return _paragraph(PRIORITY_NAMES.get(key, key.title()), style)


def _percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def _table_style(header: bool = True) -> TableStyle:
    """Navy header row, thin row rules and alternate-row shading."""
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, PANEL]),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TOPPADDING", (0, 0), (-1, 0), 3.5),
        ]
    return TableStyle(commands)


def _data_table(rows: list[list], widths: list[int]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(_table_style())
    return table


def _empty_state(message: str, width: int = CONTENT_WIDTH) -> Table:
    """A one-row box saying why a section has no rows."""
    table = Table([[_paragraph(message, EMPTY_STYLE)]], colWidths=[width], hAlign="LEFT")
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
        ])
    )
    return table


# --- Page furniture -----------------------------------------------------------


def _draw_banner(canvas, doc) -> None:
    """First page: navy title banner with an accent rule, then the footer."""
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_HEIGHT - BANNER_HEIGHT, PAGE_WIDTH, BANNER_HEIGHT, stroke=0, fill=1)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, PAGE_HEIGHT - BANNER_HEIGHT - 3, PAGE_WIDTH, 3, stroke=0, fill=1)
    canvas.setFillColor(white)
    canvas.setFont(BOLD, 20)
    canvas.drawString(EDGE_MARGIN, PAGE_HEIGHT - 45, REPORT_TITLE)
    canvas.setFillColor(BANNER_SUBTITLE)
    canvas.setFont(FONT, 10)
    canvas.drawString(EDGE_MARGIN, PAGE_HEIGHT - 64, doc.date_line)
    canvas.restoreState()
    _draw_footer(canvas, doc)


def _draw_footer(canvas, doc) -> None:
    """Every page: a thin rule, the confidentiality note and the page number."""
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.75)
    canvas.line(EDGE_MARGIN, FOOTER_RULE_Y, PAGE_WIDTH - EDGE_MARGIN, FOOTER_RULE_Y)
    canvas.setFillColor(MUTED)
    canvas.setFont(FONT, 8)
    canvas.drawString(EDGE_MARGIN, FOOTER_TEXT_Y, FOOTER_LEFT_TEXT)
    canvas.drawRightString(
        PAGE_WIDTH - EDGE_MARGIN, FOOTER_TEXT_Y, f"Page {canvas.getPageNumber()}"
    )
    canvas.restoreState()


# --- Headline tiles and charts ------------------------------------------------


def _tiles(data: ReportData) -> Table:
    """Four headline numbers: total, rejected, high priority, longest wait."""
    longest = data.longest_waiting[0].waiting if data.longest_waiting else None
    tiles = [
        (str(data.total_tickets), "Total tickets", NAVY),
        (str(data.rejected_count), "Rejected records", RED),
        (str(data.counts_by_priority.get(PRIORITY_HIGH, 0)), "High priority",
         PRIORITY_COLORS[PRIORITY_HIGH]),
        (format_waiting(longest) if longest is not None else "-", "Longest wait", NAVY),
    ]
    cells = []
    for value, label, color in tiles:
        number = ParagraphStyle(
            f"tile_{label}", fontName=BOLD, fontSize=22, leading=26, textColor=color
        )
        cells.append([Paragraph(escape(value), number), Paragraph(label, TILE_LABEL_STYLE)])
    table = Table([cells], colWidths=[CONTENT_WIDTH / 4] * 4, hAlign="LEFT")
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("LINEAFTER", (0, 0), (-2, -1), 0.5, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    return table


def _chart_title(drawing: Drawing, x: float, text: str) -> None:
    drawing.add(String(x, CHART_HEIGHT - 14, text, fontName=BOLD, fontSize=11.5, fillColor=NAVY))


def _priority_chart(data: ReportData, width: int = 262) -> Drawing:
    """Horizontal bars: tickets per priority level (plus unclassified, if any)."""
    rows = [(level, data.counts_by_priority.get(level, 0))
            for level in (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)]
    if data.unclassified_count:
        rows.append((UNCLASSIFIED_LABEL, data.unclassified_count))

    drawing = Drawing(width, CHART_HEIGHT)
    left, right = 49, width - 6  # Bars start at `left`; the baseline ends at `right`
    _chart_title(drawing, left, "Tickets by priority")

    top_of_axis = CHART_HEIGHT - CHART_AXIS_Y
    pitch = 21 if len(rows) <= 3 else 16
    first_center = CHART_AXIS_Y - 11 - (len(rows) - 1) * pitch
    longest_bar = 0.77 * (right - left)
    biggest = max(1, *(count for _, count in rows))

    for i, (level, count) in enumerate(rows):
        center = CHART_HEIGHT - (first_center + i * pitch)
        bar = longest_bar * count / biggest
        drawing.add(String(left - 8, center - 3, PRIORITY_NAMES[level], textAnchor="end",
                           fontName=FONT, fontSize=10, fillColor=MUTED))
        if bar:
            drawing.add(Rect(left, center - 6, bar, 12, fillColor=PRIORITY_COLORS[level],
                             strokeColor=None))
        drawing.add(String(left + bar + 6, center - 3, str(count), fontName=BOLD,
                           fontSize=10, fillColor=TEXT))
    drawing.add(Line(left, top_of_axis, right, top_of_axis, strokeColor=LINE, strokeWidth=0.75))
    return drawing


def _age_chart(data: ReportData, width: int = 242) -> Drawing:
    """Vertical bars: tickets per waiting-time bucket."""
    buckets = list(data.waiting_buckets.items())
    drawing = Drawing(width, CHART_HEIGHT)
    _chart_title(drawing, 0, "Tickets by waiting time")

    baseline = CHART_HEIGHT - CHART_AXIS_Y
    right = width - 14
    slot = right / max(1, len(buckets))
    bar_width = min(36, slot * 0.64)
    tallest = 52
    biggest = max([1] + [count for _, count in buckets])

    for i, (label, count) in enumerate(buckets):
        center = slot * i + slot / 2
        height = tallest * count / biggest
        if height:
            drawing.add(Rect(center - bar_width / 2, baseline, bar_width, height,
                             fillColor=NAVY, strokeColor=None))
        drawing.add(String(center, baseline + height + 4, str(count), textAnchor="middle",
                           fontName=BOLD, fontSize=10, fillColor=TEXT))
        for line_number, text in enumerate(label.split(" ", 1)):
            drawing.add(String(center, baseline - 12 - 11 * line_number, text,
                               textAnchor="middle", fontName=FONT, fontSize=9.5,
                               fillColor=MUTED))
    drawing.add(Line(0, baseline, right, baseline, strokeColor=LINE, strokeWidth=0.75))
    return drawing


def _charts(data: ReportData) -> Table:
    table = Table([[_priority_chart(data), _age_chart(data)]],
                  colWidths=[262, CONTENT_WIDTH - 262], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


# --- Tables -------------------------------------------------------------------


def _ticket_cells(entry: ReportTicket) -> dict[str, Paragraph]:
    ticket = entry.ticket
    return {
        "id": _paragraph(ticket.id[:ID_LENGTH]),
        "subject": _paragraph(ticket.subject),
        "customer": _paragraph(ticket.customer),
        "priority": _priority_paragraph(ticket.priority_level),
        "waiting": _paragraph(format_waiting(entry.waiting)),
    }


def _urgent_table(entries: list[ReportTicket]) -> Table:
    if not entries:
        return _empty_state(NO_URGENT_MESSAGE)
    rows = [[_paragraph(h, HEADER_STYLE) for h in
             ("Ticket", "Subject", "Priority", "Customer", "Waiting")]]
    for entry in entries:
        cells = _ticket_cells(entry)
        rows.append([cells["id"], cells["subject"], cells["priority"],
                     cells["customer"], cells["waiting"]])
    return _data_table(rows, URGENT_COLUMNS)


def _waiting_table(entries: list[ReportTicket]) -> Table:
    if not entries:
        return _empty_state(NO_TICKETS_MESSAGE, sum(WAITING_COLUMNS))
    rows = [[_paragraph(h, HEADER_STYLE) for h in
             ("Ticket", "Subject", "Customer", "Priority", "Waiting")]]
    for entry in entries:
        cells = _ticket_cells(entry)
        rows.append([cells["id"], cells["subject"], cells["customer"],
                     cells["priority"], cells["waiting"]])
    return _data_table(rows, WAITING_COLUMNS)


def _counts_table(data: ReportData) -> Table:
    counted = sum(data.counts_by_priority.values()) + data.unclassified_count
    rows = [[_paragraph(h, HEADER_STYLE) for h in ("Priority", "Tickets", "Share")]]
    levels = [(level, data.counts_by_priority.get(level, 0))
              for level in (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)]
    if data.unclassified_count:
        levels.append((UNCLASSIFIED_LABEL, data.unclassified_count))
    for level, count in levels:
        rows.append([_priority_paragraph(level), _paragraph(str(count)),
                     _paragraph(_percent(count, counted))])
    total_style = ParagraphStyle("total", parent=CELL_STYLE, fontName=BOLD)
    rows.append([_paragraph("Total", total_style), _paragraph(str(counted), total_style),
                 _paragraph("100%" if counted else "0%", total_style)])
    return _data_table(rows, COUNTS_COLUMNS)


# --- Document -----------------------------------------------------------------


def build_pdf(data: ReportData, path: Path) -> None:
    """Lay out `data` as the designed report and write it to `path`."""
    date_line = f"Date: {data.generated_at.strftime('%Y-%m-%d %H:%M %Z').strip()}"
    doc = BaseDocTemplate(
        str(path), pagesize=LETTER, title=REPORT_TITLE,
        # Uncompressed, so the text stays readable inside the file (and testable).
        pageCompression=0,
    )
    doc.date_line = date_line  # Read by the banner callback

    first_top = PAGE_HEIGHT - FIRST_PAGE_TOP
    later_top = PAGE_HEIGHT - LATER_PAGE_TOP

    def frame(top: float, frame_id: str) -> Frame:
        return Frame(CONTENT_LEFT, 62, CONTENT_WIDTH, top - 62, id=frame_id,
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    doc.addPageTemplates([
        PageTemplate(id="first", frames=[frame(first_top, "first")], onPage=_draw_banner),
        PageTemplate(id="later", frames=[frame(later_top, "later")], onPage=_draw_footer),
    ])

    story = [
        NextPageTemplate("later"),
        _tiles(data),
        Spacer(1, 12),
        _charts(data),
        Paragraph(NEEDS_ATTENTION_TITLE, SECTION_STYLE),
        _urgent_table(data.top_urgent),
        Paragraph(COUNTS_TITLE, SECTION_STYLE),
        _counts_table(data),
        Paragraph(WAITING_LONGEST_TITLE, SECTION_STYLE),
        _waiting_table(data.longest_waiting),
    ]
    doc.build(story)
