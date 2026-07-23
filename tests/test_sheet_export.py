"""Tests for tools/reporting_tools.py's save_report_to_sheet — the automatic
first-tab export (distinct from the still-dormant Sheets ledger). Covers:
writing to whichever tab is actually first (by position, not by name),
overwrite-not-append semantics (clear before update), formula-injection
sanitizing of untrusted vendor/category text, and failure handling.
"""

from types import SimpleNamespace

from tools.reporting_tools import generate_monthly_report, save_report_to_sheet


class _FakeValues:
    def __init__(self):
        self.clear_calls = []
        self.update_calls = []

    def clear(self, spreadsheetId, range, body):
        self.clear_calls.append((spreadsheetId, range))
        return SimpleNamespace(execute=lambda: {})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.update_calls.append((spreadsheetId, range, valueInputOption, body))
        return SimpleNamespace(execute=lambda: {"updatedCells": 1})


class _FakeSpreadsheets:
    def __init__(self, existing_tabs):
        self._existing_tabs = existing_tabs
        self._values = _FakeValues()

    def get(self, spreadsheetId, fields=None):
        sheets = [{"properties": {"title": t}} for t in self._existing_tabs]
        return SimpleNamespace(execute=lambda: {"sheets": sheets})

    def values(self):
        return self._values


class _FakeSheetsService:
    def __init__(self, existing_tabs=()):
        self._spreadsheets = _FakeSpreadsheets(list(existing_tabs))

    def spreadsheets(self):
        return self._spreadsheets


def _sample_report():
    data = {
        "transactions": [
            {"vendor": "Anthropic PBC", "amount": 1899, "category": "SaaS",
             "date": "2026-07-05", "is_duplicate": False, "is_recurring": True,
             "gst_eligible_guess": True, "payment_status_guess": "paid"},
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    return generate_monthly_report(data)


def test_save_report_writes_to_first_tab_regardless_of_its_name(monkeypatch):
    fake_service = _FakeSheetsService(existing_tabs=["Sheet1", "Other"])
    monkeypatch.setattr("tools.reporting_tools.get_service", lambda *a, **k: fake_service)

    result = save_report_to_sheet(_sample_report(), sheet_id="sheet-123")

    assert result["status"] == "ok"
    assert result["tab"] == "Sheet1"
    values = fake_service.spreadsheets().values()
    assert values.clear_calls[0][1] == "'Sheet1'!A:Z"
    assert values.update_calls[0][1] == "'Sheet1'!A1"


def test_save_report_fails_gracefully_when_sheet_has_no_tabs(monkeypatch):
    fake_service = _FakeSheetsService(existing_tabs=[])
    monkeypatch.setattr("tools.reporting_tools.get_service", lambda *a, **k: fake_service)

    result = save_report_to_sheet(_sample_report(), sheet_id="sheet-123")

    assert result["status"] == "failed"
    assert "error" in result


def test_save_report_clears_before_writing_not_appending(monkeypatch):
    fake_service = _FakeSheetsService(existing_tabs=["Sheet1"])
    monkeypatch.setattr("tools.reporting_tools.get_service", lambda *a, **k: fake_service)

    save_report_to_sheet(_sample_report(), sheet_id="sheet-123")
    save_report_to_sheet(_sample_report(), sheet_id="sheet-123")

    values = fake_service.spreadsheets().values()
    assert len(values.clear_calls) == 2
    assert len(values.update_calls) == 2
    assert all(r == "'Sheet1'!A:Z" for _, r in values.clear_calls)


def test_save_report_sanitizes_untrusted_vendor_category_text(monkeypatch):
    fake_service = _FakeSheetsService(existing_tabs=["Sheet1"])
    monkeypatch.setattr("tools.reporting_tools.get_service", lambda *a, **k: fake_service)

    data = {
        "transactions": [
            {"vendor": "=cmd|'/bin/sh'", "amount": 500, "category": "Shopping",
             "date": "2026-07-01", "is_duplicate": False, "is_recurring": False,
             "gst_eligible_guess": False, "payment_status_guess": "paid"},
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    report = generate_monthly_report(data)
    save_report_to_sheet(report, sheet_id="sheet-123")

    values = fake_service.spreadsheets().values()
    written_rows = values.update_calls[0][3]["values"]
    flat_cells = [cell for row in written_rows for cell in row if isinstance(cell, str)]
    assert not any(cell.startswith("=") for cell in flat_cells)


def test_save_report_failure_returns_failed_status(monkeypatch):
    from googleapiclient.errors import HttpError

    def _raise(*a, **k):
        raise HttpError(SimpleNamespace(status=500, reason="error"), b"error")

    monkeypatch.setattr("tools.reporting_tools.get_service", _raise)

    result = save_report_to_sheet(_sample_report(), sheet_id="sheet-123")
    assert result["status"] == "failed"
    assert "error" in result
