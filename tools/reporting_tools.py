"""tools/reporting_tools.py — Reporting Agent toolset.

Per the architecture doc: summarize, answer NL questions, generate
recommendations — never modify the ledger or re-run reconciliation logic.

query_ledger is currently NOT wired into any agent (see the note at the top
of agents.py) — Sheets sync is parked as a future integration, not deleted.
It's left here, dormant, for when that's re-enabled.
"""

from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError

from tools.google_auth import get_service
from tools.reconciliation_tools import LEDGER_TAB

LEDGER_COLUMNS = [
    "date", "vendor", "amount", "category",
    "is_duplicate", "is_recurring", "gst_eligible", "source_id",
]

# Full-word/full-phrase hints only — deliberately avoiding bare 2-letter
# abbreviations like "rd" or "fd", which would false-positive-match unrelated
# words (bare "rd" is a substring of "landlord", bare "fd" can collide with
# other category text). Spelling these out as phrases relies on the
# extraction prompt using full category names (e.g. "Recurring Deposit",
# "Fixed Deposit") rather than bare abbreviations.
_INVESTMENT_CATEGORY_HINTS = (
    "invest", "sip", "mutual fund", "recurring deposit", "fixed deposit",
    "gold", "stock", "equity", "share", "demat", "etf", "bond",
    "ppf", "nps", "elss", "commodit",
)
# Money coming BACK IN from an investment — redemption/withdrawal proceeds,
# a matured RD/FD payout, capital gains, sale profit. Opposite direction of
# _INVESTMENT_CATEGORY_HINTS above; treated like income (excluded from
# spend totals), never like an investment outflow.
_INVESTMENT_GAIN_HINTS = ("investment gain", "capital gain", "redemption", "maturity")
_INCOME_CATEGORY_HINTS = (
    "income", "dividend", "interest", "salary income", "salary credit",
    "refund", "cashback", "client payment",
)
_HOUSING_CATEGORY_HINTS = ("rent", "housing", "lease", "mortgage")


def _is_investment_category(category: str) -> bool:
    cat = (category or "").lower()
    return any(hint in cat for hint in _INVESTMENT_CATEGORY_HINTS)


def _is_housing_category(category: str) -> bool:
    """Rent/housing is a recurring fixed cost, not a "subscription" in the
    ordinary sense — keep it out of the subscriptions list even though it's
    flagged is_recurring. It still shows up in the overall category
    breakdown and recent-transactions list."""
    cat = (category or "").lower()
    return any(hint in cat for hint in _HOUSING_CATEGORY_HINTS)


def _is_investment_gain_category(category: str) -> bool:
    """True for money coming back IN from an investment (redemption,
    maturity, capital gains, sale profit) — the credit-side counterpart to
    _is_investment_category's debit side. Treated as inflow, same as income,
    for spend-total purposes."""
    cat = (category or "").lower()
    return any(hint in cat for hint in _INVESTMENT_GAIN_HINTS)


def _is_income_category(category: str) -> bool:
    """A recurring interest/dividend/salary credit is income, not a
    subscription or an investment outflow — it shouldn't land in either list
    even though it's flagged is_recurring (e.g. a monthly HDFC interest
    credit). Note: hints use "salary income"/"salary credit" rather than
    bare "salary" specifically so a small business's "Payroll"/"Staff
    Salaries" expense category (money paid OUT to employees) is never
    mistaken for income."""
    cat = (category or "").lower()
    return any(hint in cat for hint in _INCOME_CATEGORY_HINTS)


def _is_inflow_category(category: str) -> bool:
    """Any category representing money coming IN rather than being spent —
    income (salary/interest/dividend) or investment gains (redemption/
    maturity/capital gains). Used to keep total_spent/category_breakdown as
    a true "money that left the account" figure."""
    return _is_income_category(category) or _is_investment_gain_category(category)


def _spend_type(t: Dict[str, Any]) -> str:
    """Normalizes a transaction's spend_type_guess to exactly "business",
    "personal", or "untagged" (never a raw/blank/unexpected value) — so
    every transaction lands in exactly one of the three business/personal
    split buckets below, none silently dropped. Freelancers and small
    business owners in India routinely mix both through one inbox/account
    (see extract_invoice_data's prompt), so this is a per-transaction split,
    not an account-wide assumption."""
    value = (t.get("spend_type_guess") or "").strip().lower()
    return value if value in ("business", "personal") else "untagged"


