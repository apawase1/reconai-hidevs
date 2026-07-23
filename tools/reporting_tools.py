"""tools/reporting_tools.py — Reporting Agent toolset: summarizes, answers NL questions, generates recommendations."""

from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError

from tools.google_auth import get_service
from tools.reconciliation_tools import LEDGER_TAB
from tools.security import sanitize_csv_cell

LEDGER_COLUMNS = [
    "date", "vendor", "amount", "category",
    "is_duplicate", "is_recurring", "gst_eligible", "source_id",
]

_INVESTMENT_CATEGORY_HINTS = (
    "invest", "sip", "mutual fund", "recurring deposit", "fixed deposit",
    "gold", "stock", "equity", "share", "demat", "etf", "bond",
    "ppf", "nps", "elss", "commodit",
)
_INVESTMENT_GAIN_HINTS = ("investment gain", "capital gain", "redemption", "maturity")
_INCOME_CATEGORY_HINTS = (
    "income", "dividend", "interest", "salary income", "salary credit",
    "refund", "cashback", "client payment",
)
_HOUSING_CATEGORY_HINTS = ("rent", "housing", "lease", "mortgage")


def _is_investment_category(category: str) -> bool:
    """True for a category representing an investment contribution/debit."""
    cat = (category or "").lower()
    return any(hint in cat for hint in _INVESTMENT_CATEGORY_HINTS)


def _is_housing_category(category: str) -> bool:
    """True for a rent/housing category, excluded from the subscriptions list."""
    cat = (category or "").lower()
    return any(hint in cat for hint in _HOUSING_CATEGORY_HINTS)


def _is_investment_gain_category(category: str) -> bool:
    """True for money coming back in from an investment (redemption/maturity/gains)."""
    cat = (category or "").lower()
    return any(hint in cat for hint in _INVESTMENT_GAIN_HINTS)


def _is_income_category(category: str) -> bool:
    """True for a salary/interest/dividend/refund income category."""
    cat = (category or "").lower()
    return any(hint in cat for hint in _INCOME_CATEGORY_HINTS)


def _is_inflow_category(category: str) -> bool:
    """True for any category representing money coming in rather than being spent."""
    return _is_income_category(category) or _is_investment_gain_category(category)


def _spend_type(t: Dict[str, Any]) -> str:
    """Normalizes a transaction's spend_type_guess to "business", "personal", or "untagged"."""
    value = (t.get("spend_type_guess") or "").strip().lower()
    return value if value in ("business", "personal") else "untagged"


