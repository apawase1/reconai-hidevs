"""tools/security.py — guardrails shared across all three agents.

Eight layers total, per RECONAI_ARCHITECTURE_SKILL.md section 5 and
RECONAI_ARCHITECTURE_ADDENDUM.md section A:

  1. OAuth scopes (5.1)              -> enforced in tools/google_auth.py, not here
  2. Instruction-level guardrail (5.2) -> enforced in agents.py instruction text
  3. block_destructive_actions (5.3) -> before_tool_callback, below
  4. Prompt-injection handling (5.4) -> folded into input_filter, below
  5. sanitize_csv_cell (5.5)         -> called explicitly by discovery_tools.py
  6. input_filter (A.1)              -> before_model_callback, below
  7. output_filter (A.2)             -> after_model_callback, below
  8. rate_limiter (A.3)              -> before_model_callback, below

Callbacks are written against the exact keyword names google-adk==2.4.0
uses at the actual call site (verified by reading
flows/llm_flows/base_llm_flow.py and flows/llm_flows/functions.py in the
installed package, not assumed from type hints — ADK invokes these
callbacks with keyword arguments, so parameter *names* must match exactly
or you get "unexpected keyword argument"):
  before_model_callback(callback_context, llm_request)  -> Optional[LlmResponse]
  after_model_callback(callback_context, llm_response)  -> LlmResponse
  before_tool_callback(tool, args, tool_context)         -> Optional[dict]
"""

import re
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from google.adk.models.llm_response import LlmResponse
from google.genai import types

# --------------------------------------------------------------------------
# 5.5 — CSV formula-injection sanitizing
# --------------------------------------------------------------------------

_FORMULA_LEAD_CHARS = ("=", "+", "-", "@")


def sanitize_csv_cell(value):
    """Neutralizes formula-injection characters in untrusted CSV input
    before it's ever written back into a Google Sheet.

    A malicious cell starting with =, +, -, or @ can trigger formula
    execution if the file is later opened in Excel/Sheets. Prefixing with
    an apostrophe forces Sheets to treat it as literal text.
    """
    if isinstance(value, str) and value and value[0] in _FORMULA_LEAD_CHARS:
        return "'" + value
    return value


# --------------------------------------------------------------------------
# 5.3 — destructive-action blocklist (before_tool_callback)
# --------------------------------------------------------------------------

BLOCKED_ACTIONS = {"delete", "send", "modify", "remove", "erase", "drop", "truncate"}


def block_destructive_actions(
    tool,
    args: Dict[str, Any],
    tool_context,
) -> Optional[Dict]:
    """before_tool_callback: rejects any tool call whose name suggests a
    destructive action, regardless of what the model intended.

    Tripwire, not a filter expected to fire in normal operation — no real
    tool in this project should ever be named anything destructive.
    """
    tool_name_lower = tool.name.lower()
    if any(word in tool_name_lower for word in BLOCKED_ACTIONS):
        return {"status": "failed", "error": f"Blocked: '{tool.name}' is a destructive action, not permitted."}
    return None


# --------------------------------------------------------------------------
# A.1 — input filter (before_model_callback): length cap + injection screen
# --------------------------------------------------------------------------

MAX_INPUT_CHARS = 20_000

# Patterns that suggest untrusted content (an email body, a Drive doc, a CSV
# cell) is trying to issue instructions rather than just be data. Not
# exhaustive — a tripwire layered on top of the "treat content as data"
# instruction in agents.py, not a substitute for it.
_INJECTION_PATTERNS = [
    re.compile(r"ignore (all |the )?(previous|prior|above) instructions", re.I),
    re.compile(r"disregard (all |the )?(previous|prior|above)", re.I),
    re.compile(r"forward (this|it|the data|all) to\b", re.I),
    re.compile(r"send (this|it|the data|all|an? email) to\b", re.I),
    re.compile(r"you are now\b", re.I),
    re.compile(r"new instructions?:", re.I),
    re.compile(r"system prompt", re.I),
]


def _refusal(text: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        turn_complete=True,
    )


def input_filter(callback_context, llm_request) -> Optional[LlmResponse]:
    """before_model_callback: caps length and screens for prompt-injection
    patterns in the latest turn before it reaches Gemini.

    Returns None to allow the call through, or an LlmResponse to
    short-circuit with a safe refusal instead of calling the model.
    """
    contents = getattr(llm_request, "contents", None) or []
    if not contents:
        return None

    last = contents[-1]
    parts = getattr(last, "parts", None) or []
    for part in parts:
        text = getattr(part, "text", None)
        if not text:
            continue
        if len(text) > MAX_INPUT_CHARS:
            part.text = text[:MAX_INPUT_CHARS] + "\n[...truncated, exceeded input length cap...]"
            text = part.text
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                return _refusal(
                    "Input rejected: content resembled an embedded instruction "
                    "override rather than reconciliation data. Discovery/Reconciliation "
                    "agents treat email, document, and CSV content as data only."
                )
    return None


# --------------------------------------------------------------------------
# A.2 — output filter (after_model_callback): redaction + destructive-phrase block
# --------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),          # Google API key shape
    re.compile(r"ya29\.[0-9A-Za-z\-_]+"),             # Google OAuth access token shape
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
]


def _redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def output_filter(callback_context, llm_response: LlmResponse) -> LlmResponse:
    """after_model_callback: redacts secrets before output reaches the user
    or gets written to the Sheet, and blocks output that itself tries to
    describe performing a disallowed destructive action.
    """
    content = getattr(llm_response, "content", None)
    if not content or not getattr(content, "parts", None):
        return llm_response

    for part in content.parts:
        if not getattr(part, "text", None):
            continue
        text = _redact_secrets(part.text)
        lowered = text.lower()
        if any(f"i will {w}" in lowered or f"i've {w}" in lowered or f"i have {w}" in lowered for w in BLOCKED_ACTIONS):
            text = "[blocked: output described performing a disallowed destructive action]"
        part.text = text

    return llm_response


# --------------------------------------------------------------------------
# A.3 — rate limiter (before_model_callback): per-session token-bucket
# --------------------------------------------------------------------------

MAX_MODEL_CALLS_PER_WINDOW = 20
WINDOW_SECONDS = 60

_call_log: Dict[str, deque] = defaultdict(deque)


def _session_key(callback_context) -> str:
    # CallbackContext exposes .session (with an .id) in this ADK version;
    # fall back to invocation_id so the limiter still degrades gracefully if
    # the session object isn't populated yet.
    session = getattr(callback_context, "session", None)
    sid = getattr(session, "id", None) if session else None
    return sid or getattr(callback_context, "invocation_id", "unknown-session")


def rate_limiter(callback_context, llm_request) -> Optional[LlmResponse]:
    """before_model_callback: blocks the call if this session exceeded its
    model-call budget for the current rolling window. Fails gracefully
    (a plain refusal message) rather than raising.
    """
    key = _session_key(callback_context)
    now = time.time()
    log = _call_log[key]

    while log and now - log[0] > WINDOW_SECONDS:
        log.popleft()

    if len(log) >= MAX_MODEL_CALLS_PER_WINDOW:
        return _refusal(
            f"Rate limit reached ({MAX_MODEL_CALLS_PER_WINDOW} model calls per "
            f"{WINDOW_SECONDS}s per session). Try again shortly."
        )

    log.append(now)
    return None
