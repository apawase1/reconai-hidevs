"""tests/test_extraction.py — covers tools/discovery_tools.py:
email body extraction, extract_invoice_data (Gemini call mocked, no
network), and parse_bank_csv against the one supported CSV format.
"""

import base64
import json
from datetime import datetime
from types import SimpleNamespace

from tools.discovery_tools import (
    EXPECTED_CSV_COLUMNS,
    EXTRACTION_SCHEMA,
    _default_query,
    _extract_email_body,
    _extract_pdf_text,
    _find_pdf_attachments,
    _strip_html,
    extract_invoice_data,
    fetch_invoice_emails,
    parse_bank_csv,
    unlock_pdf_attachment,
)


# --- email body extraction ---

def _b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def test_extract_email_body_prefers_plain_text():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Invoice total: 500")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>Invoice total: 500</p>")}},
        ],
    }
    assert _extract_email_body(payload) == "Invoice total: 500"


def test_extract_email_body_falls_back_to_stripped_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<b>Total</b>: <span>500</span>")}},
        ],
    }
    result = _extract_email_body(payload)
    assert "<" not in result
    assert "Total" in result and "500" in result


def test_strip_html_removes_script_and_style_blocks():
    html = "<style>.a{color:red}</style><p>Hello</p><script>evil()</script>"
    result = _strip_html(html)
    assert "evil()" not in result
    assert "color:red" not in result
    assert "Hello" in result


# --- extract_invoice_data (Gemini call mocked) ---

class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, canned_text):
        self._canned_text = canned_text

    def generate_content(self, model, contents, config):
        return _FakeResponse(self._canned_text)


class _FakeClient:
    def __init__(self, api_key, canned_text="[]"):
        self.models = _FakeModels(canned_text)


def test_extract_invoice_data_empty_input_short_circuits():
    result = extract_invoice_data([])
    assert result["status"] == "ok"
    assert result["transactions"] == []


def test_extract_invoice_data_parses_structured_output(monkeypatch):
    canned = json.dumps([
        {
            "source_id": "abc123",
            "vendor": "Amazon",
            "amount": 1499.0,
            "date": "2026-07-10",
            "category": "Shopping",
            "is_recurring_guess": False,
            "gst_eligible_guess": True,
            "status": "ok",
        }
    ])

    def fake_client(api_key):
        return _FakeClient(api_key, canned_text=canned)

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr("google.genai.Client", fake_client)

    result = extract_invoice_data([{"id": "abc123", "subject": "Your Amazon order", "body_text": "Total: 1499"}])
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["transactions"][0]["vendor"] == "Amazon"


def test_extraction_schema_includes_spend_type_guess():
    # Freelancers/small business owners mix business and personal spend in
    # one inbox - the schema must expose a per-transaction business/personal
    # tag (not required, same as category/is_recurring_guess/
    # gst_eligible_guess, since it's a guess Gemini may leave out on a
    # genuinely ambiguous item).
    props = EXTRACTION_SCHEMA["items"]["properties"]
    assert "spend_type_guess" in props
    assert set(props["spend_type_guess"]["enum"]) == {"business", "personal", "unknown"}
    assert "spend_type_guess" not in EXTRACTION_SCHEMA["items"]["required"]


