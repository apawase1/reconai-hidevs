"""tools/export_tools.py — builds a single-file, print-friendly PDF snapshot of the reconciliation report."""

import re
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_TEAL = colors.HexColor("#0891B2")
_PURPLE = colors.HexColor("#4F46E5")
_CORAL = colors.HexColor("#DC2626")
_AMBER = colors.HexColor("#B45309")
_PINK = colors.HexColor("#DB2777")
_INK = colors.HexColor("#111827")
_MUTED = colors.HexColor("#6B7280")
_BORDER = colors.HexColor("#E5E7EB")
_ROW_ALT = colors.HexColor("#F9FAFB")
_HEADER_BG = colors.HexColor("#F0F9FF")

_PIE_PALETTE = [
    _TEAL, _PURPLE, _CORAL, _AMBER, _PINK,
    colors.HexColor("#059669"), colors.HexColor("#D97706"), colors.HexColor("#4338CA"),
]


class _Bookmark(Flowable):
    """Invisible flowable that registers a native PDF outline entry at its position in the document."""

    def __init__(self, key: str, title: str, level: int = 0):
        Flowable.__init__(self)
        self.key = key
        self.title = title
        self.level = level

    def wrap(self, available_width, available_height):
        return (0, 0)

    def draw(self):
        self.canv.bookmarkPage(self.key)
        self.canv.addOutlineEntry(self.title, self.key, level=self.level, closed=False)


def _styles() -> Dict[str, ParagraphStyle]:
    """Returns the named ParagraphStyle set used throughout the PDF."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ReconTitle", parent=base["Title"], textColor=_INK, fontSize=22, spaceAfter=2, alignment=TA_LEFT),
        "subtitle": ParagraphStyle("ReconSubtitle", parent=base["Normal"], textColor=_MUTED, fontSize=10, spaceAfter=14),
        "h2": ParagraphStyle("ReconH2", parent=base["Heading2"], textColor=_TEAL, fontSize=15, spaceBefore=4, spaceAfter=8),
        "h3": ParagraphStyle("ReconH3", parent=base["Heading3"], textColor=_INK, fontSize=11.5, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("ReconBody", parent=base["Normal"], textColor=_INK, fontSize=10, leading=14),
        "muted": ParagraphStyle("ReconMuted", parent=base["Normal"], textColor=_MUTED, fontSize=9, leading=13, spaceAfter=8),
        "bullet": ParagraphStyle("ReconBullet", parent=base["Normal"], textColor=_INK, fontSize=10, leading=14, leftIndent=10),
        "kpi_label": ParagraphStyle("KpiLabel", parent=base["Normal"], textColor=_MUTED, fontSize=8, alignment=TA_LEFT),
        "kpi_value": ParagraphStyle("KpiValue", parent=base["Normal"], textColor=_INK, fontSize=14, alignment=TA_LEFT, leading=17),
        "table_header": ParagraphStyle("TableHeader", parent=base["Normal"], textColor=colors.white, fontSize=9, alignment=TA_LEFT),
        "table_cell": ParagraphStyle("TableCell", parent=base["Normal"], textColor=_INK, fontSize=9.5, alignment=TA_LEFT),
        "table_cell_amount": ParagraphStyle("TableCellAmount", parent=base["Normal"], textColor=_INK, fontSize=9.5, alignment=TA_LEFT),
        "empty": ParagraphStyle("Empty", parent=base["Normal"], textColor=_MUTED, fontSize=9.5, fontName="Helvetica-Oblique"),
    }


def _md_inline_to_rl(text: str) -> str:
    """Escapes XML special characters and converts **bold** to reportlab's <b> tag."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text


def _narrative_to_flowables(markdown_text: str, styles: Dict[str, ParagraphStyle], bookmark_prefix: str) -> List[Any]:
    """Converts the Reporting Agent's small markdown subset (headings/bullets/bold) into flowables."""
    flowables: List[Any] = []
    bookmark_count = 0
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            flowables.append(Spacer(1, 4))
            continue
        if line.startswith("## "):
            bookmark_count += 1
            title = line[3:].strip()
            flowables.append(Spacer(1, 6))
            flowables.append(_Bookmark(f"{bookmark_prefix}-{bookmark_count}", title, level=1))
            flowables.append(Paragraph(_md_inline_to_rl(title), styles["h3"]))
        elif line.startswith("# "):
            flowables.append(Paragraph(_md_inline_to_rl(line[2:].strip()), styles["h2"]))
        elif line.startswith("- "):
            flowables.append(Paragraph("&bull;&nbsp;&nbsp;" + _md_inline_to_rl(line[2:].strip()), styles["bullet"]))
        else:
            flowables.append(Paragraph(_md_inline_to_rl(line), styles["body"]))
    return flowables


