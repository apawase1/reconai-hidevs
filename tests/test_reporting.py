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


def test_new_investment_types_recognized_as_recurring_investments():
    # SIP/mutual fund were already covered - RD, Gold, Stock/Equity, and FD
    # should land in recurring_investments too, not subscriptions.
    data = {
        "transactions": [
            _txn(vendor="Post Office", amount=5000, category="Recurring Deposit", is_recurring=True),
            _txn(vendor="SafeGold", amount=2000, category="Gold", is_recurring=True),
            _txn(vendor="Zerodha", amount=10000, category="Stock", is_recurring=True),
            _txn(vendor="Groww", amount=15000, category="Equity", is_recurring=True),
            _txn(vendor="SBI", amount=25000, category="Fixed Deposit", is_recurring=True),
            _txn(vendor="Netflix", amount=649, category="Subscriptions", is_recurring=True),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    investment_vendors = {s["vendor"] for s in r["recurring_investments"]}
    assert investment_vendors == {"Post Office", "SafeGold", "Zerodha", "Groww", "SBI"}
    assert [s["vendor"] for s in r["subscriptions"]] == ["Netflix"]


def test_landlord_not_falsely_matched_as_investment():
    # Guards the "bare rd/fd" false-positive risk this hint list is designed
    # to avoid: "Landlord" contains the substring "rd" but must never be
    # treated as an investment category.
    data = {
        "transactions": [
            _txn(vendor="Landlord", amount=20000, category="Rent", is_recurring=True),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["recurring_investments"] == []


def test_investment_gains_excluded_from_total_spent_and_reported_as_income():
    # A mutual fund redemption/profit payout is money coming IN, not spend -
    # it must not inflate total_spent, and should show up in total_income/
    # income_breakdown instead.
    data = {
        "transactions": [
            _txn(vendor="NJ India Online", amount=50000, category="Investment Gains", is_recurring=False),
            _txn(vendor="Groceries Store", amount=2000, category="Groceries", is_recurring=False),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["total_spent"] == 2000
    assert "Investment Gains" not in r["category_breakdown"]
    assert r["total_income"] == 50000
    assert r["income_breakdown"] == {"Investment Gains": 50000}


def test_salary_income_vs_business_payroll_direction():
    # "Salary Income" (credited to an individual) is income; "Payroll" (paid
    # out by a small business to staff) is an expense - the two must never
    # collide even though both involve the word "salary" conceptually.
    data = {
        "transactions": [
            _txn(vendor="Employer Inc", amount=80000, category="Salary Income"),
            _txn(vendor="Staff Member A", amount=30000, category="Payroll"),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["total_income"] == 80000
    assert r["total_spent"] == 30000
    assert r["category_breakdown"] == {"Payroll": 30000}
    assert r["income_breakdown"] == {"Salary Income": 80000}


def test_business_personal_split_on_spend():
    # A freelancer/small-business owner's inbox mixes both - spend must
    # split by each transaction's own spend_type_guess, not one assumption
    # for the whole account, and business_total + personal_total +
    # untagged_total must equal total_spent exactly.
    data = {
        "transactions": [
            _txn(vendor="Google Cloud Platform", amount=1100, category="SaaS", spend_type_guess="business"),
            _txn(vendor="BigBasket", amount=3200, category="Groceries", spend_type_guess="personal"),
            _txn(vendor="q635075112@ybl", amount=80, category="Transfer", spend_type_guess="unknown"),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["total_spent"] == 4380
    assert r["business_total"] == 1100
    assert r["personal_total"] == 3200
    assert r["untagged_total"] == 80
    assert r["business_total"] + r["personal_total"] + r["untagged_total"] == r["total_spent"]
    assert r["business_category_breakdown"] == {"SaaS": 1100}
    assert r["personal_category_breakdown"] == {"Groceries": 3200}


def test_business_personal_split_missing_spend_type_lands_in_untagged():
    # A transaction with no spend_type_guess at all (not even "unknown")
    # must still be counted somewhere, not silently dropped from the split.
    data = {
        "transactions": [_txn(vendor="Mystery Vendor", amount=500, category="Shopping")],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["untagged_total"] == 500
    assert r["business_total"] == 0
    assert r["personal_total"] == 0
    assert "Shopping" not in r["business_category_breakdown"]
    assert "Shopping" not in r["personal_category_breakdown"]


def test_business_personal_split_on_income():
    # Client payments received for freelance work are business income;
    # dividends/interest are personal - both split the same way as spend.
    data = {
        "transactions": [
            _txn(vendor="Razorpay - Client", amount=45000, category="Client Payment", spend_type_guess="business"),
            _txn(vendor="Tata Power Company Ltd", amount=705, category="Income (Dividends)", spend_type_guess="personal"),
        ],
        "missing_invoices": [],
        "budget_summary": {},
    }
    r = generate_monthly_report(data)
    assert r["total_income"] == 45705
    assert r["business_income_total"] == 45000
    assert r["personal_income_total"] == 705
    assert r["untagged_income_total"] == 0