def test_extract_invoice_data_missing_api_key_fails_gracefully(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    result = extract_invoice_data([{"id": "x", "body_text": "hi"}])
    assert result["status"] == "failed"
    assert "GOOGLE_API_KEY" in result["error"]


def test_extract_invoice_data_malformed_json_fails_gracefully(monkeypatch):
    def fake_client(api_key):
        return _FakeClient(api_key, canned_text="not valid json")

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr("google.genai.Client", fake_client)

    result = extract_invoice_data([{"id": "x", "body_text": "hi"}])
    assert result["status"] == "failed"
    assert result["transactions"] == []


# --- parse_bank_csv ---

def test_parse_bank_csv_success(tmp_path):
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text(
        "Date,Description,Amount,Type\n"
        "2026-07-01,Coffee Shop,150.0,debit\n"
        "2026-07-02,Salary,50000.0,credit\n"
    )
    result = parse_bank_csv(str(csv_path))
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["transactions"][0]["description"] == "Coffee Shop"


def test_parse_bank_csv_sanitizes_formula_injection(tmp_path):
    csv_path = tmp_path / "malicious.csv"
    csv_path.write_text(
        "Date,Description,Amount,Type\n"
        '2026-07-01,"=cmd|calc",150.0,debit\n'
    )
    result = parse_bank_csv(str(csv_path))
    assert result["transactions"][0]["description"].startswith("'=")


def test_parse_bank_csv_fails_clearly_on_wrong_format(tmp_path):
    csv_path = tmp_path / "wrong_format.csv"
    csv_path.write_text("Transaction Date,Details,Debit,Credit\n2026-07-01,Test,100,0\n")
    result = parse_bank_csv(str(csv_path))
    assert result["status"] == "failed"
    assert "EXPECTED_CSV_COLUMNS" in result["error"] or str(EXPECTED_CSV_COLUMNS) in result["error"]


# --- _default_query: date-scoped default (cost-reduction fix) ---

def test_default_query_scopes_to_first_of_current_month():
    query = _default_query()
    expected_date = datetime.now().replace(day=1).strftime("%Y/%m/%d")
    assert f"after:{expected_date}" in query
    assert "invoice" in query.lower()


def test_default_query_excludes_sent_but_not_archived_inbox():
    # Regression test: an earlier version used `in:inbox`, which fixed Sent
    # items leaking in but also silently dropped legitimately received mail
    # that Gmail had auto-archived out of the inbox (e.g. via a filter) —
    # `-in:sent` excludes outgoing mail without that side effect.
    query = _default_query()
    assert "-in:sent" in query
    assert "in:inbox" not in query


# --- fetch_invoice_emails: exclude_ids filtering (cost-reduction fix) ---

class _FakeAttachmentsResource:
    def __init__(self, attachments_by_id):
        self._attachments_by_id = attachments_by_id
        self.get_calls = []

    def get(self, userId, messageId, id):
        self.get_calls.append((messageId, id))
        return SimpleNamespace(execute=lambda: {"data": self._attachments_by_id[id]})


class _FakeMessagesResource:
    def __init__(self, message_ids, details_by_id, attachments_by_id=None):
        self._message_ids = message_ids
        self._details_by_id = details_by_id
        self._attachments_resource = _FakeAttachmentsResource(attachments_by_id or {})
        self.get_calls = []

    def list(self, userId, q, maxResults):
        return SimpleNamespace(execute=lambda: {"messages": [{"id": mid} for mid in self._message_ids]})

    def get(self, userId, id, format):
        self.get_calls.append(id)
        return SimpleNamespace(execute=lambda: self._details_by_id[id])

    def attachments(self):
        return self._attachments_resource


class _FakeUsers:
    def __init__(self, messages_resource):
        self._messages_resource = messages_resource

    def messages(self):
        return self._messages_resource


class _FakeGmailService:
    def __init__(self, messages_resource):
        self._users = _FakeUsers(messages_resource)

    def users(self):
        return self._users


def _fake_email_detail(subject_text):
    return {
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject_text},
                {"name": "From", "value": "vendor@example.com"},
                {"name": "Date", "value": "Sat, 18 Jul 2026 10:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("Amount due: 500")},
        }
    }


def test_fetch_invoice_emails_skips_excluded_ids(monkeypatch):
    details = {
        "msg-1": _fake_email_detail("Invoice A"),
        "msg-2": _fake_email_detail("Invoice B"),
    }
    messages_resource = _FakeMessagesResource(["msg-1", "msg-2"], details)
    fake_service = _FakeGmailService(messages_resource)

    monkeypatch.setattr("tools.discovery_tools.get_service", lambda *a, **k: fake_service)

    result = fetch_invoice_emails(query="invoice", exclude_ids=["msg-1"])

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["emails"][0]["id"] == "msg-2"
    assert result["skipped_already_processed"] == 1
    # The excluded message must never even get a detail fetch — that's the
    # whole point (skip the Gmail call, not just the extraction call).
    assert "msg-1" not in messages_resource.get_calls


def test_fetch_invoice_emails_no_exclusions_fetches_everything(monkeypatch):
    details = {"msg-1": _fake_email_detail("Invoice A")}
    messages_resource = _FakeMessagesResource(["msg-1"], details)
    fake_service = _FakeGmailService(messages_resource)

    monkeypatch.setattr("tools.discovery_tools.get_service", lambda *a, **k: fake_service)

    result = fetch_invoice_emails(query="invoice")

    assert result["count"] == 1
    assert result["skipped_already_processed"] == 0


