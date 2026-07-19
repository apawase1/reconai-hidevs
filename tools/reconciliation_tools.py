"""tools/reconciliation_tools.py — Reconciliation Agent toolset.

Per the architecture doc: dedup, missing-invoice check, budget compare, and
category/recurring/GST tagging happen here. Deterministic math (exact dedup
matching, exact budget arithmetic) lives in Python, not in an LLM's head —
the model's job is the judgment calls layered on top of these tool outputs
(is this *probably* the same subscription, is this *plausibly* GST-eligible),
not the arithmetic itself (section 8).

Long-term memory (which transactions were already processed) is a
`_processed_ids` tab in the same Google Sheet — not ADK's MemoryService
(section 2). get_processed_ids / mark_processed implement that.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError

from tools.google_auth import get_service
from tools.security import sanitize_csv_cell

LEDGER_TAB = "Ledger"
PROCESSED_IDS_TAB = "_processed_ids"
DUPLICATE_AMOUNT_TOLERANCE = 0.01  # currency rounding slack
DUPLICATE_DATE_WINDOW_DAYS = 3


def _parse_date(value: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _normalize_vendor(vendor: str) -> str:
    return "".join(ch.lower() for ch in (vendor or "") if ch.isalnum())


def check_duplicates_and_budget(
    transactions: List[Dict[str, Any]],
    bank_transactions: Optional[List[Dict[str, Any]]] = None,
    budget: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Runs deterministic dedup, missing-invoice, budget, and recurring
    checks over a batch of extracted transactions.

    Call this once per Discovery output batch, after extract_invoice_data
    and (optionally) parse_bank_csv have both run. This does not call an
    LLM — it's exact matching and arithmetic, not judgment.

    Args:
        transactions: List of transaction dicts (from extract_invoice_data),
            each with keys vendor, amount, date, category.
        bank_transactions: Optional list of bank CSV transaction dicts (from
            parse_bank_csv) used to flag bank debits with no matching
            invoice/receipt as missing_invoice.
        budget: Optional dict mapping category name -> monthly budget amount.
            If omitted, budget_summary is returned empty and no
            over_budget flags are set.

    Returns:
        dict with keys: status ("ok"), transactions (input list enriched
        with is_duplicate, duplicate_of, is_recurring bools), missing_invoices
        (list of bank_transactions with no matching invoice), budget_summary
        (dict of category -> {spent, budget, over_budget}).
    """
    enriched = [dict(t) for t in transactions]

    # --- Duplicate detection: same normalized vendor + amount within a
    # small date window is treated as the same charge seen twice. ---
    seen_keys = []  # list of (vendor_norm, amount, date_obj, index)
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

    # --- Recurring detection: same vendor + amount appearing across >=2
    # distinct months anywhere in the batch. ---
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

    # --- Missing invoice: bank debits with no matching invoice/receipt. ---
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

    # --- Budget comparison, per category. ---
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
    """Writes reconciled transactions to the Google Sheet ledger.

    Call this after reconciliation is complete, once per batch of
    transactions — do not call it per individual transaction. Every cell is
    sanitized against CSV/Sheets formula injection before being written,
    since the source data ultimately traces back to untrusted email/CSV
    content.

    Args:
        transactions: List of transaction dicts, each with keys vendor,
            amount, date, category, is_duplicate, is_recurring,
            gst_eligible_guess (or gst_eligible).
        sheet_id: The target Google Sheet's ID.

    Returns:
        dict with keys: status ("ok"/"failed"), rows_written (int),
        error (str, only present if status is "failed").
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
    """Reads the _processed_ids tab so a re-run doesn't duplicate work
    already done in a prior run (the cross-run "memory" per section 2).

    Args:
        sheet_id: The target Google Sheet's ID.

    Returns:
        dict with keys: status ("ok"/"failed"), ids (list of str),
        error (str, only present if status is "failed").
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
        # A missing tab isn't fatal — treat as "no ids processed yet".
        if "Unable to parse range" in str(e) or "not found" in str(e).lower():
            return {"status": "ok", "ids": []}
        return {"status": "failed", "ids": [], "error": str(e)}


def mark_processed(ids: List[str], sheet_id: str) -> Dict[str, Any]:
    """Appends newly processed source IDs to the _processed_ids tab.

    Call this once per run, after append_to_ledger succeeds, with every
    source_id that was written — not per individual ID.

    Args:
        ids: List of source_id strings that were just written to the ledger.
        sheet_id: The target Google Sheet's ID.

    Returns:
        dict with keys: status ("ok"/"failed"), ids_written (int),
        error (str, only present if status is "failed").
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
