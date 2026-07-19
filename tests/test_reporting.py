"""Tests for tools/reporting_tools.py's generate_monthly_report.

Covers the behavior fixed after the "Frame Kro" report/dashboard mismatch
and the new payment_status_guess ("pending" bill) handling: duplicates and
pending-payment transactions must never land in total_spent/category
totals, and subscriptions/recurring_investments/payments_pending must be
split correctly.
"""

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


def test_duplicate_excluded_from_totals_but_counted():
    data = {
        "transactions": [
            _txn(vendor="Frame Kro", amount=499, date="2026-07-16", is_duplicate=True),
            _txn(vendor="Frame Kro", amount=499, date="2026-07-17", is_duplicate=False),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["total_spent"] == 499
    assert r["category_breakdown"]["Shopping"] == 499
    assert r["duplicate_count"] == 1


def test_pending_bill_excluded_from_totals_and_listed_separately():
    data = {
        "transactions": [
            _txn(vendor="MSEDCL", amount=340, category="Utilities", payment_status_guess="pending"),
            _txn(vendor="Netflix", amount=649, category="Subscriptions", is_recurring=True),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["total_spent"] == 649
    assert "Utilities" not in r["category_breakdown"]
    assert r["payments_pending"] == [
        {"vendor": "MSEDCL", "amount": 340.0, "category": "Utilities", "date": "2026-07-01"}
    ]


def test_subscriptions_vs_recurring_investments_split():
    data = {
        "transactions": [
            _txn(vendor="Anthropic PBC", amount=1899, category="SaaS", is_recurring=True),
            _txn(vendor="NJ India Online", amount=39000, category="Investment", is_recurring=True),
            _txn(vendor="One-off Shop", amount=250, category="Shopping", is_recurring=False),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert [s["vendor"] for s in r["subscriptions"]] == ["Anthropic PBC"]
    assert [s["vendor"] for s in r["recurring_investments"]] == ["NJ India Online"]


def test_category_vendors_used_for_narrative_detail():
    data = {
        "transactions": [
            _txn(vendor="Google Cloud Platform", amount=1100, category="SaaS"),
            _txn(vendor="Anthropic PBC", amount=1899, category="SaaS"),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert set(r["category_vendors"]["SaaS"]) == {"Google Cloud Platform", "Anthropic PBC"}


def test_recurring_rent_excluded_from_subscriptions():
    # Rent is a recurring fixed cost, not a "subscription" in the ordinary
    # sense - it shouldn't land in the subscriptions list just because
    # is_recurring is True and it's not an investment category.
    data = {
        "transactions": [
            _txn(vendor="Landlord", amount=20000, category="Rent", is_recurring=True),
            _txn(vendor="Netflix", amount=649, category="Subscriptions", is_recurring=True),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    vendors_in_sub_or_inv = {s["vendor"] for s in r["subscriptions"] + r["recurring_investments"]}
    assert "Landlord" not in vendors_in_sub_or_inv
    assert "Netflix" in vendors_in_sub_or_inv
    # Rent still counts toward total spent and the category breakdown.
    assert r["category_breakdown"]["Rent"] == 20000


def test_recurring_income_excluded_from_subscriptions_and_investments():
    # A recurring interest/dividend credit is money coming IN, not a
    # subscription you pay for or an investment outflow - it shouldn't
    # land in either list just because is_recurring is True.
    data = {
        "transactions": [
            _txn(vendor="HDFC Bank", amount=2124, category="Interest Income", is_recurring=True),
            _txn(vendor="Anthropic PBC", amount=1899, category="SaaS", is_recurring=True),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    vendors_in_sub_or_inv = {s["vendor"] for s in r["subscriptions"] + r["recurring_investments"]}
    assert "HDFC Bank" not in vendors_in_sub_or_inv
    assert "Anthropic PBC" in vendors_in_sub_or_inv


def test_recurring_dedupe_collapses_same_vendor_amount():
    data = {
        "transactions": [
            _txn(vendor="Netflix", amount=649, category="Subscriptions", date="2026-06-05", is_recurring=True),
            _txn(vendor="Netflix", amount=649, category="Subscriptions", date="2026-07-05", is_recurring=True),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert len(r["subscriptions"]) == 1
    assert r["subscriptions"][0]["amount"] == 649
