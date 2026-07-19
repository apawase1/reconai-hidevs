"""tests/test_reconciliation.py — covers the deterministic dedup/recurring/
budget/missing-invoice logic in tools/reconciliation_tools.py. This is exact
arithmetic per the architecture doc (section 8), so it's worth testing
directly rather than trusting the LLM layer above it.
"""

from tools.reconciliation_tools import check_duplicates_and_budget


def test_flags_exact_duplicate_same_vendor_amount_close_dates():
    transactions = [
        {"vendor": "Netflix", "amount": 649, "date": "2026-07-05", "category": "Subscriptions"},
        {"vendor": "Netflix", "amount": 649, "date": "2026-07-06", "category": "Subscriptions"},
    ]
    result = check_duplicates_and_budget(transactions)
    assert result["transactions"][0]["is_duplicate"] is False
    assert result["transactions"][1]["is_duplicate"] is True
    assert result["transactions"][1]["duplicate_of"] == 0


def test_does_not_flag_different_vendors_as_duplicate():
    transactions = [
        {"vendor": "Netflix", "amount": 649, "date": "2026-07-05", "category": "Subscriptions"},
        {"vendor": "Spotify", "amount": 649, "date": "2026-07-05", "category": "Subscriptions"},
    ]
    result = check_duplicates_and_budget(transactions)
    assert all(t["is_duplicate"] is False for t in result["transactions"])


def test_does_not_flag_same_vendor_far_apart_dates():
    transactions = [
        {"vendor": "Amazon", "amount": 1200, "date": "2026-07-01", "category": "Shopping"},
        {"vendor": "Amazon", "amount": 1200, "date": "2026-07-20", "category": "Shopping"},
    ]
    result = check_duplicates_and_budget(transactions)
    assert all(t["is_duplicate"] is False for t in result["transactions"])


def test_flags_recurring_across_months():
    transactions = [
        {"vendor": "Netflix", "amount": 649, "date": "2026-06-05", "category": "Subscriptions"},
        {"vendor": "Netflix", "amount": 649, "date": "2026-07-05", "category": "Subscriptions"},
    ]
    result = check_duplicates_and_budget(transactions)
    assert all(t["is_recurring"] for t in result["transactions"])


def test_budget_comparison_flags_over_budget_category():
    transactions = [
        {"vendor": "Zomato", "amount": 3000, "date": "2026-07-01", "category": "Food"},
        {"vendor": "Swiggy", "amount": 4000, "date": "2026-07-10", "category": "Food"},
    ]
    result = check_duplicates_and_budget(transactions, budget={"Food": 5000})
    assert result["budget_summary"]["Food"]["spent"] == 7000
    assert result["budget_summary"]["Food"]["over_budget"] is True


def test_missing_invoice_flags_unmatched_bank_debit():
    transactions = [
        {"vendor": "Amazon", "amount": 1200, "date": "2026-07-01", "category": "Shopping"},
    ]
    bank_transactions = [
        {"description": "AMAZON PURCHASE", "amount": 1200, "type": "debit"},
        {"description": "ELECTRICITY BOARD", "amount": 900, "type": "debit"},
    ]
    result = check_duplicates_and_budget(transactions, bank_transactions=bank_transactions)
    descriptions = [m["description"] for m in result["missing_invoices"]]
    assert "ELECTRICITY BOARD" in descriptions
    assert "AMAZON PURCHASE" not in descriptions


def test_missing_invoice_ignores_credits():
    transactions = []
    bank_transactions = [{"description": "SALARY", "amount": 50000, "type": "credit"}]
    result = check_duplicates_and_budget(transactions, bank_transactions=bank_transactions)
    assert result["missing_invoices"] == []
