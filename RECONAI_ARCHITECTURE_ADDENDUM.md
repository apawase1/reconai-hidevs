# ReconAI — Architecture Addendum (Guardrails v2 + Vercel Deployment)

Supplements `RECONAI_ARCHITECTURE_SKILL.md`. Adds three new guardrail layers
(input filters, output filters, rate limiting) to Section 5, and revises the
deployment story (Section 7 / 9) to target Vercel.

---

## A. Guardrails v2 — three new layers

The original spec had scope-restriction (5.1), instruction guardrails (5.2),
a destructive-action `before_tool_callback` (5.3), prompt-injection notes
(5.4), and CSV sanitizing (5.5). Add these three, all implemented as ADK
callbacks so they sit at the framework layer, not buried in prompt text.

### A.1 Input filters — `before_model_callback` / `before_agent_callback`
Screen the user prompt **and** every chunk of untrusted external content
(email bodies, Drive docs, CSV cells) *before* it reaches Gemini.

Checks:
- **Length cap** — truncate/reject oversized input. Controls token cost and
  shrinks the prompt-injection surface.
- **Injection screen** — flag content in an *instruction* position that matches
  patterns like "ignore previous instructions", "forward to", or an embedded
  `send`/email/URL directive. Neutralize by wrapping external content in an
  explicit DATA delimiter, not by trusting the model to notice.
- **PII/secret redaction on ingest** — strip tokens, keys, card numbers before
  they ever hit the model or logs.

```python
def input_filter(callback_context, llm_request):
    """before_model_callback: caps length and neutralizes injection patterns
    in untrusted content before the model sees it. Returns None to allow,
    or an LlmResponse to short-circuit with a safe refusal."""
    text = llm_request.contents[-1].parts[0].text or ""
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
    for pat in INJECTION_PATTERNS:            # compiled regex list
        if pat.search(text.lower()):
            return safe_refusal("Input rejected: possible injected instruction.")
    llm_request.contents[-1].parts[0].text = text
    return None
```

### A.2 Output filters — `after_model_callback` / `after_tool_callback`
Screen model/tool output *before* it's shown to the user or written to the
Sheet.

Checks:
- **Secret/PII redaction on egress** — never surface leaked tokens or PII.
- **Destructive-phrasing block** — reject output that tries to emit a send/
  delete/modify instruction (defense-in-depth with 5.3).
- **Schema validation** — before `append_to_ledger` writes, confirm the
  structured JSON matches the expected transaction schema; reject malformed
  writes rather than corrupting the ledger.

```python
def output_filter(callback_context, llm_response):
    """after_model_callback: redacts secrets/PII and blocks destructive
    phrasing in model output before it reaches the user or the Sheet."""
    text = llm_response.content.parts[0].text or ""
    text = redact_secrets(text)
    if any(w in text.lower() for w in BLOCKED_ACTIONS):
        text = "[blocked: output contained a disallowed action]"
    llm_response.content.parts[0].text = text
    return llm_response
```

### A.3 Rate limiting — `before_model_callback` + app layer
Throttle per session to cap runaway API cost and abuse.

- **Mechanism**: token-bucket or fixed-window counter keyed on `session_id`.
- **Where**: enforce in a `before_model_callback` (blocks the model call when
  over budget) and mirror a coarse cap at the app layer (per-IP / per-session).
- **Limits (starting point)**: e.g. max 5 reconciliation runs/min/session and
  max ~20 model calls/run. Fail *gracefully* — return "rate limit reached, try
  again in Ns", never a stack trace.

```python
def rate_limiter(callback_context, llm_request):
    """before_model_callback: blocks the call if this session exceeded its
    model-call budget for the current window."""
    sid = callback_context.session_id
    if not _bucket(sid).allow():
        return safe_refusal("Rate limit reached — try again shortly.")
    return None
```

### A.4 Callback wiring (all 3 agents)
```python
LlmAgent(
    ...,
    before_agent_callback=input_filter,          # or before_model_callback
    before_model_callback=rate_limiter,
    before_tool_callback=block_destructive_actions,   # existing 5.3
    after_model_callback=output_filter,
    after_tool_callback=output_filter,           # optional, for tool results
)
```
Put the shared implementations in `tools/security.py` alongside
`sanitize_csv_cell` and `block_destructive_actions`.