def test_fetch_invoice_emails_concurrent_fetch_preserves_order_and_count(monkeypatch):
    # fetch_invoice_emails fetches per-message detail concurrently via a
    # thread pool (see DISCOVERY_FETCH_WORKERS) instead of one at a time -
    # this checks that concurrency doesn't drop messages or scramble the
    # result order despite threads finishing in whatever order they finish.
    ids = [f"msg-{i}" for i in range(6)]
    details = {mid: _fake_email_detail(f"Invoice {mid}") for mid in ids}
    messages_resource = _FakeMessagesResource(ids, details)
    fake_service = _FakeGmailService(messages_resource)

    monkeypatch.setattr("tools.discovery_tools.get_service", lambda *a, **k: fake_service)

    result = fetch_invoice_emails(query="invoice")

    assert result["status"] == "ok"
    assert result["count"] == 6
    assert [e["id"] for e in result["emails"]] == ids


def test_fetch_invoice_emails_defaults_to_date_scoped_query(monkeypatch):
    captured = {}

    class _CapturingMessagesResource(_FakeMessagesResource):
        def list(self, userId, q, maxResults):
            captured["q"] = q
            return super().list(userId, q, maxResults)

    messages_resource = _CapturingMessagesResource([], {})
    fake_service = _FakeGmailService(messages_resource)
    monkeypatch.setattr("tools.discovery_tools.get_service", lambda *a, **k: fake_service)

    fetch_invoice_emails()

    assert "after:" in captured["q"]


# --- password-protected PDF attachments ---

def test_extract_pdf_text_reports_password_required(monkeypatch):
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    def fake_open(*args, **kwargs):
        raise PDFPasswordIncorrect("wrong password")

    monkeypatch.setattr("pdfplumber.open", fake_open)
    result = _extract_pdf_text(b"fake-pdf-bytes", password="wrong-guess")
    assert result["status"] == "password_required"
    assert "text" in result and result["text"] == ""


def test_extract_pdf_text_reports_password_required_for_generic_encryption_error(monkeypatch):
    # Regression test for a real bug found against an actual bank-issued
    # locked PDF: it raised PDFEncryptionError (the parent class), not the
    # narrower PDFPasswordIncorrect subclass the code used to check for
    # specifically — so it fell through to a generic "failed" status and
    # Discovery silently moved on instead of ever asking for a password.
    from pdfminer.pdfdocument import PDFEncryptionError

    def fake_open(*args, **kwargs):
        raise PDFEncryptionError("unsupported encryption revision")

    monkeypatch.setattr("pdfplumber.open", fake_open)
    result = _extract_pdf_text(b"fake-pdf-bytes")
    assert result["status"] == "password_required"


def test_find_pdf_attachments_detects_via_mime_type_without_pdf_suffix():
    # Regression test: some senders attach a PDF whose filename has no
    # ".pdf" suffix at all (no extension, or a generic name like
    # "Attachment") — a suffix-only check misses it completely, not even
    # as a "failed" item, just silently invisible.
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": ""}},
            {
                "filename": "Statement",
                "mimeType": "application/pdf",
                "body": {"attachmentId": "att-no-suffix"},
            },
        ],
    }
    found = _find_pdf_attachments(payload)
    assert len(found) == 1
    assert found[0]["attachment_id"] == "att-no-suffix"
    assert found[0]["filename"] == "Statement"


def _build_real_encrypted_pdf(user_password: str, text: str) -> bytes:
    """Builds an actually-encrypted PDF in memory (no monkeypatching) so
    the password tests exercise real pdfplumber/pdfminer behavior, not an
    idealized mock of it."""
    import io as _io

    from pypdf import PdfWriter
    from reportlab.pdfgen import canvas

    buf = _io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 700, text)
    c.save()
    buf.seek(0)

    from pypdf import PdfReader
    reader = PdfReader(buf)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=user_password, owner_password="owner-pw")

    out = _io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_extract_pdf_text_real_encrypted_pdf_reports_password_required_no_password():
    # Regression test for a real bug: pdfplumber >=0.11 wraps pdfminer's
    # PDFPasswordIncorrect in its own PdfminerException, so a bare
    # `except PDFPasswordIncorrect` never actually caught it against a real
    # locked PDF - it fell through to status "failed" and the password-ask
    # flow in agents.py never triggered. This test uses REAL encrypted PDF
    # bytes (no monkeypatching pdfplumber.open) so it can't be fooled by an
    # idealized mock of the exception - it would have caught the bug.
    raw = _build_real_encrypted_pdf("realpassword123", "Secret invoice text")
    result = _extract_pdf_text(raw)
    assert result["status"] == "password_required"


def test_extract_pdf_text_real_encrypted_pdf_wrong_password():
    raw = _build_real_encrypted_pdf("realpassword123", "Secret invoice text")
    result = _extract_pdf_text(raw, password="totally-wrong-guess")
    assert result["status"] == "password_required"


