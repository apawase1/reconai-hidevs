"""app.py — ReconAI Streamlit UI: dashboard, chat loop, and account controls wired to the ADK Runner."""

import hashlib
import json
import os
import time
from datetime import datetime

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from google.genai import types

from agents import root_agent
from tools.google_auth import (
    GoogleSignInError,
    begin_interactive_sign_in,
    clear_cached_credentials,
    finish_credentials,
    get_signed_in_email,
)
from tools.export_tools import export_report_to_pdf
from tools.reporting_tools import generate_monthly_report, save_report_to_sheet

load_dotenv()

st.set_page_config(page_title="ReconAI", page_icon="\U0001F4CA", layout="wide")

THEME_DARK = {
    "BG": "#040A14",
    "SURFACE": "#081726",
    "SURFACE_ALT": "#0C1F33",
    "BORDER": "rgba(34, 211, 238, 0.22)",
    "BORDER_GLOW": "rgba(34, 211, 238, 0.55)",
    "TEXT_PRIMARY": "#E8FBFF",
    "TEXT_MUTED": "#5E86A0",
    "TEAL": "#22D3EE",
    "PURPLE": "#3B82F6",
    "CORAL": "#FF3B5C",
    "AMBER": "#38BDF8",
    "BLUE": "#0EA5E9",
    "GLOW_CYAN": "0 0 12px rgba(34, 211, 238, 0.55)",
    "GLOW_CYAN_SOFT": "0 0 18px rgba(34, 211, 238, 0.18)",
    "GLOW_RED": "0 0 12px rgba(255, 59, 92, 0.55)",
    "CATEGORY_COLORS": ["#22D3EE", "#3B82F6", "#38BDF8", "#818CF8", "#2DD4BF", "#93C5FD", "#0EA5E9"],
    "ACCENT_RGB": "34, 211, 238",
    "ACCENT_RGB_2": "59, 130, 246",
    "GRID_TEXTURE": True,
}

_active_theme = THEME_DARK

BG = _active_theme["BG"]
SURFACE = _active_theme["SURFACE"]
SURFACE_ALT = _active_theme["SURFACE_ALT"]
BORDER = _active_theme["BORDER"]
BORDER_GLOW = _active_theme["BORDER_GLOW"]
TEXT_PRIMARY = _active_theme["TEXT_PRIMARY"]
TEXT_MUTED = _active_theme["TEXT_MUTED"]
TEAL = _active_theme["TEAL"]
PURPLE = _active_theme["PURPLE"]
CORAL = _active_theme["CORAL"]
AMBER = _active_theme["AMBER"]
BLUE = _active_theme["BLUE"]
GLOW_CYAN = _active_theme["GLOW_CYAN"]
GLOW_CYAN_SOFT = _active_theme["GLOW_CYAN_SOFT"]
GLOW_RED = _active_theme["GLOW_RED"]
CATEGORY_COLORS = _active_theme["CATEGORY_COLORS"]