def _dedupe_recurring(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapses recurring transactions to one row per distinct vendor+amount."""
    seen = {}
    for t in items:
        key = ((t.get("vendor") or "").strip().lower(), round(float(t.get("amount") or 0), 2))
        if key not in seen:
            seen[key] = {
                "vendor": t.get("vendor") or "Unknown",
                "amount": round(float(t.get("amount") or 0), 2),
                "category": t.get("category") or "Uncategorized",
            }
    return sorted(seen.values(), key=lambda v: -v["amount"])


def generate_monthly_report(reconciled_data: Dict[str, Any]) -> Dict[str, Any]:
    """Builds the monthly reconciliation summary (totals, breakdowns, subscriptions, report text) from reconciled data."""
    transactions = reconciled_data.get("transactions", [])
    missing_invoices = reconciled_data.get("missing_invoices", [])
    budget_summary = reconciled_data.get("budget_summary", {})

    duplicate_count = sum(1 for t in transactions if t.get("is_duplicate"))
    unique_transactions = [t for t in transactions if not t.get("is_duplicate")]

    pending_transactions = [t for t in unique_transactions if t.get("payment_status_guess") == "pending"]
    paid_transactions = [t for t in unique_transactions if t.get("payment_status_guess") != "pending"]

    outflow_transactions = [t for t in paid_transactions if not _is_inflow_category(t.get("category"))]
    inflow_transactions = [t for t in paid_transactions if _is_inflow_category(t.get("category"))]

    total_spent = sum(float(t.get("amount") or 0) for t in outflow_transactions)
    category_breakdown: Dict[str, float] = {}
    category_vendors: Dict[str, List[str]] = {}
    for t in outflow_transactions:
        cat = t.get("category") or "Uncategorized"
        category_breakdown[cat] = category_breakdown.get(cat, 0) + float(t.get("amount") or 0)
        vendor = t.get("vendor") or "Unknown"
        vendors = category_vendors.setdefault(cat, [])
        if vendor not in vendors:
            vendors.append(vendor)

    total_income = sum(float(t.get("amount") or 0) for t in inflow_transactions)
    income_breakdown: Dict[str, float] = {}
    for t in inflow_transactions:
        cat = t.get("category") or "Uncategorized"
        income_breakdown[cat] = income_breakdown.get(cat, 0) + float(t.get("amount") or 0)

    business_total = sum(float(t.get("amount") or 0) for t in outflow_transactions if _spend_type(t) == "business")
    personal_total = sum(float(t.get("amount") or 0) for t in outflow_transactions if _spend_type(t) == "personal")
    untagged_total = sum(float(t.get("amount") or 0) for t in outflow_transactions if _spend_type(t) == "untagged")

    business_category_breakdown: Dict[str, float] = {}
    personal_category_breakdown: Dict[str, float] = {}
    for t in outflow_transactions:
        cat = t.get("category") or "Uncategorized"
        amount = float(t.get("amount") or 0)
        spend_type = _spend_type(t)
        if spend_type == "business":
            business_category_breakdown[cat] = business_category_breakdown.get(cat, 0) + amount
        elif spend_type == "personal":
            personal_category_breakdown[cat] = personal_category_breakdown.get(cat, 0) + amount

    business_income_total = sum(float(t.get("amount") or 0) for t in inflow_transactions if _spend_type(t) == "business")
    personal_income_total = sum(float(t.get("amount") or 0) for t in inflow_transactions if _spend_type(t) == "personal")
    untagged_income_total = sum(float(t.get("amount") or 0) for t in inflow_transactions if _spend_type(t) == "untagged")

    recurring_count = sum(1 for t in outflow_transactions if t.get("is_recurring"))
    gst_eligible_total = sum(
        float(t.get("amount") or 0)
        for t in outflow_transactions
        if t.get("gst_eligible_guess") or t.get("gst_eligible")
    )
    over_budget_categories = [cat for cat, v in budget_summary.items() if v.get("over_budget")]

    recurring_paid = [
        t for t in outflow_transactions
        if t.get("is_recurring") and not _is_housing_category(t.get("category"))
    ]
    recurring_investments = _dedupe_recurring([t for t in recurring_paid if _is_investment_category(t.get("category"))])
    subscriptions = _dedupe_recurring([t for t in recurring_paid if not _is_investment_category(t.get("category"))])

    payments_pending = [
        {
            "vendor": t.get("vendor") or "Unknown",
            "amount": round(float(t.get("amount") or 0), 2),
            "category": t.get("category") or "Uncategorized",
            "date": t.get("date", ""),
        }
        for t in pending_transactions
    ]

    lines = [
        "# Monthly Reconciliation Report",
        "",
        f"**Total spent:** {total_spent:,.2f}",
        f"**Transactions processed:** {len(transactions)}",
        f"**Duplicates flagged (excluded from totals above):** {duplicate_count}",
        f"**Missing invoices:** {len(missing_invoices)}",
        f"**Recurring charges:** {recurring_count}",
        f"**GST-eligible total:** {gst_eligible_total:,.2f}",
        "",
        "## Where it went (by category)",
    ]
    if category_breakdown:
        grand_total = sum(category_breakdown.values()) or 1
        for cat, amount in sorted(category_breakdown.items(), key=lambda kv: -kv[1]):
            pct = round(amount / grand_total * 100)
            vendors = ", ".join(category_vendors.get(cat, []))
            lines.append(f"- **{cat}:** {amount:,.2f} ({pct}%) — {vendors}")
    else:
        lines.append("- No categorized spend this period.")

    lines += ["", "## Subscriptions you have"]
    if subscriptions:
        for s in subscriptions:
            lines.append(f"- **{s['vendor']}:** {s['amount']:,.2f} ({s['category']})")
    else:
        lines.append("- None detected this period.")

    lines += ["", "## Recurring investments"]
    if recurring_investments:
        for s in recurring_investments:
            lines.append(f"- **{s['vendor']}:** {s['amount']:,.2f} ({s['category']})")
    else:
        lines.append("- None detected this period.")

    if payments_pending:
        lines += ["", "## Payments pending (not yet paid, not included in total spent)"]
        for p in payments_pending:
            lines.append(f"- **{p['vendor']}:** {p['amount']:,.2f} due — {p['category']} ({p['date']})")

    if income_breakdown:
        lines += ["", f"## Money in this period (not spend — total: {total_income:,.2f})"]
        for cat, amount in sorted(income_breakdown.items(), key=lambda kv: -kv[1]):
            lines.append(f"- **{cat}:** {amount:,.2f}")

    if business_total or personal_total:
        lines += ["", "## Business vs personal"]
        lines.append(f"- **Business spend:** {business_total:,.2f}")
        lines.append(f"- **Personal spend:** {personal_total:,.2f}")
        if untagged_total:
            lines.append(f"- **Untagged spend (couldn't tell which):** {untagged_total:,.2f}")
        if business_income_total or personal_income_total:
            lines.append(f"- **Business money in:** {business_income_total:,.2f}")
            lines.append(f"- **Personal money in:** {personal_income_total:,.2f}")
            if untagged_income_total:
                lines.append(f"- **Untagged money in:** {untagged_income_total:,.2f}")

    if over_budget_categories:
        lines += ["", "## Over budget", *[f"- {c}" for c in over_budget_categories]]

    return {
        "status": "ok",
        "total_spent": round(total_spent, 2),
        "category_breakdown": category_breakdown,
        "category_vendors": category_vendors,
        "total_income": round(total_income, 2),
        "income_breakdown": income_breakdown,
        "business_total": round(business_total, 2),
        "personal_total": round(personal_total, 2),
        "untagged_total": round(untagged_total, 2),
        "business_category_breakdown": business_category_breakdown,
        "personal_category_breakdown": personal_category_breakdown,
        "business_income_total": round(business_income_total, 2),
        "personal_income_total": round(personal_income_total, 2),
        "untagged_income_total": round(untagged_income_total, 2),
        "subscriptions": subscriptions,
        "recurring_investments": recurring_investments,
        "payments_pending": payments_pending,
        "duplicate_count": duplicate_count,
        "missing_invoice_count": len(missing_invoices),
        "recurring_count": recurring_count,
        "gst_eligible_total": round(gst_eligible_total, 2),
        "over_budget_categories": over_budget_categories,
        "summary_markdown": "\n".join(lines),
    }


def query_ledger(sheet_id: str, filter_category: Optional[str] = None) -> Dict[str, Any]:
    """Reads rows from the Google Sheet ledger, optionally filtered by category. Not currently wired into any agent."""
    try:
        service = get_service("sheets", "v4")
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{LEDGER_TAB}!A:H"
        ).execute()
        raw_rows = result.get("values", [])

        rows: List[Dict[str, Any]] = []
        for raw in raw_rows:
            padded = raw + [""] * (len(LEDGER_COLUMNS) - len(raw))
            row = dict(zip(LEDGER_COLUMNS, padded))
            if filter_category and row.get("category") != filter_category:
                continue
            rows.append(row)

        return {"status": "ok", "rows": rows, "row_count": len(rows)}
    except HttpError as e:
        return {"status": "failed", "rows": [], "row_count": 0, "error": str(e)}


def _get_first_tab_title(service, sheet_id: str) -> str:
    """Returns the title of the spreadsheet's first tab (its default first page)."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties").execute()
    sheets = meta.get("sheets", [])
    if not sheets:
        raise ValueError("This Sheet has no tabs to write to.")
    return sheets[0]["properties"]["title"]


def save_report_to_sheet(report: Dict[str, Any], sheet_id: str) -> Dict[str, Any]:
    """Writes the current monthly report to the spreadsheet's first tab, overwriting whatever was there before."""
    from datetime import datetime

    def _s(value: Any) -> Any:
        return sanitize_csv_cell(str(value)) if isinstance(value, str) else value

    rows: List[List[Any]] = [
        ["ReconAI Monthly Reconciliation Report"],
        [f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        [],
        ["Total spent", report.get("total_spent", 0)],
        ["Total money in", report.get("total_income", 0)],
        ["Business spend", report.get("business_total", 0)],
        ["Personal spend", report.get("personal_total", 0)],
        ["Untagged spend", report.get("untagged_total", 0)],
        ["Business money in", report.get("business_income_total", 0)],
        ["Personal money in", report.get("personal_income_total", 0)],
        ["Duplicates flagged", report.get("duplicate_count", 0)],
        ["Missing invoices", report.get("missing_invoice_count", 0)],
        ["Recurring charges", report.get("recurring_count", 0)],
        ["GST-eligible total", report.get("gst_eligible_total", 0)],
        [],
    ]

    category_breakdown = report.get("category_breakdown", {})
    category_vendors = report.get("category_vendors", {})
    rows.append(["Category breakdown"])
    rows.append(["Category", "Amount", "Vendors"])
    for cat, amount in sorted(category_breakdown.items(), key=lambda kv: -kv[1]):
        rows.append([_s(cat), amount, _s(", ".join(category_vendors.get(cat, [])))])
    rows.append([])

    rows.append(["Subscriptions"])
    rows.append(["Vendor", "Amount", "Category"])
    for s in report.get("subscriptions", []):
        rows.append([_s(s["vendor"]), s["amount"], _s(s["category"])])
    rows.append([])

    rows.append(["Recurring investments"])
    rows.append(["Vendor", "Amount", "Category"])
    for s in report.get("recurring_investments", []):
        rows.append([_s(s["vendor"]), s["amount"], _s(s["category"])])
    rows.append([])

    rows.append(["Payments pending"])
    rows.append(["Vendor", "Amount", "Category", "Date"])
    for p in report.get("payments_pending", []):
        rows.append([_s(p["vendor"]), p["amount"], _s(p["category"]), _s(p.get("date", ""))])
    rows.append([])

    income_breakdown = report.get("income_breakdown", {})
    if income_breakdown:
        rows.append(["Money in this period"])
        rows.append(["Category", "Amount"])
        for cat, amount in sorted(income_breakdown.items(), key=lambda kv: -kv[1]):
            rows.append([_s(cat), amount])
        rows.append([])

    over_budget_categories = report.get("over_budget_categories", [])
    if over_budget_categories:
        rows.append(["Over budget"])
        for cat in over_budget_categories:
            rows.append([_s(cat)])

    try:
        service = get_service("sheets", "v4")
        tab_name = _get_first_tab_title(service, sheet_id)
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id, range=f"'{tab_name}'!A:Z", body={}
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()
        return {"status": "ok", "rows_written": len(rows), "tab": tab_name}
    except (HttpError, ValueError) as e:
        return {"status": "failed", "rows_written": 0, "error": str(e)}