def _kpi_grid(report: Dict[str, Any], transaction_count: int, styles: Dict[str, ParagraphStyle]) -> Table:
    """Builds the top-of-page KPI summary table."""
    def cell(label: str, value: str) -> List[Any]:
        return [Paragraph(label, styles["kpi_label"]), Paragraph(value, styles["kpi_value"])]

    kpis = [
        cell("Total spent", f"{report.get('total_spent', 0):,.2f}"),
        cell("Total money in", f"{report.get('total_income', 0):,.2f}"),
        cell("Transactions", str(transaction_count)),
        cell("Duplicates flagged", str(report.get("duplicate_count", 0))),
        cell("Missing invoices", str(report.get("missing_invoice_count", 0))),
        cell("Recurring charges", str(report.get("recurring_count", 0))),
        cell("GST-eligible total", f"{report.get('gst_eligible_total', 0):,.2f}"),
        cell("Business / Personal", f"{report.get('business_total', 0):,.0f} / {report.get('personal_total', 0):,.0f}"),
    ]
    rows = [kpis[i:i + 4] for i in range(0, len(kpis), 4)]
    col_width = 4.4 * cm
    table = Table(rows, colWidths=[col_width] * 4, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _HEADER_BG),
        ("BOX", (0, 0), (-1, -1), 0.75, _BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def _category_pie(category_breakdown: Dict[str, float]) -> Optional[Drawing]:
    """Builds a native vector pie chart (reportlab.graphics) of the category breakdown."""
    if not category_breakdown:
        return None
    items = sorted(category_breakdown.items(), key=lambda kv: -kv[1])[:8]
    drawing = Drawing(420, 190)
    pie = Pie()
    pie.x = 40
    pie.y = 10
    pie.width = 150
    pie.height = 150
    pie.data = [amount for _, amount in items]
    pie.labels = None
    pie.sideLabels = False
    for i in range(len(items)):
        pie.slices[i].fillColor = _PIE_PALETTE[i % len(_PIE_PALETTE)]
        pie.slices[i].strokeColor = colors.white
        pie.slices[i].strokeWidth = 1.5
    drawing.add(pie)

    legend = Legend()
    legend.x = 220
    legend.y = 150
    legend.dx = 8
    legend.dy = 8
    legend.fontSize = 9
    legend.fontName = "Helvetica"
    legend.alignment = "left"
    legend.columnMaximum = 8
    total = sum(amount for _, amount in items) or 1
    legend.colorNamePairs = [
        (_PIE_PALETTE[i % len(_PIE_PALETTE)], f"{cat}  —  {amount:,.0f} ({round(amount / total * 100)}%)")
        for i, (cat, amount) in enumerate(items)
    ]
    drawing.add(legend)
    return drawing


def _simple_table(
    headers: List[str],
    rows: List[List[str]],
    col_widths: List[float],
    styles: Dict[str, ParagraphStyle],
    header_color=_TEAL,
) -> Table:
    """Builds a styled, zebra-striped reportlab Table from headers and rows."""
    header_row = [Paragraph(h, styles["table_header"]) for h in headers]
    body_rows = [[Paragraph(str(cell), styles["table_cell"]) for cell in row] for row in rows]
    table = Table([header_row] + body_rows, colWidths=col_widths, hAlign="LEFT", repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), header_color),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, header_color),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for row_index in range(1, len(body_rows) + 1):
        if row_index % 2 == 0:
            style.append(("BACKGROUND", (0, row_index), (-1, row_index), _ROW_ALT))
    table.setStyle(TableStyle(style))
    return table


def _section(
    flowables: List[Any],
    key: str,
    title: str,
    styles: Dict[str, ParagraphStyle],
    body: List[Any],
    empty_message: Optional[str] = None,
) -> None:
    """Appends a bookmarked section heading plus its body flowables or an empty_message placeholder."""
    section_flowables: List[Any] = [
        _Bookmark(key, title, level=0),
        Paragraph(title, styles["h2"]),
        HRFlowable(width="100%", thickness=0.75, color=_BORDER, spaceAfter=8),
    ]
    if body:
        section_flowables.extend(body)
    elif empty_message:
        section_flowables.append(Paragraph(empty_message, styles["empty"]))
    flowables.append(KeepTogether(section_flowables[:3]))
    flowables.extend(section_flowables[3:])
    flowables.append(Spacer(1, 10))