### A.5 Updated security checklist (additions)
- [ ] Input filter: length cap + injection screen + ingest PII redaction
- [ ] Output filter: egress secret/PII redaction + destructive-phrase block + ledger-schema validation
- [ ] Rate limiter: per-session budget, graceful over-limit response
- [ ] All five callbacks registered on all 3 agents

---

## B. Deployment — Google Cloud Run (chosen)

**Decision: deploy the Streamlit app to Google Cloud Run.** This gives a public
demo URL (`*.run.app`) — no localhost — with **no rewrite of `app.py`**.
Streamlit runs fine on Cloud Run because Cloud Run runs a full container with a
persistent server (unlike Vercel, which is static frontends + short-lived
serverless functions only and cannot host a long-running Streamlit server).
Bonus: it's on Google's own stack, which strengthens the hackathon story.

**Vercel is dropped** — using it would have forced splitting into a Next.js
frontend + FastAPI backend for no functional gain here.

### What to add (only the container plumbing)
```
reconai/
|-- agents.py            # unchanged shape + all 5 callbacks wired
|-- app.py               # unchanged Streamlit app
|-- tools/               # discovery / reconciliation / reporting / security
|-- Dockerfile           # NEW — container for Cloud Run
|-- .dockerignore        # NEW — exclude venv, .env, token.json, credentials.json
|-- requirements.txt
|-- README.md
```

### Dockerfile sketch
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Cloud Run injects $PORT; Streamlit must bind to it and to 0.0.0.0
ENV PORT=8080
CMD streamlit run app.py \
    --server.port=$PORT --server.address=0.0.0.0 \
    --server.headless=true
```

### Deploy steps
1. `gcloud run deploy reconai --source . --region <region> --allow-unauthenticated`
   (builds via Cloud Build, no local Docker needed).
2. **Secrets**: set `GOOGLE_API_KEY` via `--set-env-vars` or Secret Manager —
   never bake `.env` into the image (`.dockerignore` excludes it).
3. **OAuth**: add the deployed `https://<service>.run.app` URL as an authorized
   redirect URI in the Google Cloud OAuth consent config. Prefer minting
   `token.json` at runtime over shipping it in the image.
4. Confirm the public URL loads and the OAuth round-trip works end to end.

### OAuth/token caveat on Cloud Run
Cloud Run containers are **stateless and ephemeral** — a baked-in `token.json`
won't persist across restarts/instances. For a single-user demo that's fine
(re-auth on cold start), but note it in the README so a judge isn't surprised
by a re-login prompt.

---

## C. Updated action items (supersedes items 6–11 of the prior summary)