def _inject_theme():
    """Injects the app's single dark-theme stylesheet and all custom component CSS."""
    accent = _active_theme["ACCENT_RGB"]
    accent2 = _active_theme["ACCENT_RGB_2"]
    grid_texture = (
        f"repeating-linear-gradient(0deg, rgba({accent},0.025) 0px, rgba({accent},0.025) 1px, transparent 1px, transparent 42px),"
        f"repeating-linear-gradient(90deg, rgba({accent},0.025) 0px, rgba({accent},0.025) 1px, transparent 1px, transparent 42px),"
        if _active_theme["GRID_TEXTURE"]
        else ""
    )
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&family=Share+Tech+Mono&display=swap');

        @keyframes reconPulse {{
            0%   {{ box-shadow: 0 0 0 0 rgba({accent}, 0.55); }}
            70%  {{ box-shadow: 0 0 0 8px rgba({accent}, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba({accent}, 0); }}
        }}

        .stApp {{
            background:
                radial-gradient(circle at 15% 0%, rgba({accent},0.06), transparent 40%),
                radial-gradient(circle at 85% 100%, rgba({accent2},0.05), transparent 45%),
                {grid_texture}
                {BG};
        }}
        [data-testid="stSidebar"] {{
            background: {SURFACE};
            border-right: 1px solid {BORDER};
        }}
        [data-testid="stSidebar"] * {{
            color: {TEXT_PRIMARY};
        }}
        h1, h2, h3, p, span, label, div {{
            color: {TEXT_PRIMARY};
        }}
        .stCaption, [data-testid="stCaptionContainer"] {{
            color: {TEXT_MUTED} !important;
        }}
        [data-testid="stChatMessage"] {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 16px;
            box-shadow: {GLOW_CYAN_SOFT};
        }}
        [data-testid="stStatusWidget"], [data-testid="stExpander"] {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 12px;
        }}
        .stTextInput input, .stTextArea textarea {{
            background: {SURFACE_ALT} !important;
            color: {TEXT_PRIMARY} !important;
            border: 1px solid {BORDER} !important;
        }}
        .stTextInput input:focus, .stTextArea textarea:focus {{
            border: 1px solid {BORDER_GLOW} !important;
            box-shadow: {GLOW_CYAN} !important;
        }}
        .stButton button {{
            background: {SURFACE_ALT} !important;
            color: {TEAL} !important;
            border: 1px solid {BORDER_GLOW} !important;
            font-family: 'Share Tech Mono', monospace;
            letter-spacing: 0.04em;
        }}
        .stButton button:hover {{
            box-shadow: {GLOW_CYAN} !important;
        }}
        [data-testid="stFileUploaderDropzone"] {{
            background: {SURFACE_ALT};
            border: 1px dashed {BORDER};
        }}
        [data-testid="stFileUploaderDropzone"] button {{
            background: {SURFACE} !important;
            color: {TEXT_PRIMARY} !important;
            border: 1px solid {BORDER} !important;
        }}
        [data-testid="stFileUploaderDropzone"] span,
        [data-testid="stFileUploaderDropzone"] small,
        [data-testid="stFileUploaderDropzone"] div {{
            color: {TEXT_MUTED} !important;
        }}
        [data-testid="stChatInput"] {{
            background: {SURFACE_ALT} !important;
            border: 1px solid {BORDER} !important;
        }}
        [data-testid="stChatInputTextArea"], [data-testid="stChatInput"] textarea {{
            background: {SURFACE_ALT} !important;
            color: {TEXT_PRIMARY} !important;
        }}
        [data-testid="stChatInputSubmitButton"] {{
            background: {SURFACE_ALT} !important;
            color: {TEAL} !important;
        }}

        [data-testid="stAppDeployButton"] {{
            display: none !important;
        }}
        [data-testid="stToolbar"], [data-testid="stAppToolbar"] {{
            z-index: 999992;
        }}
        /* Streamlit's own status widget (the "Running..." spinner during a
        rerun, and the "File change. Rerun / Always rerun" prompt when the
        app's source file changes on disk) lays out inside that same native
        toolbar, in the same screen region as our fixed Clear-screen/account
        bar — the toolbar's z-index above beats ours, so it was rendering
        on top of "Clear screen" and the account icon. Taking it out of that
        flex row and re-anchoring it below our bar (same right edge) keeps
        it visible without it ever colliding with our own controls. */
        [data-testid="stStatusWidget"] {{
            position: fixed !important;
            top: calc(3.75rem + 10px) !important;
            right: 3.4rem !important;
            z-index: 999989 !important;
        }}
        /* Streamlit's own toast notifications (st.toast(...)) always render
        fixed at top:60px, right:0 — exactly flush against the bottom edge
        of our fixed Clear-screen/account bar above, so they read as a
        dropdown popping out of it. Push them down with real breathing
        room so they're clearly their own, separate thing. */
        [data-testid="stToastContainer"] {{
            top: calc(3.75rem + 16px) !important;
        }}
        .st-key-recon-topbar {{
            position: fixed;
            top: 0;
            height: 3.75rem;
            right: 3.4rem;
            z-index: 999991;
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            width: auto !important;
            gap: 8px !important;
        }}
        .st-key-recon-topbar[data-testid="stHorizontalBlock"] {{
            align-items: center !important;
        }}
        .st-key-recon-topbar [data-testid="stElementContainer"],
        .st-key-recon-topbar [data-testid="stLayoutWrapper"],
        .st-key-recon-topbar [data-testid="stPopover"] {{
            width: auto !important;
            flex: 0 0 auto !important;
        }}
        .st-key-gate_signin a[data-testid^="stBaseLinkButton"] {{
            background: #131314 !important;
            color: #E3E3E3 !important;
            border: 1px solid #8E918F !important;
            border-radius: 4px !important;
            font-family: 'Roboto', 'Helvetica Neue', Arial, sans-serif !important;
            font-weight: 500 !important;
            font-size: 14px !important;
            letter-spacing: 0.15px !important;
            height: 34px !important;
            min-height: 34px !important;
            padding: 0 12px 0 38px !important;
            box-shadow: none !important;
            background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'><path fill='%23EA4335' d='M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z'/><path fill='%234285F4' d='M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z'/><path fill='%23FBBC05' d='M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24s.92 7.54 2.56 10.78l7.97-6.19z'/><path fill='%2334A853' d='M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z'/></svg>") !important;
            background-repeat: no-repeat !important;
            background-position: 12px center !important;
            background-size: 18px 18px !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            text-decoration: none !important;
        }}
        .st-key-gate_signin a[data-testid^="stBaseLinkButton"]:hover {{
            background-color: #1E1F20 !important;
            border-color: #E3E3E3 !important;
            box-shadow: none !important;
        }}
        .st-key-recon-topbar [data-testid="stPopover"] button {{
            background: {SURFACE_ALT} !important;
            color: {TEXT_MUTED} !important;
            border: 1px solid {BORDER} !important;
            border-radius: 50% !important;
            width: 34px !important;
            height: 34px !important;
            min-height: 34px !important;
            padding: 0 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            box-shadow: none !important;
        }}
        .st-key-recon-topbar [data-testid="stPopover"] button [data-testid="stIconMaterial"] {{
            font-size: 22px !important;
            width: 22px !important;
            height: 22px !important;
            line-height: 1 !important;
            margin: 0 0 0 -3px !important;
            padding: 0 !important;
        }}
        .st-key-recon-topbar [data-testid="stPopover"] button p {{
            font-size: 22px !important;
            line-height: 1 !important;
            margin: 0 !important;
        }}
        .st-key-recon-topbar [data-testid="stPopover"] button:hover {{
            color: {TEAL} !important;
            border-color: {BORDER_GLOW} !important;
        }}
        .st-key-recon-topbar [data-testid="stPopoverButton"] svg {{
            display: none !important;
        }}
        [data-testid="stPopoverBody"] {{
            background: {SURFACE} !important;
            border: 1px solid {BORDER} !important;
            box-shadow: {GLOW_CYAN_SOFT} !important;
        }}
        [data-testid="stPopoverBody"] * {{
            color: {TEXT_PRIMARY};
        }}
        .st-key-recon_switch_account button, .st-key-recon_logout button {{
            background: {SURFACE_ALT} !important;
            border: 1px solid {BORDER} !important;
            font-family: 'Share Tech Mono', monospace !important;
            font-size: 13px !important;
        }}
        .st-key-recon_logout button {{
            color: {CORAL} !important;
        }}
        .st-key-recon_switch_account button:hover {{
            color: {TEAL} !important;
            border-color: {BORDER_GLOW} !important;
        }}
        .st-key-recon_logout button:hover {{
            border-color: {CORAL} !important;
        }}

        .recon-info-row {{
            position: relative;
        }}
        .recon-info-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 15px;
            height: 15px;
            border-radius: 50%;
            background: {SURFACE_ALT};
            border: 1px solid {BORDER};
            color: {TEXT_MUTED};
            font-size: 10px;
            line-height: 1;
            cursor: help;
            margin-left: 6px;
            vertical-align: middle;
        }}
        .recon-info-badge:hover {{
            border-color: {BORDER_GLOW};
            color: {TEAL};
        }}
        .recon-info-row .recon-info-tooltip {{
            visibility: hidden;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.12s ease;
            position: absolute;
            z-index: 999995;
            left: 0;
            right: 0;
            top: 100%;
            margin-top: 6px;
            width: auto;
            max-width: 100%;
            box-sizing: border-box;
            background: {SURFACE};
            border: 1px solid {BORDER_GLOW};
            border-radius: 8px;
            padding: 12px 14px;
            font-size: 12px;
            line-height: 1.65;
            color: {TEXT_PRIMARY};
            box-shadow: {GLOW_CYAN_SOFT};
            white-space: normal;
            text-align: left;
            cursor: auto;
        }}
        .recon-info-badge:hover ~ .recon-info-tooltip {{
            visibility: visible;
            opacity: 1;
        }}
        .st-key-recon_topbtn_clear button {{
            background: {SURFACE_ALT} !important;
            color: {TEXT_MUTED} !important;
            border: 1px solid {BORDER} !important;
            border-radius: 4px !important;
            font-family: 'Share Tech Mono', monospace !important;
            font-size: 13px !important;
            height: 34px !important;
            min-height: 34px !important;
            padding: 0 12px !important;
            box-shadow: none !important;
        }}
        .st-key-recon_topbtn_clear button:hover {{
            color: {TEAL} !important;
            border-color: {BORDER_GLOW} !important;
        }}

        .recon-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 4px 0 20px 0;
        }}
        .recon-brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .recon-logo {{
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: {SURFACE_ALT};
            border: 1px solid {BORDER_GLOW};
            box-shadow: {GLOW_CYAN};
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Orbitron', sans-serif;
            font-weight: 700;
            font-size: 18px;
            color: {TEAL};
            text-shadow: {GLOW_CYAN};
        }}
        .recon-title {{
            font-family: 'Orbitron', sans-serif;
            font-size: 21px;
            font-weight: 700;
            letter-spacing: 0.04em;
            margin: 0;
            color: {TEXT_PRIMARY};
            text-shadow: 0 0 10px rgba({accent}, 0.35);
        }}
        .recon-subtitle {{
            font-size: 12px;
            color: {TEXT_MUTED};
            margin: 2px 0 0 0;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .recon-live-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: {TEAL};
            animation: reconPulse 2s infinite;
        }}

        .metric-row {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 14px;
            margin-bottom: 18px;
        }}
        .metric-card {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 14px;
            padding: 18px 20px;
            box-shadow: {GLOW_CYAN_SOFT};
        }}
        .metric-label {{
            font-size: 11px;
            color: {TEXT_MUTED};
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .metric-value {{
            font-family: 'Share Tech Mono', monospace;
            font-size: 26px;
            font-weight: 700;
            line-height: 1.1;
            color: {TEXT_PRIMARY};
            text-shadow: 0 0 10px rgba({accent}, 0.4);
        }}
        .metric-badge {{
            display: inline-block;
            margin-top: 8px;
            padding: 3px 10px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.04em;
        }}

        .panel {{
            background: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 14px;
            padding: 20px 22px;
            position: relative;
            box-shadow: {GLOW_CYAN_SOFT};
            overflow: hidden;
            margin-bottom: 14px;
        }}
        [data-testid="stHorizontalBlock"] {{
            align-items: flex-start !important;
        }}
        .panel::before, .panel::after {{
            content: "";
            position: absolute;
            width: 14px;
            height: 14px;
            border-color: {BORDER_GLOW};
            border-style: solid;
            opacity: 0.9;
        }}
        .panel::before {{
            top: -1px; left: -1px;
            border-width: 2px 0 0 2px;
            border-radius: 6px 0 0 0;
        }}
        .panel::after {{
            bottom: -1px; right: -1px;
            border-width: 0 2px 2px 0;
            border-radius: 0 0 6px 0;
        }}
        .panel-title {{
            font-size: 13px;
            font-weight: 700;
            margin-bottom: 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: {TEAL};
        }}

        .ledger-balance-card {{
            background: linear-gradient(135deg, {SURFACE_ALT}, {SURFACE});
            border: 1px solid {BORDER_GLOW};
            border-radius: 14px;
            padding: 22px;
            margin-bottom: 14px;
            box-shadow: {GLOW_CYAN};
        }}
        .ledger-balance-label {{
            font-size: 11px;
            color: {TEXT_MUTED};
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}
        .ledger-balance-value {{
            font-family: 'Share Tech Mono', monospace;
            font-size: 30px;
            font-weight: 700;
            margin: 6px 0 14px 0;
            color: {TEAL};
            text-shadow: {GLOW_CYAN};
        }}
        .ledger-stat-row {{
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            color: {TEXT_MUTED};
            padding: 6px 0;
            border-top: 1px solid {BORDER};
        }}
        .ledger-stat-row span:last-child {{
            color: {TEXT_PRIMARY};
            font-weight: 600;
            font-family: 'Share Tech Mono', monospace;
        }}

        .txn-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 0;
            border-top: 1px solid {BORDER};
        }}
        .txn-left {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .txn-avatar {{
            width: 38px;
            height: 38px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 14px;
            color: {BG};
            flex-shrink: 0;
        }}
        .txn-vendor {{
            font-size: 14px;
            font-weight: 600;
            margin: 0;
        }}
        .txn-meta {{
            font-size: 12px;
            color: {TEXT_MUTED};
            margin: 0;
        }}
        .txn-amount {{
            font-family: 'Share Tech Mono', monospace;
            font-size: 15px;
            font-weight: 700;
        }}
        .tag {{
            display: inline-block;
            font-size: 10px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
            margin-left: 6px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}

        .missing-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-top: 1px solid {BORDER};
            font-size: 13px;
        }}

        .empty-hint {{
            color: {TEXT_MUTED};
            font-size: 13px;
            padding: 12px 0;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _vendor_avatar(vendor: str) -> str:
    """Builds a colored initial-letter avatar div for a transaction's vendor name."""
    initial = (vendor or "?").strip()[:1].upper() or "?"
    idx = int(hashlib.md5(vendor.encode("utf-8")).hexdigest(), 16) % len(CATEGORY_COLORS)
    color = CATEGORY_COLORS[idx]
    return f'<div class="txn-avatar" style="background:{color}; box-shadow: 0 0 10px {color}99;">{initial}</div>'


_ACCOUNT_AVATAR_COLORS = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#7C4DFF", "#00ACC1"]


def _account_avatar_html(email: str, size: int = 32) -> str:
    """Builds a colored initial-letter avatar div for the signed-in account, deterministic from its email."""
    initial = (email or "?").strip()[:1].upper() or "?"
    idx = int(hashlib.md5((email or "?").encode("utf-8")).hexdigest(), 16) % len(_ACCOUNT_AVATAR_COLORS)
    color = _ACCOUNT_AVATAR_COLORS[idx]
    font_size = round(size * 0.46)
    return (
        f'<div style="width:{size}px; height:{size}px; min-width:{size}px; border-radius:50%; '
        f'background:{color}; display:flex; align-items:center; justify-content:center; '
        f'color:#fff; font-family:\'Roboto\',\'Helvetica Neue\',Arial,sans-serif; '
        f'font-weight:600; font-size:{font_size}px;">{initial}</div>'
    )


MOCK_RECONCILED_DATA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "mock_data", "reconciled_output_sample.json"
)


def _load_mock_reconciled_data() -> dict:
    """Reads the UI-testing mock dataset from mock_data/reconciled_output_sample.json."""
    with open(MOCK_RECONCILED_DATA_PATH) as f:
        return json.load(f)


def _write_session_state(session_service, session_id: str, state_delta: dict) -> None:
    """Persists a state_delta into ADK session state via append_event, since a bare assignment doesn't stick."""
    import asyncio

    from google.adk.events import Event, EventActions

    async def _write():
        session = await session_service.get_session(
            app_name="reconai", user_id="streamlit-user", session_id=session_id
        )
        event = Event(author="system", actions=EventActions(state_delta=state_delta))
        await session_service.append_event(session, event)

    asyncio.run(_write())


def _load_mock_data(session_service, session_id: str) -> None:
    """Writes the mock reconciled dataset into session state with no Gmail/Gemini/Sheets calls."""
    _write_session_state(session_service, session_id, {"reconciled_data": _load_mock_reconciled_data()})
    st.session_state.reconciled_data_is_mock = True


_GREETING_WORDS = {
    "hi", "hii", "hiii", "hiya", "hello", "hey", "heya", "yo", "sup",
    "hola", "namaste", "howdy", "good morning", "good afternoon",
    "good evening", "morning", "evening",
}


def _is_pure_greeting(text: str) -> bool:
    """True only if the whole message is just a greeting (e.g. 'hi', 'Hello!') — a message that merely starts with one, like 'hi, reconcile July', still runs the full pipeline."""
    normalized = text.strip().lower().strip("!.,?~ ")
    return normalized in _GREETING_WORDS


_GREETING_REPLY = (
    "Hi! I'm ReconAI — I reconcile your invoices, receipts, and bank "
    "transactions from Gmail into a monthly report: duplicates, recurring "
    "subscriptions, GST-eligible spend, pending payments, and more.\n\n"
    "Try something like **\"Prepare July reconciliation\"** to get started."
)


_inject_theme()

if not os.getenv("GOOGLE_API_KEY"):
    st.error("GOOGLE_API_KEY not found. Check your .env file.")
    st.stop()

_header_col, _export_col = st.columns([5, 3])
with _header_col:
    st.markdown(
        """
        <div class="recon-header">
            <div class="recon-brand">
                <div class="recon-logo">R</div>
                <div>
                    <p class="recon-title">RECONAI</p>
                    <p class="recon-subtitle">
                        <span class="recon-live-dot"></span>
                        Discovery &rarr; Reconciliation &rarr; Reporting &middot; Google ADK
                    </p>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
_export_slot = _export_col.empty()


if "gmail_email" not in st.session_state:
    st.session_state.gmail_email = None
if "gmail_authed" not in st.session_state:
    st.session_state.gmail_authed = False


def _reset_session_for_new_account() -> None:
    """Wipes all session state so the next signed-in account starts from a completely blank slate."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def _clear_screen() -> None:
    """Resets the chat transcript and dashboard to blank, keeping the current sign-in intact."""
    for key in ("messages", "adk_session_id"):
        if key in st.session_state:
            del st.session_state[key]


def _start_sign_in() -> None:
    """Starts the OAuth flow's local redirect server (non-blocking) and stashes the pending flow + auth_url in session state."""
    try:
        auth_url, pending = begin_interactive_sign_in()
        st.session_state._oauth_auth_url = auth_url
        st.session_state._oauth_pending = pending
    except GoogleSignInError as e:
        st.session_state._oauth_error = str(e)


def _poll_sign_in() -> bool:
    """Checks whether the sign-in tab has redirected back yet. Returns True while still waiting; on success or failure it updates session state and returns False."""
    pending = st.session_state.get("_oauth_pending")
    if pending is None:
        return False
    try:
        creds = pending.poll()
    except Exception:
        st.session_state._oauth_error = (
            "Sign-in didn't finish — this can happen if the tab was closed "
            "or the sign-in was cancelled. Please try again."
        )
        return False
    if creds is None:
        return True
    finish_credentials(creds)
    st.session_state.gmail_authed = True
    st.session_state.gmail_email = get_signed_in_email(creds)
    for key in ("_oauth_pending", "_oauth_auth_url"):
        st.session_state.pop(key, None)
    return False


with st.container(key="recon-topbar", horizontal=True, vertical_alignment="center"):
    if st.session_state.gmail_authed:
        if st.button("Clear screen", key="recon_topbtn_clear", help="Start a fresh run — keeps you signed in."):
            _clear_screen()
            st.rerun()
        _email = st.session_state.gmail_email or "your account"
        with st.popover("", icon=":material/account_circle:", help=f"Account: {_email}"):
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:10px; padding:2px 4px 10px;">
                    {_account_avatar_html(_email, size=36)}
                    <div style="overflow:hidden;">
                        <div style="font-size:13px; font-weight:600; white-space:nowrap;
                                    overflow:hidden; text-overflow:ellipsis; max-width:220px;">{_email}</div>
                        <div style="font-size:11px; color:{TEXT_MUTED};">Signed in with Google</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("🔁 Switch account", key="recon_switch_account", use_container_width=True,
                         help="Sign out and immediately pick a different Google account — the app keeps running."):
                clear_cached_credentials()
                _reset_session_for_new_account()
                st.rerun()
            if st.button("🚪 Log out", key="recon_logout", use_container_width=True,
                         help="Sign out and shut down this local app."):
                clear_cached_credentials()
                _reset_session_for_new_account()
                st.info("Signed out — shutting down the local app now. You can close this tab.")
                os._exit(0)

if not st.session_state.gmail_authed:
    # The sign-in gate polls once a second (time.sleep(1) + st.rerun() below)
    # to check whether the OAuth redirect has landed. Each of those reruns
    # briefly puts Streamlit back into its "running" state, which flashes the
    # native toolbar's spinner/Stop control on and off once a second the whole
    # time you're on this screen. There's nothing meaningful to stop mid-poll,
    # so it's just hidden here rather than left flickering.
    st.markdown(
        '<style>[data-testid="stStatusWidget"] { display: none !important; }</style>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div style="max-width:440px; margin: 60px auto 24px; text-align:center;">
            <div class="recon-logo" style="margin:0 auto 18px; width:56px; height:56px; font-size:24px;">R</div>
            <h2 style="margin:0 0 8px; font-family:'Orbitron', sans-serif; letter-spacing:0.04em;">Sign in to ReconAI</h2>
            <p style="color:{TEXT_MUTED}; font-size:14px; line-height:1.5;">
                Connect your Google account so ReconAI can read invoices and
                receipts from Gmail (read-only — it can never send, delete,
                or modify anything). Signing in opens a new tab to pick your
                account, then closes itself and brings you right back.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # The OAuth URL is prepared as soon as this screen is shown, before any
    # click, so the button below is a real link (st.link_button -> a real
    # <a target="_blank">) from the very first render — a script-triggered
    # window.open() call has to fire in the same tick as a genuine click to
    # not get silently blocked by the browser's popup blocker, and by the
    # time a rerun comes back from the server that window has passed, so it
    # was getting blocked more often than not. A real link is never blocked.
    if st.session_state.get("_oauth_pending") is None and not st.session_state.get("_oauth_error"):
        _start_sign_in()

    _gate_l, _gate_c, _gate_r = st.columns([1, 1, 1])
    if st.session_state.get("_oauth_error"):
        with _gate_c:
            st.error(f"⚠️ {st.session_state.pop('_oauth_error')}")
            if st.button("Try again", key="gate_signin_retry", use_container_width=True):
                st.rerun()
    elif st.session_state.get("_oauth_pending") is not None:
        with _gate_c:
            with st.spinner("Waiting for you to finish signing in..."):
                with st.container(key="gate_signin"):
                    st.link_button(
                        " Sign in with Google",
                        st.session_state._oauth_auth_url,
                        use_container_width=True,
                    )
                still_waiting = _poll_sign_in()
        if still_waiting:
            time.sleep(1)
        st.rerun()
    st.stop()


@st.cache_resource
def get_runner():
    """Builds (once, cached) the ADK InMemoryRunner wrapping root_agent."""
    from google.adk.runners import InMemoryRunner
    return InMemoryRunner(agent=root_agent, app_name="reconai")


runner = get_runner()

with st.sidebar:
    st.header("Reconciliation setup")
    st.caption(
        "🔒 **No ledger, no manual export step** — paste a Google Sheet ID "
        "below and every run auto-writes its report to that Sheet's first "
        "tab (overwriting it, never appending). Skip the field entirely if "
        "you don't want that — reconciliation works exactly the same "
        "either way, nothing else depends on it."
    )
    st.markdown(
        f"""
        <div class="recon-info-row" style="font-size:14px; margin-bottom:4px; display:flex; align-items:center; flex-wrap:wrap;">
            <span>Google Sheet ID <span style="color:{TEXT_MUTED}; margin-left:4px;">(optional, auto-export)</span></span>
            <span class="recon-info-badge">i</span>
            <div class="recon-info-tooltip">
                <b>Setting up the Sheet:</b><br>
                1. Create a blank Sheet at <b>sheets.new</b> while signed into
                the <b>same Google account</b> you signed into ReconAI with.<br>
                2. Different account? Either switch accounts, or share the
                Sheet with <b>Editor</b> access to your ReconAI account.<br>
                3. Copy the ID from the URL — the part between
                <code>/d/</code> and <code>/edit</code>.<br>
                4. Paste just that ID below — every run after that
                auto-saves here, no button to click.<br><br>
                Each save overwrites the Sheet's <b>first tab</b> — it
                never appends or touches any other tab.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    sheet_id_input = st.text_input(
        "Google Sheet ID",
        value="",
        placeholder="Paste the ID from the Sheet's URL",
        key="sheet_id_input",
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(
        "Guardrails active: readonly Gmail scope, input/output filters, "
        "rate limiting, destructive-action blocklist, CSV sanitizing."
    )

if "adk_session_id" not in st.session_state:
    session = runner.session_service.create_session_sync(
        app_name="reconai", user_id="streamlit-user", state={}
    )
    st.session_state.adk_session_id = session.id

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.divider()
    with st.expander("UI testing (no API calls)"):
        st.caption(
            "Loads fake reconciliation data straight into session state so "
            "you can see the dashboard styling without touching Gmail or "
            "Gemini — zero credits spent."
        )
        if st.button("Load mock data"):
            _load_mock_data(runner.session_service, st.session_state.adk_session_id)
            st.rerun()

for msg in st.session_state.messages:
    st.chat_message(msg["role"]).markdown(msg["content"])


prompt = st.chat_input("e.g. 'Prepare July reconciliation' or ask a question about your ledger")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.chat_message("user").markdown(prompt)

    if _is_pure_greeting(prompt):
        st.chat_message("assistant").markdown(_GREETING_REPLY)
        st.session_state.messages.append({"role": "assistant", "content": _GREETING_REPLY})
    else:
        content = types.Content(role="user", parts=[types.Part(text=prompt)])

        final_text_chunks = []
        tool_reconciled_data = None
        with st.chat_message("assistant"):
            with st.status("Running ReconAI pipeline...", expanded=True) as status:
                for event in runner.run(
                    user_id="streamlit-user",
                    session_id=st.session_state.adk_session_id,
                    new_message=content,
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                status.write(f"**[{event.author}]** {part.text}")
                                if event.author == "reporting_agent":
                                    final_text_chunks.append(part.text)
                    for fr in event.get_function_responses():
                        if fr.name == "check_duplicates_and_budget" and isinstance(fr.response, dict):
                            tool_reconciled_data = fr.response
                status.update(label="Pipeline finished", state="complete")

            final_text = "\n\n".join(final_text_chunks) if final_text_chunks else "(no reporting output produced)"
            st.markdown(final_text)

        st.session_state.messages.append({"role": "assistant", "content": final_text})

        if tool_reconciled_data is not None:
            st.session_state.reconciled_data_is_mock = False
            _write_session_state(
                runner.session_service,
                st.session_state.adk_session_id,
                {"reconciled_data": tool_reconciled_data},
            )

session = runner.session_service.get_session_sync(
    app_name="reconai", user_id="streamlit-user", session_id=st.session_state.adk_session_id
)
reconciled = (session.state or {}).get("reconciled_data") if session else None

if reconciled:
    try:
        data = reconciled if isinstance(reconciled, dict) else json.loads(reconciled)
        transactions = data.get("transactions", [])
        missing = data.get("missing_invoices", [])

        report = generate_monthly_report(data)
        total = report["total_spent"]
        category_totals = report["category_breakdown"]
        category_vendors = report["category_vendors"]
        total_income = report.get("total_income", 0)
        income_breakdown = report.get("income_breakdown", {})
        business_total = report.get("business_total", 0)
        personal_total = report.get("personal_total", 0)
        untagged_total = report.get("untagged_total", 0)
        business_income_total = report.get("business_income_total", 0)
        personal_income_total = report.get("personal_income_total", 0)
        subscriptions = report["subscriptions"]
        recurring_investments = report["recurring_investments"]
        payments_pending = report["payments_pending"]
        duplicates = report["duplicate_count"]
        recurring = report["recurring_count"]
        gst_total = report["gst_eligible_total"]

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        dup_badge_bg, dup_badge_fg = (f"{CORAL}22", CORAL) if duplicates else (f"{TEAL}22", TEAL)
        miss_badge_bg, miss_badge_fg = (f"{CORAL}22", CORAL) if missing else (f"{TEAL}22", TEAL)
        st.markdown(
            f"""
            <div class="metric-row">
                <div class="metric-card">
                    <div class="metric-label">Total spent</div>
                    <div class="metric-value">{total:,.0f}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Transactions</div>
                    <div class="metric-value">{len(transactions)}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Duplicates flagged</div>
                    <div class="metric-value">{duplicates}</div>
                    <span class="metric-badge" style="background:{dup_badge_bg}; color:{dup_badge_fg};">
                        {"Review these" if duplicates else "None found"}
                    </span>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Missing invoices</div>
                    <div class="metric-value">{len(missing)}</div>
                    <span class="metric-badge" style="background:{miss_badge_bg}; color:{miss_badge_fg};">
                        {"Needs follow-up" if missing else "All matched"}
                    </span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with _export_slot.container(horizontal=True, horizontal_alignment="right"):
            _last_assistant_text = next(
                (m["content"] for m in reversed(st.session_state.get("messages", [])) if m.get("role") == "assistant"),
                None,
            )
            _pdf_bytes = export_report_to_pdf(
                report,
                transaction_count=len(transactions),
                narrative_markdown=_last_assistant_text,
                generated_for=st.session_state.get("gmail_email"),
            )
            st.download_button(
                "📄 Export PDF",
                data=_pdf_bytes,
                file_name=f"reconai-report-{datetime.now().strftime('%Y-%m-%d')}.pdf",
                mime="application/pdf",
                key="export_pdf",
            )

        _sheet_id = (st.session_state.get("sheet_id_input") or "").strip()
        _is_mock_data = st.session_state.get("reconciled_data_is_mock", False)
        _is_trivial_data = not transactions and not missing
        if _sheet_id and not _is_mock_data and not _is_trivial_data:
            _save_marker = (st.session_state.adk_session_id, _sheet_id, len(transactions), total)
            if st.session_state.get("_last_sheet_save_marker") != _save_marker:
                with st.spinner("Auto-saving this report to your Sheet..."):
                    try:
                        _save_result = save_report_to_sheet(report, _sheet_id)
                    except GoogleSignInError as e:
                        _save_result = {"status": "failed", "rows_written": 0, "error": str(e)}
                    except Exception as e:  # noqa: BLE001
                        _save_result = {"status": "failed", "rows_written": 0, "error": str(e)}
                st.session_state._last_sheet_save_marker = _save_marker
                _sheet_url = f"https://docs.google.com/spreadsheets/d/{_sheet_id}/edit"
                if _save_result["status"] == "ok":
                    st.toast("Auto-saved to your Sheet's first tab.", icon="✅")
                    st.caption(f"💾 Auto-saved — [open the Sheet]({_sheet_url}).")
                else:
                    st.toast("Couldn't auto-save to that Sheet.", icon="⚠️")
                    st.caption(
                        "Couldn't auto-save to that Sheet — double-check the Sheet ID and "
                        "that your signed-in Google account has edit access to it."
                    )
        else:
            st.caption(
                "💡 Add a Google Sheet ID in the sidebar to auto-export this report there "
                "— totally optional, everything above works the same without it."
            )

        left, right = st.columns([3, 2])

        with left:
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title">Category breakdown</div>', unsafe_allow_html=True)
            if category_totals:
                labels = list(category_totals.keys())
                values = [category_totals[k] for k in labels]
                colors = [CATEGORY_COLORS[i % len(CATEGORY_COLORS)] for i in range(len(labels))]

                fig = go.Figure(
                    data=[
                        go.Pie(
                            labels=labels,
                            values=values,
                            hole=0.62,
                            marker=dict(colors=colors, line=dict(color=SURFACE, width=3)),
                            textinfo="none",
                            hovertemplate="%{label}: %{value:,.0f}<extra></extra>",
                        )
                    ]
                )
                fig.update_layout(
                    showlegend=True,
                    legend=dict(
                        orientation="v",
                        yanchor="middle",
                        y=0.5,
                        xanchor="left",
                        x=1.02,
                        font=dict(color=TEXT_PRIMARY, size=12),
                    ),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=10),
                    height=260,
                    annotations=[
                        dict(
                            text=f"{total:,.0f}<br><span style='font-size:11px;color:{TEXT_MUTED}'>total spent</span>",
                            x=0.5, y=0.5, showarrow=False,
                            font=dict(size=18, color=TEXT_PRIMARY),
                        )
                    ],
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            else:
                st.markdown('<div class="empty-hint">No categorized transactions yet.</div>', unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

        with right:
            st.markdown(
                f"""
                <div class="ledger-balance-card">
                    <div class="ledger-balance-label">Ledger summary</div>
                    <div class="ledger-balance-value">{total:,.2f}</div>
                    <div class="ledger-stat-row"><span>Recurring charges</span><span>{recurring}</span></div>
                    <div class="ledger-stat-row"><span>GST-eligible total</span><span>{gst_total:,.2f}</span></div>
                    <div class="ledger-stat-row"><span>Ledger sync</span><span>coming soon</span></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title">Recent transactions</div>', unsafe_allow_html=True)
        if transactions:
            for t in transactions[:10]:
                vendor = t.get("vendor") or "Unknown"
                amount = float(t.get("amount") or 0)
                category = t.get("category") or "Uncategorized"
                is_dup = t.get("is_duplicate")
                is_rec = t.get("is_recurring")
                is_pending = t.get("payment_status_guess") == "pending"
                is_inflow = category in income_breakdown
                spend_type = t.get("spend_type_guess")
                if is_inflow:
                    amount_color = TEAL
                elif is_dup or is_pending:
                    amount_color = CORAL
                else:
                    amount_color = TEXT_PRIMARY
                tags = ""
                if is_dup:
                    tags += f'<span class="tag" style="background:{CORAL}22; color:{CORAL};">Duplicate</span>'
                if is_pending:
                    tags += f'<span class="tag" style="background:{AMBER}22; color:{AMBER};">Pending</span>'
                if is_inflow:
                    tags += f'<span class="tag" style="background:{TEAL}22; color:{TEAL};">Money in</span>'
                if spend_type == "business":
                    tags += '<span class="tag" style="background:#F472B622; color:#F472B6;">Business</span>'
                if is_rec:
                    tags += f'<span class="tag" style="background:{PURPLE}22; color:{PURPLE};">Recurring</span>'
                sign = "+" if is_inflow else "-"
                st.markdown(
                    f"""
                    <div class="txn-row">
                        <div class="txn-left">
                            {_vendor_avatar(vendor)}
                            <div>
                                <p class="txn-vendor">{vendor}{tags}</p>
                                <p class="txn-meta">{category} &middot; {t.get("date", "")}</p>
                            </div>
                        </div>
                        <div class="txn-amount" style="color:{amount_color};">{sign}{amount:,.2f}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="empty-hint">No transactions reconciled yet.</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        if missing:
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown(
                f'<div class="panel-title">Missing invoices <span class="tag" style="background:{CORAL}22; color:{CORAL};">{len(missing)}</span></div>',
                unsafe_allow_html=True,
            )
            for m in missing:
                st.markdown(
                    f"""
                    <div class="missing-row">
                        <span>{m.get("description", "Unknown transaction")}</span>
                        <span style="color:{CORAL};">{float(m.get("amount") or 0):,.2f}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

        if payments_pending:
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown(
                f'<div class="panel-title">Payments pending '
                f'<span class="tag" style="background:{AMBER}22; color:{AMBER};">{len(payments_pending)}</span></div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="font-size:12px; color:{TEXT_MUTED}; margin:-8px 0 10px 0;">'
                f'Bill generated/issued but not confirmed paid — not counted in total spent above.</p>',
                unsafe_allow_html=True,
            )
            for p in payments_pending:
                st.markdown(
                    f"""
                    <div class="missing-row">
                        <span>{p['vendor']} &middot; {p.get('category', '')}</span>
                        <span style="color:{AMBER};">{p['amount']:,.2f}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

        if income_breakdown:
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown(
                f'<div class="panel-title">Money in this period '
                f'<span class="tag" style="background:{TEAL}22; color:{TEAL};">{total_income:,.2f}</span></div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="font-size:12px; color:{TEXT_MUTED}; margin:-8px 0 10px 0;">'
                f'Salary, interest, dividends, and investment gains/redemption/maturity payouts — '
                f'not spend, not included in total spent above.</p>',
                unsafe_allow_html=True,
            )
            for cat, amount in sorted(income_breakdown.items(), key=lambda kv: -kv[1]):
                st.markdown(
                    f"""
                    <div class="missing-row">
                        <span>{cat}</span>
                        <span style="color:{TEAL};">+{amount:,.2f}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

        if business_total or personal_total or untagged_total:
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            st.markdown('<div class="panel-title">Business vs personal</div>', unsafe_allow_html=True)
            st.markdown(
                f'<p style="font-size:12px; color:{TEXT_MUTED}; margin:-8px 0 10px 0;">'
                f'Same total_spent above, split by what each transaction looks like it was for.</p>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f"""
                <div class="missing-row"><span style="color:{PURPLE}; font-weight:600;">Business spend</span><span style="color:{PURPLE};">{business_total:,.2f}</span></div>
                <div class="missing-row"><span style="color:{TEAL}; font-weight:600;">Personal spend</span><span style="color:{TEAL};">{personal_total:,.2f}</span></div>
                """,
                unsafe_allow_html=True,
            )
            if untagged_total:
                st.markdown(
                    f'<div class="missing-row"><span style="color:{TEXT_MUTED};">Untagged (couldn\'t tell which)</span>'
                    f'<span style="color:{TEXT_MUTED};">{untagged_total:,.2f}</span></div>',
                    unsafe_allow_html=True,
                )
            if business_income_total or personal_income_total:
                st.markdown(
                    f"""
                    <div class="missing-row"><span style="color:{PURPLE}; font-weight:600;">Business money in</span><span style="color:{PURPLE};">+{business_income_total:,.2f}</span></div>
                    <div class="missing-row"><span style="color:{TEAL}; font-weight:600;">Personal money in</span><span style="color:{TEAL};">+{personal_income_total:,.2f}</span></div>
                    """,
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

        if subscriptions or recurring_investments:
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
            sub_col, inv_col = st.columns(2)

            with sub_col:
                st.markdown('<div class="panel">', unsafe_allow_html=True)
                st.markdown('<div class="panel-title">Subscriptions you have</div>', unsafe_allow_html=True)
                if subscriptions:
                    for s in subscriptions:
                        st.markdown(
                            f"""
                            <div class="txn-row">
                                <div class="txn-left">
                                    {_vendor_avatar(s['vendor'])}
                                    <div>
                                        <p class="txn-vendor">{s['vendor']}</p>
                                        <p class="txn-meta">{s.get('category', '')}</p>
                                    </div>
                                </div>
                                <div class="txn-amount">-{s['amount']:,.2f}</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                else:
                    st.markdown('<div class="empty-hint">None detected this period.</div>', unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

            with inv_col:
                st.markdown('<div class="panel">', unsafe_allow_html=True)
                st.markdown('<div class="panel-title">Recurring investments</div>', unsafe_allow_html=True)
                if recurring_investments:
                    for s in recurring_investments:
                        st.markdown(
                            f"""
                            <div class="txn-row">
                                <div class="txn-left">
                                    {_vendor_avatar(s['vendor'])}
                                    <div>
                                        <p class="txn-vendor">{s['vendor']}</p>
                                        <p class="txn-meta">{s.get('category', '')}</p>
                                    </div>
                                </div>
                                <div class="txn-amount">-{s['amount']:,.2f}</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                else:
                    st.markdown('<div class="empty-hint">None detected this period.</div>', unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

    except (TypeError, json.JSONDecodeError):
        st.caption("Reconciliation data present but not yet in a displayable shape.")
