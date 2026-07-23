"""tools/security.py — guardrails shared across all three agents."""

import re
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from google.adk.models.llm_response import LlmResponse
from google.genai import types

_FORMULA_LEAD_CHARS = ("=", "+", "-", "@")


def sanitize_csv_cell(value):
    """Neutralizes formula-injection characters before writing untrusted text into a Google Sheet."""
    if isinstance(value, str) and value and value[0] in _FORMULA_LEAD_CHARS:
        return "'" + value
    return value


BLOCKED_ACTIONS = {"delete", "send", "modify", "remove", "erase", "drop", "truncate"}


def block_destructive_actions(
    tool,
    args: Dict[str, Any],
    tool_context,
) -> Optional[Dict]:
    """before_tool_callback: rejects any tool call whose name suggests a destructive action."""
    tool_name_lower = tool.name.lower()
    if any(word in tool_name_lower for word in BLOCKED_ACTIONS):
        return {"status": "failed", "error": f"Blocked: '{tool.name}' is a destructive action, not permitted."}
    return None


MAX_INPUT_CHARS = 20_000

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
    """Builds a turn-complete LlmResponse carrying a plain refusal message."""
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        turn_complete=True,
    )


def input_filter(callback_context, llm_request) -> Optional[LlmResponse]:
    """before_model_callback: caps input length and screens for prompt-injection patterns."""
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


_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"ya29\.[0-9A-Za-z\-_]+"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
]


def _redact_secrets(text: str) -> str:
    """Replaces any matched API key / OAuth token / private key shape with [REDACTED]."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def output_filter(callback_context, llm_response: LlmResponse) -> LlmResponse:
    """after_model_callback: redacts secrets and blocks output describing a destructive action."""
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


MAX_MODEL_CALLS_PER_WINDOW = 20
WINDOW_SECONDS = 60

_call_log: Dict[str, deque] = defaultdict(deque)


def _session_key(callback_context) -> str:
    """Returns a stable per-session key for the rate limiter's call log."""
    session = getattr(callback_context, "session", None)
    sid = getattr(session, "id", None) if session else None
    return sid or getattr(callback_context, "invocation_id", "unknown-session")


def rate_limiter(callback_context, llm_request) -> Optional[LlmResponse]:
    """before_model_callback: blocks the call once a session exceeds its rolling-window model-call budget."""
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