def test_extract_pdf_text_real_encrypted_pdf_correct_password():
    raw = _build_real_encrypted_pdf("realpassword123", "Secret invoice text")
    result = _extract_pdf_text(raw, password="realpassword123")
    assert result["status"] == "ok"
    assert "Secret invoice text" in result["text"]


class _FakePdfContext:
    """pdfplumber.open()'s return value is used as a context manager
    (`with pdfplumber.open(...) as pdf:`), and Python looks up __enter__/
    __exit__ on the *type*, not the instance — so a SimpleNamespace with
    those set as instance attributes won't work here. A tiny real class
    does."""

    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_extract_pdf_text_succeeds_with_correct_password(monkeypatch):
    fake_page = SimpleNamespace(extract_text=lambda: "Invoice total: 999")
    fake_pdf = _FakePdfContext(pages=[fake_page])

    monkeypatch.setattr("pdfplumber.open", lambda *a, **k: fake_pdf)
    result = _extract_pdf_text(b"fake-pdf-bytes", password="correct-horse-battery-staple")
    assert result["status"] == "ok"
    assert "999" in result["text"]


def test_extract_pdf_text_treats_empty_text_as_failed_not_password(monkeypatch):
    fake_page = SimpleNamespace(extract_text=lambda: "")
    fake_pdf = _FakePdfContext(pages=[fake_page])

    monkeypatch.setattr("pdfplumber.open", lambda *a, **k: fake_pdf)
    result = _extract_pdf_text(b"fake-pdf-bytes")
    assert result["status"] == "failed"
    assert "scanned" in result["error"].lower()


def test_unlock_pdf_attachment_returns_recovered_text_on_success(monkeypatch):
    monkeypatch.setattr("tools.discovery_tools.get_service", lambda *a, **k: object())
    monkeypatch.setattr("tools.discovery_tools._fetch_attachment_bytes", lambda service, mid, aid: b"bytes")
    monkeypatch.setattr(
        "tools.discovery_tools._extract_pdf_text",
        lambda raw_bytes, password=None: {"status": "ok", "text": "Vendor: Acme, Amount: 500"},
    )

    result = unlock_pdf_attachment("msg-1", "att-1", "correct-password")
    assert result["status"] == "ok"
    assert "Acme" in result["text"]


def test_unlock_pdf_attachment_reports_still_locked_on_wrong_password(monkeypatch):
    monkeypatch.setattr("tools.discovery_tools.get_service", lambda *a, **k: object())
    monkeypatch.setattr("tools.discovery_tools._fetch_attachment_bytes", lambda service, mid, aid: b"bytes")
    monkeypatch.setattr(
        "tools.discovery_tools._extract_pdf_text",
        lambda raw_bytes, password=None: {"status": "password_required", "text": "", "error": "PDF is password-protected."},
    )

    result = unlock_pdf_attachment("msg-1", "att-1", "wrong-password")
    assert result["status"] == "password_required"
    assert "still locked" in result["error"].lower()


def test_fetch_invoice_emails_surfaces_locked_pdf_attachment(monkeypatch):
    email_detail = _fake_email_detail("Invoice with attachment")
    email_detail["payload"]["parts"] = [
        {"mimeType": "text/plain", "body": {"data": _b64("See attached invoice")}},
        {"filename": "invoice.pdf", "body": {"attachmentId": "att-locked"}},
    ]
    # top-level payload no longer has direct body.data once it has parts-only structure
    email_detail["payload"].pop("body", None)

    messages_resource = _FakeMessagesResource(
        ["msg-1"], {"msg-1": email_detail}, attachments_by_id={"att-locked": _b64("encrypted-pdf-bytes")}
    )
    fake_service = _FakeGmailService(messages_resource)
    monkeypatch.setattr("tools.discovery_tools.get_service", lambda *a, **k: fake_service)

    from pdfminer.pdfdocument import PDFPasswordIncorrect
    monkeypatch.setattr("pdfplumber.open", lambda *a, **k: (_ for _ in ()).throw(PDFPasswordIncorrect("locked")))

    result = fetch_invoice_emails(query="invoice")

    assert result["status"] == "ok"  # a locked attachment is not a crash/skip, just a flagged item
    assert len(result["locked_attachments"]) == 1
    locked = result["locked_attachments"][0]
    assert locked["filename"] == "invoice.pdf"
    assert locked["message_id"] == "msg-1"
    assert locked["attachment_id"] == "att-locked"

    email = result["emails"][0]
    assert email["attachments"][0]["status"] == "password_required"