def _dedupe_recurring(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapses recurring transactions down to one row per distinct
    vendor+amount so a subscription/SIP that matched multiple months in the
    batch shows up once, not once per matching month."""
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
    """Builds a monthly reconciliation summary from already-reconciled data.

    Call this once, after Reconciliation has finished — it does not fetch
    new data or re-run any checks, only summarizes what check_duplicates_and_budget
    already computed.

    Args:
        reconciled_data: The dict returned by check_duplicates_and_budget
            (keys: transactions, missing_invoices, budget_summary).

    Returns:
        dict with keys:
          status ("ok"),
          total_spent (float), category_breakdown (dict category->amount),
          category_vendors (dict category -> sorted list of distinct vendor
            names in it) — use this for any narrative vendor callout, never
            re-scan the raw transactions,
          total_income (float), income_breakdown (dict category->amount) —
            money that came IN this period rather than being spent: salary,
            bank interest, dividends, and investment gains (redemption/
            maturity/capital gains proceeds). Kept separate from total_spent
            so investment profit or a salary credit never inflates "how much
            you spent",
          business_total/personal_total/untagged_total (float) — total_spent
            split by each transaction's spend_type_guess (freelancers and
            small business owners routinely mix both through one inbox, so
            this is a per-transaction split of the same total_spent figure,
            not a second total). untagged_total covers transactions whose
            spend_type_guess couldn't be determined — never silently
            dropped from the split. business_category_breakdown/
            personal_category_breakdown mirror category_breakdown but
            scoped to each side (untagged transactions still count in the
            combined category_breakdown, just not in either scoped one).
            business_income_total/personal_income_total/
            untagged_income_total are the same split applied to
            total_income instead of total_spent,
          subscriptions (list of {vendor, amount, category} — recurring,
            non-investment, non-inflow transactions, deduped to one row per
            vendor),
          recurring_investments (list of same shape — recurring transactions
            in an investment-like category: SIP/mutual fund/RD/gold/stock/
            equity/PPF/NPS/etc. contribution debits, never redemption/gains),
          payments_pending (list of {vendor, amount, category, date} —
            transactions whose payment_status_guess is "pending": a bill or
            scheduled debit that's been generated/issued but not actually
            paid yet),
          duplicate_count (int), missing_invoice_count (int),
          recurring_count (int), gst_eligible_total (float),
          over_budget_categories (list), summary_markdown (str).

        total_spent, category_breakdown, category_vendors, recurring_count,
        and gst_eligible_total are all computed from transactions that are
        non-duplicate, not payment_status "pending", AND not an inflow
        category (income or investment gains — see total_income above). A
        flagged duplicate is the same charge counted twice, not additional
        spend. A "pending" transaction is a bill/scheduled debit that's only
        been generated/issued — it isn't money that's left your account yet,
        so it doesn't belong in a spend total either; it shows up in
        payments_pending instead. duplicate_count and payments_pending still
        surface everything found, so nothing is hidden — it's just not
        double- or pre-counted into the spend totals, and inflow money is
        never counted as spend at all.
    """
    transactions = reconciled_data.get("transactions", [])
    missing_invoices = reconciled_data.get("missing_invoices", [])
    budget_summary = reconciled_data.get("budget_summary", {})

    duplicate_count = sum(1 for t in transactions if t.get("is_duplicate"))
    unique_transactions = [t for t in transactions if not t.get("is_duplicate")]

    pending_transactions = [t for t in unique_transactions if t.get("payment_status_guess") == "pending"]
    paid_transactions = [t for t in unique_transactions if t.get("payment_status_guess") != "pending"]

    # Split paid transactions by direction: money that left the account
    # (outflow — actual spend) vs. money that came in (inflow — salary,
    # interest, dividends, investment redemption/maturity/gains). Only
    # outflow transactions count toward total_spent/category_breakdown;
    # inflow gets its own total_income/income_breakdown instead of silently
    # inflating "how much you spent" (e.g. a mutual fund redemption profit
    # is not spend).
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

    # Business vs personal split, per the pitch's "surface both, clearly
    # tagged, rather than pretending they're separate" — a per-transaction
    # split of the same outflow/inflow totals above, not a second set of
    # numbers. business_total + personal_total + untagged_total always
    # equals total_spent (same for the *_income_total trio vs total_income).
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
        # untagged transactions still count in total_spent/category_breakdown
        # above - they just don't split into either business or personal
        # breakdown, since we genuinely don't know which one they are.

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

    # Housing/rent is excluded from "subscriptions" specifically (see
    # _is_housing_category) — inflow categories are already excluded from
    # outflow_transactions above, so no need to re-check them here.
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
    """Reads rows from the Google Sheet ledger for the Reporting Agent to
    reason over when answering a natural-language question.

    NOT currently wired into any agent — see the module docstring.

    Args:
        sheet_id: The target Google Sheet's ID.
        filter_category: Optional category name to filter rows by.

    Returns:
        dict with keys: status ("ok"/"failed"), rows (list of dicts keyed
        by LEDGER_COLUMNS), row_count (int), error (str, only if failed).
    """
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