def export_report_to_pdf(
    report: Dict[str, Any],
    transaction_count: int = 0,
    narrative_markdown: Optional[str] = None,
    generated_for: Optional[str] = None,
) -> bytes:
    """Builds the full reconciliation report + dashboard as a single bookmarked PDF and returns its bytes."""
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        title="ReconAI Reconciliation Report",
    )

    flowables: List[Any] = []

    flowables.append(Paragraph("ReconAI — Reconciliation Report", styles["title"]))
    subtitle_bits = [f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}"]
    if generated_for:
        subtitle_bits.append(f"for {generated_for}")
    flowables.append(Paragraph("  &middot;  ".join(subtitle_bits), styles["subtitle"]))
    flowables.append(_kpi_grid(report, transaction_count, styles))
    flowables.append(Spacer(1, 14))

    category_breakdown = report.get("category_breakdown") or {}
    category_vendors = report.get("category_vendors") or {}
    body: List[Any] = []
    pie = _category_pie(category_breakdown)
    if pie:
        body.append(pie)
        body.append(Spacer(1, 6))
    if category_breakdown:
        grand_total = sum(category_breakdown.values()) or 1
        rows = []
        for cat, amount in sorted(category_breakdown.items(), key=lambda kv: -kv[1]):
            pct = round(amount / grand_total * 100)
            vendors = ", ".join(category_vendors.get(cat, [])[:4])
            rows.append([cat, f"{amount:,.2f}", f"{pct}%", vendors])
        body.append(_simple_table(
            ["Category", "Amount", "% of total", "Vendors"], rows,
            [3.3 * cm, 2.6 * cm, 2.2 * cm, 7.5 * cm], styles,
        ))
    _section(flowables, "cat", "Where it went", styles, body, "No categorized spend this period.")

    business_total = report.get("business_total", 0)
    personal_total = report.get("personal_total", 0)
    untagged_total = report.get("untagged_total", 0)
    business_income_total = report.get("business_income_total", 0)
    personal_income_total = report.get("personal_income_total", 0)
    if business_total or personal_total or untagged_total:
        rows = [
            ["Business spend", f"{business_total:,.2f}"],
            ["Personal spend", f"{personal_total:,.2f}"],
        ]
        if untagged_total:
            rows.append(["Untagged (couldn't tell which)", f"{untagged_total:,.2f}"])
        if business_income_total or personal_income_total:
            rows.append(["Business money in", f"+{business_income_total:,.2f}"])
            rows.append(["Personal money in", f"+{personal_income_total:,.2f}"])
        body = [_simple_table(["Split", "Amount"], rows, [10 * cm, 5.6 * cm], styles, header_color=_PURPLE)]
        _section(flowables, "bizperso", "Business vs personal", styles, body)

    subscriptions = report.get("subscriptions") or []
    body = []
    if subscriptions:
        rows = [[s["vendor"], s["category"], f"{s['amount']:,.2f}"] for s in subscriptions]
        body = [_simple_table(["Vendor", "Category", "Amount"], rows, [7 * cm, 4.6 * cm, 4 * cm], styles)]
    _section(flowables, "subs", "Subscriptions", styles, body, "None detected this period.")

    recurring_investments = report.get("recurring_investments") or []
    body = []
    if recurring_investments:
        rows = [[s["vendor"], s["category"], f"{s['amount']:,.2f}"] for s in recurring_investments]
        body = [_simple_table(["Vendor", "Category", "Amount"], rows, [7 * cm, 4.6 * cm, 4 * cm], styles)]
    _section(flowables, "invest", "Recurring investments", styles, body, "None detected this period.")

    payments_pending = report.get("payments_pending") or []
    if payments_pending:
        rows = [[p["vendor"], p["category"], p.get("date", ""), f"{p['amount']:,.2f}"] for p in payments_pending]
        body = [_simple_table(
            ["Vendor", "Category", "Date", "Amount due"], rows,
            [5.5 * cm, 3.8 * cm, 2.8 * cm, 3.5 * cm], styles, header_color=_AMBER,
        )]
        _section(flowables, "pending", "Payments pending (not yet paid, not in total spent)", styles, body)

    income_breakdown = report.get("income_breakdown") or {}
    if income_breakdown:
        rows = [[cat, f"+{amount:,.2f}"] for cat, amount in sorted(income_breakdown.items(), key=lambda kv: -kv[1])]
        body = [_simple_table(["Category", "Amount"], rows, [10 * cm, 5.6 * cm], styles)]
        _section(
            flowables, "income",
            f"Money in this period (total: {report.get('total_income', 0):,.2f}, not spend)",
            styles, body,
        )

    over_budget_categories = report.get("over_budget_categories") or []
    if over_budget_categories:
        body = [Paragraph("&bull;&nbsp;&nbsp;" + _md_inline_to_rl(cat), styles["bullet"]) for cat in over_budget_categories]
        _section(flowables, "budget", "Over budget", styles, body)

    if narrative_markdown and narrative_markdown.strip():
        flowables.append(PageBreak())
        flowables.append(_Bookmark("notes", "Recommendations & notes", level=0))
        flowables.append(Paragraph("Recommendations & notes", styles["h2"]))
        flowables.append(Paragraph("From your last ReconAI chat reply, included as-is.", styles["muted"]))
        flowables.append(HRFlowable(width="100%", thickness=0.75, color=_BORDER, spaceAfter=8))
        flowables.extend(_narrative_to_flowables(narrative_markdown, styles, bookmark_prefix="note"))

    doc.build(flowables)
    return buffer.getvalue()
