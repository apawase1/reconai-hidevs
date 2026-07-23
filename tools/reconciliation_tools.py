"""tools/reconciliation_tools.py — Reconciliation Agent toolset: deterministic dedup, missing-invoice, budget, and recurring checks."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError

from tools.google_auth import get_service
from tools.security import sanitize_csv_cell

LEDGER_TAB = "Ledger"
PROCESSED_IDS_TAB = "_processed_ids"
DUPLICATE_AMOUNT_TOLERANCE = 0.01
DUPLICATE_DATE_WINDOW_DAYS = 3


def _parse_date(value: str) -> Optional[datetime]:
    """Parses a date string against a fixed list of accepted formats."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _normalize_vendor(vendor: str) -> str:
    """Lowercases and strips non-alphanumeric characters for vendor-name matching."""
    return "".join(ch.lower() for ch in (vendor or "") if ch.isalnum())


def check_duplicates_and_budget(
    transactions: List[Dict[str, Any]],
    bank_transactions: Optional[List[Dict[str, Any]]] = None,
    budget: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Runs deterministic dedup, missing-invoice, budget, and recurring checks over a batch of extracted transactions.

    Args:
        transactions: List of transaction dicts (from extract_invoice_data).
        bank_transactions: Optional bank CSV transaction dicts (from parse_bank_csv).
        budget: Optional dict mapping category name -> monthly budget amount.

    Returns:
        dict with keys: status, transactions (enriched with is_duplicate,
        duplicate_of, is_recurring), missing_invoices, budget_summary.
    """
    enriched = [dict(t) for t in transactions]

    seen_keys = []
    for idx, t in enumerate(enriched):
        vendor_norm = _normalize_vendor(t.get("vendor", ""))
        amount = t.get("amount") or 0
        date_obj = _parse_date(str(t.get("date", "")))

        duplicate_of = None
        for other_vendor, other_amount, other_date, other_idx in seen_keys:
            if other_vendor != vendor_norm:
                continue
            if abs(other_amount - amount) > DUPLICATE_AMOUNT_TOLERANCE:
                continue
            if date_obj and other_date:
                if abs((date_obj - other_date).days) > DUPLICATE_DATE_WINDOW_DAYS:
                    continue
            duplicate_of = other_idx
            break

        enriched[idx]["is_duplicate"] = duplicate_of is not None
        enriched[idx]["duplicate_of"] = duplicate_of
        seen_keys.append((vendor_norm, amount, date_obj, idx))

    vendor_months: Dict[str, set] = {}
    for t in enriched:
        vendor_norm = _normalize_vendor(t.get("vendor", ""))
        date_obj = _parse_date(str(t.get("date", "")))
        month_key = (date_obj.year, date_obj.month) if date_obj else None
        key = (vendor_norm, round(float(t.get("amount") or 0), 2))
        vendor_months.setdefault(key, set())
        if month_key:
            vendor_months[key].add(month_key)

    for t in enriched:
        vendor_norm = _normalize_vendor(t.get("vendor", ""))
        key = (vendor_norm, round(float(t.get("amount") or 0), 2))
        t["is_recurring"] = len(vendor_months.get(key, set())) >= 2 or t.get("is_recurring_guess", False)

    missing_invoices = []
    if bank_transactions:
        invoice_keys = {
            (_normalize_vendor(t.get("vendor", "")), round(float(t.get("amount") or 0), 2))
            for t in enriched
        }
        for bt in bank_transactions:
            if str(bt.get("type", "")).lower() not in ("debit", "withdrawal"):
                continue
            desc_norm = _normalize_vendor(bt.get("description", ""))
            amount = round(float(bt.get("amount") or 0), 2)
            matched = any(
                inv_vendor and inv_vendor in desc_norm and inv_amount == amount
                for inv_vendor, inv_amount in invoice_keys
            )
            if not matched:
                missing_invoices.append(bt)

    budget_summary: Dict[str, Any] = {}
    if budget:
        spent_by_category: Dict[str, float] = {}
        for t in enriched:
            cat = t.get("category") or "Uncategorized"
            spent_by_category[cat] = spent_by_category.get(cat, 0) + float(t.get("amount") or 0)
        for cat, cap in budget.items():
            spent = spent_by_category.get(cat, 0)
            budget_summary[cat] = {"spent": round(spent, 2), "budget": cap, "over_budget": spent > cap}

    return {
        "status": "ok",
        "transactions": enriched,
        "missing_invoices": missing_invoices,
        "budget_summary": budget_summary,
    }


def append_to_ledger(transactions: List[Dict[str, Any]], sheet_id: str) -> Dict[str, Any]:
    """Writes reconciled transactions to the Google Sheet ledger, sanitizing every cell first.

    Args:
        transactions: List of transaction dicts.
        sheet_id: The target Google Sheet's ID.

    Returns:
        dict with keys: status, rows_written, error (only if failed).
    """
    if not transactions:
        return {"status": "ok", "rows_written": 0}

    try:
        service = get_service("sheets", "v4")
        rows = []
        for t in transactions:
            rows.append([
                sanitize_csv_cell(str(t.get("date", ""))),
                sanitize_csv_cell(str(t.get("vendor", ""))),
                t.get("amount", 0),
                sanitize_csv_cell(str(t.get("category", ""))),
                bool(t.get("is_duplicate", False)),
                bool(t.get("is_recurring", False)),
                bool(t.get("gst_eligible_guess", t.get("gst_eligible", False))),
                sanitize_csv_cell(str(t.get("source_id", ""))),
            ])

        body = {"values": rows}
        result = service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{LEDGER_TAB}!A:H",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()

        return {"status": "ok", "rows_written": len(rows), "updates": result.get("updates", {})}
    except HttpError as e:
        return {"status": "failed", "rows_written": 0, "error": str(e)}


def get_processed_ids(sheet_id: str) -> Dict[str, Any]:
    """Reads the _processed_ids tab so a re-run doesn't duplicate work already done.

    Args:
        sheet_id: The target Google Sheet's ID.

    Returns:
        dict with keys: status, ids, error (only if failed).
    """
    try:
        service = get_service("sheets", "v4")
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{PROCESSED_IDS_TAB}!A:A"
        ).execute()
        rows = result.get("values", [])
        ids = [row[0] for row in rows if row]
        return {"status": "ok", "ids": ids}
    except HttpError as e:
        if "Unable to parse range" in str(e) or "not found" in str(e).lower():
            return {"status": "ok", "ids": []}
        return {"status": "failed", "ids": [], "error": str(e)}


def mark_processed(ids: List[str], sheet_id: str) -> Dict[str, Any]:
    """Appends newly processed source IDs to the _processed_ids tab.

    Args:
        ids: List of source_id strings just written to the ledger.
        sheet_id: The target Google Sheet's ID.

    Returns:
        dict with keys: status, ids_written, error (only if failed).
    """
    if not ids:
        return {"status": "ok", "ids_written": 0}
    try:
        service = get_service("sheets", "v4")
        body = {"values": [[sanitize_csv_cell(str(i))] for i in ids]}
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{PROCESSED_IDS_TAB}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()
        return {"status": "ok", "ids_written": len(ids)}
    except HttpError as e:
        return {"status": "failed", "ids_written": 0, "error": str(e)}
