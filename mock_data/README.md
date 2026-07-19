# mock_data/

Reference snapshots of ReconAI's pipeline, generated (not hand-typed) by feeding a
realistic Discovery-agent-shaped transaction batch through the actual
`check_duplicates_and_budget` and `generate_monthly_report` functions. This
guarantees every duplicate/recurring/pending/budget flag here is exactly what
the real code would produce, not an approximation.

- **discovery_output_sample.json** — what `extract_invoice_data` hands off to
  Reconciliation: raw transactions with `source_id`, `vendor`, `amount`, `date`,
  `category`, `is_recurring_guess`, `gst_eligible_guess`, `payment_status_guess`,
  `status`.
- **reconciled_output_sample.json** — the same batch after
  `check_duplicates_and_budget`: adds `is_duplicate`/`duplicate_of`/`is_recurring`,
  plus `missing_invoices` (from a cross-checked bank CSV debit with no matching
  invoice) and `budget_summary`. This exact dict is what `app.py`'s
  `_MOCK_RECONCILED_DATA` and the "Load mock data" sidebar button use.
- **report_sample.json** — the same data after `generate_monthly_report`: totals,
  category breakdown, subscriptions, recurring investments, payments pending,
  and the markdown summary.

Covers every feature the app demos: a real duplicate (Frame Kro — two emails
for one order), six recurring subscriptions (Anthropic, Google Cloud, Netflix,
Disney+ Hotstar, Amazon Prime, Spotify), two recurring investments (a SIP, a
NACH mutual fund debit), recurring rent (kept out of the subscriptions list —
it's a fixed cost, not a subscription), one pending/unpaid bill (BESCOM
electricity), recurring interest income correctly kept out of both the
subscriptions and investments lists, groceries and Amazon shopping, food
orders, UPI transfers, a dividend credit, and one bank-CSV debit (PVR Cinemas)
with no matching invoice.

Regenerate by editing the script embedded in this session's history, or by
constructing a transaction list in the same shape and calling
`tools.reconciliation_tools.check_duplicates_and_budget` then
`tools.reporting_tools.generate_monthly_report` directly.
