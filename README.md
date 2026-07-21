# ReconAI — Autonomous AI Financial Operations Agent

Every month, freelancers and small business owners — across any domain (design, consulting, retail, trades) — lose real time hunting through Gmail and bank statements for invoices, receipts, and bills, then reconciling them by hand. It doesn't matter what the business sells; financial evidence still scatters across the same inbox.

ReconAI isn't a chatbot you describe your spending to — it's an agent that goes and finds the evidence itself and automates the workflow of a junior finance executive. Built on Google ADK as a 3-agent [pipeline](https://google.github.io/adk-docs/): a Discovery Agent searches Gmail for invoices/receipts and reads an uploaded bank statement CSV; a Reconciliation Agent runs exact-math duplicate detection, recurring/subscription tagging, budget comparison, and GST-eligibility flagging; a Reporting Agent turns the result into a plain-English monthly report, a dashboard, and grounded natural-language Q&A over the actual reconciled data.

Built for freelancers and small business owners in India (Open Track). This audience typically doesn't separate business and personal finances into different accounts/inboxes, so ReconAI tags every transaction `business`/`personal` and splits totals accordingly, rather than pretending the two are separate.

**Live static demo (mock data, no backend required):** https://apawase1.github.io/reconai-hidevs/

> Built for the AI Agent Builder Series 2026 hackathon. Single-user personal demo, not a multi-tenant product. See `RECONAI_ARCHITECTURE_ADDENDUM.md` for the full design rationale.
>
> **This build is intentionally scoped down to Gmail-in, report-out** to keep Google API call volume and Gemini token usage inside a hobby/hackathon budget — not because Drive or Sheets integration is technically hard. Both are straightforward extensions of the existing tool layer (see "Future scope" below) and are the natural next step once there's compute/API budget to run them continuously.
>
> **Discovery is currently Gmail-only** — Drive support (`fetch_drive_receipts`) has been deliberately removed to cut Google API call volume and Gemini token usage per run. See `RECONAI_ARCHITECTURE_ADDENDUM.md` section D if you want to re-add it later.
>
> **Google Sheets *ledger* sync is parked as a future integration**, not deleted — `get_processed_ids`/`append_to_ledger`/`mark_processed`/`query_ledger` still exist in `tools/reconciliation_tools.py` and `tools/reporting_tools.py`, but no agent currently calls them, to cut API calls and setup friction (missing-tab errors, cross-run memory that needs a Sheet to exist). This build is Gmail-in, report-out: nothing is persisted between runs, and every run re-fetches/re-extracts the full date-scoped range. Re-enable by re-wiring those four tools back into `agents.py`.
>
> **This is separate from "Save to Sheet"**, which *is* live — the dashboard has an explicit button that writes the current run's numbers to a "Dashboard" tab in a Sheet you paste in, overwriting it each time. It's a one-off snapshot export, not the ledger above, and it's never called automatically.

## Architecture

```
User: "Prepare July reconciliation"
        |
Discovery Agent  ->  Reconciliation Agent  ->  Reporting Agent
  Gmail, bank CSV,     dedup, missing-invoice,     monthly report:
  Gemini extraction     budget compare, GST/         where it went, subscriptions,
                         recurring/pending tagging     recurring investments,
                                                        payments pending, NL Q&A
        |                     |                        |
                    Streamlit UI: chat + live activity trace + dashboard
```

Each agent is an ADK `LlmAgent` running Gemini 3.5 Flash, orchestrated as a `SequentialAgent`. Agents hand off data via ADK session state (`output_key` / `{key}` templating) — see `agents.py`. There is no ledger/Sheet in this build (see the note above), so session state is purely in-run working memory and does not survive a restart.

## Guardrails

Eight layers, all wired as ADK callbacks in `tools/security.py` (see `RECONAI_ARCHITECTURE_ADDENDUM.md` section A for the full rationale):

1. **OAuth scopes** — `gmail.readonly` for reads; no send/modify/delete scope requested anywhere. (`tools/google_auth.py` also still requests the `spreadsheets` scope for when Sheets sync is re-enabled, but nothing currently uses it.)
2. **Instruction-level guardrail** — every agent's instruction states it's read-only and treats content as data, not instructions.
3. **`block_destructive_actions`** — `before_tool_callback` tripwire that rejects any tool call whose name suggests delete/send/modify.
4. **Prompt-injection handling** — folded into `input_filter`; agents are told explicitly to never act on requests embedded in email content.
5. **`sanitize_csv_cell`** — neutralizes formula-injection characters (`=`, `+`, `-`, `@`) in uploaded CSV cells.
6. **`input_filter`** — length cap + prompt-injection pattern screen on every model call.
7. **`output_filter`** — redacts secret-shaped strings and blocks destructive-sounding output before it reaches the user.
8. **`rate_limiter`** — per-session token-bucket cap on model calls, fails gracefully rather than raising.

All eight are covered by `tests/test_security.py` (59 tests total across the suite, all passing).

## Project structure

```
reconai/
|-- agents.py                 # 3 agents + SequentialAgent + guardrail wiring
|-- app.py                    # Streamlit UI: ADK Runner, CSV upload, live trace, themed dashboard
|-- tools/
|   |-- google_auth.py            # shared OAuth credential loading (Gmail; Sheets scope dormant)
|   |-- discovery_tools.py        # fetch_invoice_emails, extract_invoice_data, parse_bank_csv,
|   |                              #   unlock_pdf_attachment
|   |-- reconciliation_tools.py   # check_duplicates_and_budget (Sheets tools dormant, see note above)
|   |-- reporting_tools.py        # generate_monthly_report (query_ledger dormant)
|   |-- security.py               # the 8 guardrails above
|-- tests/
|   |-- test_security.py          # guardrails
|   |-- test_extraction.py        # email/HTML parsing, Gemini extraction (mocked), CSV parsing
|   |-- test_reconciliation.py    # dedup/recurring/budget/missing-invoice logic
|   |-- test_reporting.py         # report sections: totals, subscriptions, investments, pending bills
|-- mock_data/                # reference JSON snapshots (discovery -> reconciled -> report shape),
|   |                          #   used for docs and the "Load mock data" button — no real data
|-- docs/
|   |-- index.html                # static GitHub Pages demo (mock data only, no backend)
|-- RECONAI_ARCHITECTURE_ADDENDUM.md  # design rationale referenced throughout this README
|-- Dockerfile                # Cloud Run container
|-- .dockerignore
|-- requirements.txt
|-- .env.example
```

> Not in this repo: the mock invoice/bank-statement PDFs and seed emails used to demo against a throwaway Gmail account (`domt25499@gmail.com`) were sent directly to that inbox and aren't checked in — they'd just be dead weight here once the demo account is seeded. `mock_data/` (JSON only, no PDFs/emails) is kept since the app's own "Load mock data" button and `docs/index.html` both reference it.

## Setup

1. **Python 3.11+**, then:
   ```
   python -m venv venv
   source venv/bin/activate   # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```
2. **Google Cloud OAuth credentials**: create an OAuth 2.0 Desktop app client in Google Cloud Console, enable the Gmail API, download the client secret as `credentials.json` into the project root.
3. **Gemini API key**: copy `.env.example` to `.env` and fill in `GOOGLE_API_KEY`.
4. **Run locally**:
   ```
   streamlit run app.py
   ```
   The app itself now handles first-time sign-in: on open, it shows a **"Sign in with Google"** screen and blocks the rest of the UI until you click it and complete the browser consent flow — no separate script to run first. (`test_auth.py` still exists as a standalone way to pre-mint `token.json` before a Cloud Run deploy, since that's a headless environment with no browser to pop open — see "Deployment" below.)

`credentials.json`, `token.json`, and `.env` are git-ignored and docker-ignored — never commit them.

**Switching accounts:** the top-right corner holds **"Clear screen"** and a Google-styled account button showing your signed-in address. "Clear screen" resets the chat and dashboard to a blank run without touching your Google sign-in — use it to start a fresh reconciliation. Clicking the account button signs out: it clears the cached token, wipes every trace of the current session (so no data leaks to the next person), and shuts down this local app entirely — since it's a single-user local demo tool, "signed out" means "stopped," not "showing someone else's login screen in the same running process." Restart with `streamlit run app.py` (or your usual launch command) and sign in with the other account.

## Usage

- Optionally fill in a JSON budget and/or upload a bank statement CSV in the sidebar.
- Bank CSV must match the one supported format: columns `Date, Description, Amount, Type`. Other formats fail with a clear error rather than being silently misparsed.
- Discovery excludes your own Sent/Drafts mail (`-in:sent -in:drafts -in:chats`), scoped to the current calendar month by default — invoices you emailed to someone else won't be misread as ones you received. It deliberately does *not* restrict to `in:inbox`, so receipts that Gmail auto-archived out of your inbox (common for vendor/subscription receipts with a "skip inbox" filter) still get found.
- Type `Prepare July reconciliation` (or similar) in the chat box to run the full 3-agent pipeline. Watch the live activity trace, then see the report and dashboard below it. Both the chat report and the dashboard read from the exact same `generate_monthly_report` output — they can't disagree with each other.
- The report and dashboard are organized as: **where the money went** (category breakdown), **subscriptions you have** (recurring non-investment charges — SaaS etc.), **recurring investments** (SIP/NACH/mutual fund debits), and **payments pending** (bills that were generated/issued but not confirmed paid — e.g. an electricity bill notice — kept out of the spend total until they're actually paid).
- Duplicate-flagged transactions are excluded from every total (they're the same charge counted twice, not extra spend) but still shown, tagged, in the recent-transactions list so you can review them.
- Ask follow-up questions ("Why did I spend more this month?") — the Reporting Agent can only answer from this run's own report data; there's no ledger to query across past runs in this build.
- If Gmail has no invoice/receipt-looking emails, Discovery will legitimately report 0 found — that's correct behavior, not a bug. Send yourself a test email with a subject like "Invoice from Test Vendor" to get a real end-to-end run.
- **Save to Sheet**: paste a Google Sheet ID (the long ID in its URL) into the sidebar — the sidebar's **"How to set up the Sheet"** expander walks through creating one and finding the ID — then click **"💾 Save to Sheet"** in the top-right of the page. This writes the current run's numbers to a "Dashboard" tab, creating that tab if it doesn't exist yet — every click overwrites the tab with the latest snapshot, it never appends or accumulates rows. The Sheet just needs to be one your signed-in Google account can edit (e.g. one you created at [sheets.new](https://sheets.new) while signed into that same account).
- **Export PDF**: click **"📄 Export PDF"** next to "Save to Sheet" to download the full report as a single, print-friendly PDF — the same numbers as the dashboard (never recomputed), organized into clearly headed, bookmarked sections (where it went with a category chart, business vs personal, subscriptions, recurring investments, payments pending, money in this period) so it's easy to jump straight to a section from any PDF viewer's sidebar. If you've chatted with the Reporting Agent this run, its last reply is included as a "Recommendations & notes" appendix at the end. Nothing is uploaded or saved anywhere — it's generated fresh on each click and downloaded straight to your computer.
- **Password-protected PDF attachments**: if an invoice email has a locked PDF attached, Discovery reports everything else as normal and then asks you, in the chat reply, for that specific file's password (naming the file and which email it came from). If the email itself already states the password format — common with bank e-statements ("password is the first 4 letters of your name + your DOB in DDMM," "password is your PAN in uppercase") — Discovery reads that and quotes the hint back to you instead of making you dig it up yourself. Just reply with the password in your next message — no special format required, Discovery will match it to the file it just asked about and unlock it via `unlock_pdf_attachment`. The password itself is never written anywhere or repeated back in any reply.

## Switching to a different Google account

1. Add the other account's email as a **Test user** in Google Cloud Console → APIs & Services → OAuth consent screen, *before* the demo — unverified apps block sign-in for anyone not on that list.
2. In the app's top-right corner, click the Google account button (it shows your signed-in address) — this drops the cached token, wipes the session, and shuts down the local app (no manual file juggling needed, but you do need to relaunch it).
3. Restart the app (`streamlit run app.py`), click **"Sign in with Google"**, and pick the other account in the browser window that opens.
4. To go back afterward, sign out and restart again, signing back in with your own account the same way.

(The old manual approach — `mv token.json token_backup.json`, restart, then `mv` it back — still works if you're running outside Streamlit, e.g. scripting against the tools directly, but the in-app button is the fast path for a live demo.)

## Deployment — Google Cloud Run

Streamlit needs a persistent server, which static/serverless-only hosts (GitHub Pages, Vercel) can't provide — Cloud Run runs a full container, so this deploys with **zero changes to `app.py`**.

```
gcloud run deploy reconai --source . --region <your-region> --allow-unauthenticated
```

- Set `GOOGLE_API_KEY` via `--set-env-vars` or Secret Manager — never bake `.env` into the image.
- Add the deployed `https://<service>.run.app` URL as an authorized redirect URI in your Google Cloud OAuth consent config.
- **OAuth caveat**: Cloud Run containers are stateless and ephemeral. `InstalledAppFlow.run_local_server()` (used for the first-time auth dance) only works on a machine with a display — your laptop, not the container. Mint `token.json` locally first (setup step 5 above), then ship it to the deployed service as a mounted secret rather than baking it into the image. Expect an occasional re-auth prompt after long idle periods; for a single-user demo this is an acceptable tradeoff, not a bug.

See `RECONAI_ARCHITECTURE_ADDENDUM.md` section B for the full deployment rationale, including why Vercel was ruled out.

## Testing

```
pytest tests/ -v
```

`test_extraction.py`'s Gemini calls are mocked — no network access or API key required to run the suite.

## Known limitations (by design, for this MVP)

- Single-user, single Google account — no multi-tenancy.
- Discovery is Gmail-only for now — no Drive search (see the note at the top of this README).
- One bank CSV format supported (documented in `tools/discovery_tools.py`, `EXPECTED_CSV_COLUMNS`) — not a generic multi-bank parser.
- No persistence between runs — Sheets sync is dormant (see the note at the top of this README), so session state resets on restart and every run re-fetches/re-extracts the full date-scoped range rather than skipping already-seen emails.

## Future scope

This MVP is deliberately scoped to Gmail-in, report-out (one inbox, one bank statement, one month, no persistence) to keep API and Gemini token usage inside a hobby/hackathon budget. The items below are the natural next steps toward the fuller pitch — junior-finance-executive-style automation for small business owners, not just individuals:

- **Google Drive discovery** (`fetch_drive_receipts`) — scan Drive for receipts/invoices saved outside Gmail (e.g. downloaded PDFs, scanned bills), so Discovery isn't limited to what arrived by email. Tool-layer code already exists, switched off, not unbuilt.
- **Google Sheets sync** (`get_processed_ids` / `append_to_ledger` / `mark_processed` / `query_ledger`) — a persistent ledger that remembers what's already been reconciled across runs, so Discovery can skip previously-seen transactions instead of re-fetching the full date range every time, and so the Reporting Agent can answer questions across multiple months, not just the current run. Tool-layer code already exists, switched off, not unbuilt.
- **Cross-account reconciliation** — today `check_duplicates_and_budget` only ever sees one Gmail inbox's extracted transactions plus one optional bank CSV per run. A real small-business setup usually has more than one account (personal + business current account, sometimes a co-founder's inbox too); reconciling *across* them — matching an internal transfer between a user's own accounts instead of counting it as two unrelated external transactions — is not implemented yet. `docs/index.html` has a mockup of the target shape under "Cross-account reconciliation." (Not to be confused with the business/personal *tag*, which is a per-transaction split within one account/inbox and already shipped — see below.)

**Already shipped, not future scope:** the business/personal split is live — `extract_invoice_data` tags every transaction `spend_type_guess` ("business"/"personal"/"unknown") in the same batched Gemini call (no extra API cost), and `generate_monthly_report` splits totals accordingly (`business_total`/`personal_total`/`untagged_total`, plus the same split on `total_income`). This exists because this audience — freelancers and small business owners in India — typically runs both through the same inbox/account rather than keeping them separate, so the tool surfaces both, clearly tagged, instead of pretending they're separate.
- **Growth-strategy synthesis** — the Reporting Agent currently gives reactive, single-month callouts (duplicates, pending bills, GST-eligible spend, over-budget categories), not forward-looking analysis. Trend deltas ("SaaS spend up 18% QoQ"), a cash-flow view netting `total_spent` against `total_income`, and reinvestment nudges all need month-over-month history — which depends on the Sheets ledger above being wired back in.

With more API/compute budget, re-wiring the Sheets/Drive tools and building out cross-account matching + trend-based growth-strategy generation turns ReconAI from a single-run snapshot tool into a continuously-running financial ops agent with real memory — which was the original design intent for this project.