1. ~~Fix model string (`gemini-3.5-flash` -> `gemini-2.5-flash`) in all files.~~
   **Superseded**: `gemini-2.5-flash` returns `404 NOT_FOUND` ("no longer
   available to new users") as of this build. All files now default to
   `gemini-3.5-flash` (`agents.py`, `tools/discovery_tools.py`,
   `.env.example`) — verify this is still the current model name if you hit
   a 404 again later, model availability shifts over time.
2. Wire `output_key` session-state handoffs in `agents.py`.
3. Build `tools/discovery_tools.py`, `reconciliation_tools.py`,
   `reporting_tools.py`.
4. Build `tools/security.py` — now with **five** callbacks:
   `sanitize_csv_cell`, `block_destructive_actions`, `input_filter`,
   `output_filter`, `rate_limiter`.
5. Register all five callbacks on all three agents.
6. **Deployment: Google Cloud Run** (decided). Keep `app.py`; add `Dockerfile`
   + `.dockerignore`, deploy via `gcloud run deploy --source .`, set secrets as
   env vars, register the `*.run.app` redirect URI for OAuth.
7. Add `tests/` (`test_extraction.py`, `test_security.py` — cover the new
   filters + rate limiter).
8. Write `README.md`; verify `.env` / `credentials.json` / `token.json` are
   git-ignored **and** dockerignored.

---

## D. Discovery scaled back to Gmail-only (credit conservation)

Drive support (`fetch_drive_receipts`, PDF text extraction via `pdfplumber`)
has been **removed**, not just disabled, to cut two real cost sources per
run:

- **Fewer Google API calls** — Drive's `files.list` + one `files.get_media`
  per file no longer happen at all.
- **Smaller Gemini extraction batches** — `extract_invoice_data` only
  processes Gmail results now, so each batched call is shorter (fewer input
  tokens = fewer credits burned per reconciliation run).

Concretely, this changed:

- `tools/discovery_tools.py`: `fetch_drive_receipts` and `_extract_pdf_text`
  deleted; `pdfplumber` import removed (also dropped from
  `requirements.txt` — nothing else used it).
- `tools/google_auth.py` / `test_auth.py`: `drive.readonly` scope removed
  from `SCOPES`. **This means existing `token.json` files minted under the
  old scope list still work fine** (a token with a superset of granted
  scopes doesn't need to shrink), but any *fresh* auth will only request
  Gmail + Sheets.
- `agents.py`: `discovery_agent` no longer imports or lists
  `fetch_drive_receipts` as a tool; its instruction explicitly says
  Gmail-only, no Drive search.

**To re-add Drive later** (once credits/quota aren't the bottleneck):
restore `fetch_drive_receipts`/`_extract_pdf_text` in
`tools/discovery_tools.py`, add `pdfplumber` back to `requirements.txt`, add
`drive.readonly` back to `SCOPES` in both `tools/google_auth.py` and
`test_auth.py`, **delete `token.json` and re-run `test_auth.py`** (a cached
token only carries the scopes it was first granted — it will not
auto-upgrade), and wire the tool back into `discovery_agent` in `agents.py`.

---

## E. Stop re-paying for already-processed emails on every run

Even Gmail-only, repeatedly testing "prepare July reconciliation" was
re-fetching and re-extracting the same emails every single run, because
`get_processed_ids` was only ever checked inside Reconciliation —
*after* Discovery had already spent a Gmail detail fetch and a slice of
the paid `extract_invoice_data` batch on emails that were already in the
ledger from a prior run. Two fixes, both in `tools/discovery_tools.py`
and `agents.py`:

- **`fetch_invoice_emails` gained an `exclude_ids` parameter.** Message IDs
  in it are filtered out of the Gmail search results *before* the
  per-message detail fetch — not just before extraction — so an
  already-processed email costs nothing on a re-run, not even a Gmail API
  call. `discovery_agent` now calls `get_processed_ids` itself (moved from
  Reconciliation's toolset) and passes the result straight into
  `fetch_invoice_emails(exclude_ids=...)`. Reconciliation no longer calls
  `get_processed_ids` at all — Discovery's output is trusted to already be
  new-only, saving a redundant tool call there too.
- **The default Gmail query is now scoped to the current calendar month**
  via `_default_query()` (adds `after:<first-of-month>` to the existing
  keyword match), instead of searching your entire mailbox history on every
  call. Override with explicit `after:`/`before:` bounds when reconciling a
  past month — `discovery_agent`'s instruction tells it to do this when the
  user names a specific period.
- `discovery_agent`'s instruction now references `{sheet_id?}` (note the
  `?` — ADK's optional-state syntax, which resolves to an empty string
  instead of raising `KeyError` when no Sheet ID was set yet). Do not drop
  the `?` if you touch this instruction — the non-optional `{sheet_id}`
  form throws if the key is absent from session state, which happens
  whenever a run starts with no Sheet ID configured.

Covered by four new tests in `tests/test_extraction.py`
(`test_default_query_scopes_to_first_of_current_month`,
`test_fetch_invoice_emails_skips_excluded_ids`,
`test_fetch_invoice_emails_no_exclusions_fetches_everything`,
`test_fetch_invoice_emails_defaults_to_date_scoped_query`).

---

## F. Password-protected PDF attachments — ask, don't crash

Gmail invoice emails sometimes carry a locked PDF attachment (e.g. bank
e-statements). Before this change, `fetch_invoice_emails` never looked at
attachments at all — only the email body was read. Now it does, and
handles the locked case as a first-class outcome rather than a crash,
following the same status-field convention as everything else in
`discovery_tools.py` (section 3: "processed 34 of 36, 2 skipped" beats a
stack trace).

**Design.** This is a Gmail-scope feature, not a Drive one, so it did not
need to be cut alongside section D's Drive removal — `attachments.get` is
already covered by the existing `gmail.readonly` scope, no new OAuth scope
required.

- `tools/discovery_tools.py`:
  - `_find_pdf_attachments(payload)` walks a message's MIME parts looking
    for `.pdf` filenames with an `attachmentId`.
  - `_extract_pdf_text(raw_bytes, password=None)` wraps `pdfplumber.open(...,
    password=...)` and turns `pdfminer.pdfdocument.PDFPasswordIncorrect`
    into `status: "password_required"` instead of letting it raise. Empty
    extracted text (a scanned/image-only PDF) is `status: "failed"`, not
    `"password_required"` — those are different problems and get different
    messages.
  - `fetch_invoice_emails` now downloads and attempts every PDF attachment
    it finds per message (no password), and returns each email's
    `attachments` list plus a flat `locked_attachments` summary (message_id,
    attachment_id, filename, subject) so the agent can relay every locked
    file to the user in one place instead of digging through nested email
    dicts.
  - `unlock_pdf_attachment(message_id, attachment_id, password)` — a new
    tool, re-fetches that one attachment's bytes and retries extraction
    with the supplied password. Returns recovered text on success, or
    `password_required` again (with an "still locked" message) on a wrong
    guess.
- `agents.py`: `discovery_agent` gained `unlock_pdf_attachment` as a tool.
  Its instruction tells it to: report every locked attachment to the user
  by name in plain language after everything else it found; if a later
  user message in the same conversation looks like it's supplying a
  password (the ADK session persists across chat turns in `app.py`, so the
  model can see its own earlier "please provide the password for X" ask),
  call `unlock_pdf_attachment` with that file's ids instead of restarting
  the whole search; and fold recovered attachment text into
  `extract_invoice_data`'s batch as its own item, keyed
  `"{message_id}:{attachment_id}"` so it's tracked separately from its
  parent email's body text.
- **Never log or persist the password.** The instruction explicitly tells
  the agent not to repeat it back in any reply or write it anywhere — only
  the recovered text matters downstream. `output_filter` (section A.2)
  provides a second layer of defense here since it redacts secret-shaped
  strings from model output, though a short human-chosen password won't
  always match its patterns — the instruction-level rule is the primary
  control for this specific case, not the filter.
- `requirements.txt`: `pdfplumber` is back (it was removed alongside Drive
  in section D — re-added here since PDF extraction is needed again, just
  scoped to Gmail attachments this time).

**Refinement — surface password hints instead of asking blind.** Bank
e-statement emails routinely state the PDF password's format directly in
the same email body (e.g. "password is the first 4 letters of your name in
caps + your DOB in DDMM," or "password is your PAN in uppercase"). Since
Discovery already has that email's `body_text` in context by the time it
reports a locked attachment, its instruction now tells it to re-read that
specific email's body for such a hint and quote it back to the user
alongside the ask, instead of asking blind and making the user go dig up
the email themselves. This is deliberately left as instruction-level
natural-language judgment, not a regex in `discovery_tools.py` — hint
phrasing varies too much across banks (HDFC/ICICI/SBI/Axis all phrase it
differently) for a fixed pattern to be worth the false-negative risk;
that's exactly the kind of judgment call this architecture reserves for
the LLM rather than hardcoding (per section 8's reasoning-vs-arithmetic
split). Not independently unit-tested — it's LLM behavior over natural
language, not deterministic tool logic, so it's verified through actual
usage/demo rather than pytest.

Covered by seven new tests in `tests/test_extraction.py`:
`test_extract_pdf_text_reports_password_required`,
`test_extract_pdf_text_succeeds_with_correct_password`,
`test_extract_pdf_text_treats_empty_text_as_failed_not_password`,
`test_unlock_pdf_attachment_returns_recovered_text_on_success`,
`test_unlock_pdf_attachment_reports_still_locked_on_wrong_password`,
`test_fetch_invoice_emails_surfaces_locked_pdf_attachment`, plus the
extended fake-Gmail-service test harness (`_FakeAttachmentsResource`) that
makes the last one possible.
