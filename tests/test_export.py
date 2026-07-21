"""Tests for tools/export_tools.py's export_report_to_pdf — the "Export PDF"
button's underlying feature. Covers: a valid PDF is produced, every figure
in it matches the same report dict the dashboard renders from (never
recomputed), sections are skipped cleanly when empty, and the optional
chat-narrative appendix only appears when actually provided.
"""

from io import BytesIO

from pypdf import PdfReader

from tools.export_tools import export_report_to_pdf
from tools.reporting_tools import generate_monthly_report


def _txn(**overrides):
    base = {
        "vendor": "Test Vendor",
        "amount": 100.0,
        "category": "Shopping",
        "date": "2026-07-01",
        "is_duplicate": False,
        "is_recurring": False,
        "gst_eligible_guess": False,
        "payment_status_guess": "paid",
    }
    base.update(overrides)
    return base


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_export_produces_valid_pdf_with_matching_totals():
    data = {
        "transactions": [
            _txn(vendor="Anthropic PBC", amount=1899, category="SaaS", is_recurring=True, spend_type_guess="business"),
            _txn(vendor="BigBasket", amount=2200, category="Groceries", spend_type_guess="personal"),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    report = generate_monthly_report(data)
    pdf_bytes = export_report_to_pdf(report, transaction_count=len(data["transactions"]))

    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1

    text = _extract_text(pdf_bytes)
    assert f"{report['total_spent']:,.2f}" in text
    assert "Anthropic PBC" in text
    assert "BigBasket" in text
    assert "1,899.00" in text
    assert "2,200.00" in text


def test_export_has_bookmarks_for_navigation():
    data = {
        "transactions": [_txn()],
        "missing_invoices": [],
        "budget_summary": {},
    }
    report = generate_monthly_report(data)
    pdf_bytes = export_report_to_pdf(report, transaction_count=1)

    reader = PdfReader(BytesIO(pdf_bytes))
    outline_titles = [item.title for item in reader.outline if not isinstance(item, list)]
    assert "Where it went" in outline_titles


def test_export_skips_empty_sections_gracefully():
    data = {
        "transactions": [_txn(is_recurring=False)],  # no subscriptions, no investments, no pending
        "missing_invoices": [],
        "budget_summary": {},
    }
    report = generate_monthly_report(data)
    pdf_bytes = export_report_to_pdf(report, transaction_count=1)

    text = _extract_text(pdf_bytes)
    assert "None detected this period." in text


def test_export_omits_narrative_section_when_not_provided():
    data = {"transactions": [_txn()], "missing_invoices": [], "budget_summary": {}}
    report = generate_monthly_report(data)
    pdf_bytes = export_report_to_pdf(report, transaction_count=1, narrative_markdown=None)

    text = _extract_text(pdf_bytes)
    assert "Recommendations & notes" not in text


def test_export_includes_narrative_section_when_provided():
    data = {"transactions": [_txn()], "missing_invoices": [], "budget_summary": {}}
    report = generate_monthly_report(data)
    narrative = "## Recommendations\n\n- **Cut** subscription spend where possible.\n"
    pdf_bytes = export_report_to_pdf(report, transaction_count=1, narrative_markdown=narrative)

    text = _extract_text(pdf_bytes)
    assert "Recommendations & notes" in text
    assert "Cut subscription spend" in text


def test_export_includes_business_personal_split_when_present():
    data = {
        "transactions": [
            _txn(vendor="Google Cloud Platform", amount=500, category="SaaS", spend_type_guess="business"),
            _txn(vendor="Groceries Store", amount=300, category="Groceries", spend_type_guess="personal"),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    report = generate_monthly_report(data)
    pdf_bytes = export_report_to_pdf(report, transaction_count=2)

    text = _extract_text(pdf_bytes)
    assert "Business vs personal" in text
    assert "500.00" in text
    assert "300.00" in text
