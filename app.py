"""
app.py — ReconAI Streamlit UI, wired to the real ADK Runner (agents.py).

Same functional core as before — ADK Runner, session state, sidebar
config, chat loop calling the 3-agent pipeline — with a redesigned
dashboard: dark themed, metric cards, a category-breakdown donut chart,
a ledger-summary card, budget bars, and a styled recent-transactions list.
Purely presentational; no changes to how the pipeline runs or what
guardrails are wired in agents.py.

Runner/session are cached across reruns via st.cache_resource /
st.session_state so we don't rebuild the ADK Runner on every keystroke
(section 8's explicit performance note).
"""

import hashlib
import json
import os
import tempfile

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from google.genai import types

from agents import root_agent
from tools.google_auth import (
    GoogleSignInError,
    clear_cached_credentials,
    get_credentials,
    get_signed_in_email,
    load_cached_credentials,
)
from tools.reporting_tools import generate_monthly_report

load_dotenv()

st.set_page_config(page_title="ReconAI", page_icon="\U0001F4CA", layout="wide")

# --- Color palettes: neon cyan/blue HUD (dark) and a clean light mode ---
THEME_DARK = {
    "BG": "#040A14",
    "SURFACE": "#081726",
    "SURFACE_ALT": "#0C1F33",
    "BORDER": "rgba(34, 211, 238, 0.22)",
    "BORDER_GLOW": "rgba(34, 211, 238, 0.55)",
    "TEXT_PRIMARY": "#E8FBFF",
    "TEXT_MUTED": "#5E86A0",
    "TEAL": "#22D3EE",      # primary cyan accent — "good" status, positive glow
    "PURPLE": "#3B82F6",    # electric blue — recurring tag
    "CORAL": "#FF3B5C",     # neon red — duplicates / danger / missing invoices
    "AMBER": "#38BDF8",     # sky blue — category variety
    "BLUE": "#0EA5E9",
    "GLOW_CYAN": "0 0 12px rgba(34, 211, 238, 0.55)",
    "GLOW_CYAN_SOFT": "0 0 18px rgba(34, 211, 238, 0.18)",
    "GLOW_RED": "0 0 12px rgba(255, 59, 92, 0.55)",
    "CATEGORY_COLORS": ["#22D3EE", "#3B82F6", "#38BDF8", "#818CF8", "#2DD4BF", "#93C5FD", "#0EA5E9"],
    "ACCENT_RGB": "34, 211, 238",
    "ACCENT_RGB_2": "59, 130, 246",
    "GRID_TEXTURE": True,
}

THEME_LIGHT = {
    "BG": "#F3F6FB",
    "SURFACE": "#FFFFFF",
    "SURFACE_ALT": "#EEF2F8",
    "BORDER": "rgba(15, 23, 42, 0.10)",
    "BORDER_GLOW": "rgba(37, 99, 235, 0.45)",
    "TEXT_PRIMARY": "#0F1B2D",
    "TEXT_MUTED": "#64748B",
    "TEAL": "#0891B2",
    "PURPLE": "#4F46E5",
    "CORAL": "#DC2626",
    "AMBER": "#0284C7",
    "BLUE": "#2563EB",
    "GLOW_CYAN": "0 2px 10px rgba(37, 99, 235, 0.16)",
    "GLOW_CYAN_SOFT": "0 1px 8px rgba(15, 23, 42, 0.07)",
    "GLOW_RED": "0 2px 10px rgba(220, 38, 38, 0.18)",
    "CATEGORY_COLORS": ["#0891B2", "#4F46E5", "#2563EB", "#7C3AED", "#0D9488", "#DB2777", "#0284C7"],
    "ACCENT_RGB": "37, 99, 235",
    "ACCENT_RGB_2": "37, 99, 235",
    "GRID_TEXTURE": False,
}

if "theme" not in st.session_state:
    st.session_state.theme = "dark"

_active_theme = THEME_LIGHT if st.session_state.theme == "light" else THEME_DARK

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
        /* Streamlit's st.columns() is a flexbox row with the default
        align-items: stretch, so a shorter column gets stretched to match
        its taller sibling. .panel used to also claim height:100% on top of
        that stretch, which could make a panel report a much larger box
        than its own content needed — visually bleeding into whatever
        comes next in the page (reported: Budget status spilling into
        Recent transactions). Dropping height:100% lets each panel size to
        its own content; overflow:hidden is a safety clip against any
        remaining stretch. */
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

        .budget-row {{
            margin-bottom: 12px;
        }}
        .budget-row-top {{
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            margin-bottom: 6px;
        }}
        .budget-track {{
            width: 100%;
            height: 6px;
            border-radius: 999px;
            background: {SURFACE_ALT};
            overflow: hidden;
        }}
        .budget-fill {{
            height: 100%;
            border-radius: 999px;
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
    initial = (vendor or "?").strip()[:1].upper() or "?"
    idx = int(hashlib.md5(vendor.encode("utf-8")).hexdigest(), 16) % len(CATEGORY_COLORS)
    color = CATEGORY_COLORS[idx]
    return f'<div class="txn-avatar" style="background:{color}; box-shadow: 0 0 10px {color}99;">{initial}</div>'


# --- Mock data for UI testing, no Gmail/Gemini/Sheets calls involved ---
# Same shape check_duplicates_and_budget actually returns (in fact, this
# exact dict was PRODUCED by calling check_duplicates_and_budget on a
# realistic discovery-agent-shaped transaction batch, then saved — not
# hand-typed — so the dedup/recurring/budget flags are guaranteed
# internally consistent). Deliberately covers every feature the app
# demoes: a real duplicate pair (Frame Kro — two emails for one order),
# six recurring subscriptions (Anthropic, Google Cloud, Netflix, Hotstar,
# Prime, Spotify), two recurring investments (a SIP, a NACH mutual fund
# debit), recurring rent (kept out of the subscriptions list — see
# _is_housing_category in tools/reporting_tools.py), one pending bill not
# yet paid (BESCOM electricity), recurring interest income that should NOT
# be mistaken for a subscription, groceries/Amazon spend, food orders, UPI
# transfers, a dividend credit, and one bank-CSV debit with no matching
# invoice (missing_invoices). See tools/reporting_tools.py and
# tests/test_reporting.py for the generation script / assertions.
_MOCK_RECONCILED_DATA = {
    "status": "ok",
    "transactions": [
        {"source_id": "19f2208354f379c9:hdfc_interest_jul01", "vendor": "HDFC Bank", "amount": 2124.0,
         "date": "2026-07-01", "category": "Interest Income", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "18d9a41c220b5f3a:groceries_jul", "vendor": "BigBasket", "amount": 3200.0,
         "date": "2026-07-08", "category": "Groceries", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "18d9a41c220b5f3b:amazon_jul", "vendor": "Amazon", "amount": 2450.0,
         "date": "2026-07-12", "category": "Shopping", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1a2b3c4d5e6f7001:anthropic_jul", "vendor": "Anthropic PBC", "amount": 1899.0,
         "date": "2026-07-05", "category": "SaaS", "is_recurring_guess": True,
         "gst_eligible_guess": True, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1a2b3c4d5e6f7002:gcp_jul", "vendor": "Google Cloud Platform", "amount": 1100.0,
         "date": "2026-07-18", "category": "SaaS", "is_recurring_guess": True,
         "gst_eligible_guess": True, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1b3c5d7e9f102030:sip_jul", "vendor": "SIP Investment", "amount": 39000.0,
         "date": "2026-07-16", "category": "Investment", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1b3c5d7e9f102031:nach_sip_jul", "vendor": "NACH Mutual Fund SIP", "amount": 8500.0,
         "date": "2026-07-05", "category": "Investment", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1c4d5e6f70819202:framekro_confirm", "vendor": "Frame Kro", "amount": 499.0,
         "date": "2026-07-16", "category": "Shopping", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1c4d5e6f70819203:framekro_receipt", "vendor": "Frame Kro", "amount": 499.0,
         "date": "2026-07-17", "category": "Shopping", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": True, "duplicate_of": 7, "is_recurring": False},
        {"source_id": "1d5e6f7081920a1b:bescom_bill_jul", "vendor": "BESCOM", "amount": 1240.0,
         "date": "2026-07-16", "category": "Utilities", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "pending", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1e6f708192a3b4c5:zomato_jul18", "vendor": "Sri Krishna Sagar", "amount": 239.0,
         "date": "2026-07-19", "category": "Food", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1e6f708192a3b4c6:swiggy_jul18", "vendor": "Indira Priyadarshini", "amount": 760.0,
         "date": "2026-07-18", "category": "Food", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1f708192a3b4c5d6:upi_1", "vendor": "q635075112@ybl", "amount": 80.0,
         "date": "2026-07-18", "category": "Transfer", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1f708192a3b4c5d7:upi_2", "vendor": "q958687424@ybl", "amount": 80.0,
         "date": "2026-07-16", "category": "Transfer", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "208192a3b4c5d6e7:tata_dividend", "vendor": "Tata Power Company Ltd", "amount": 705.0,
         "date": "2026-07-14", "category": "Income (Dividends)", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "21a2b3c4d5e6f701:netflix_jul", "vendor": "Netflix", "amount": 649.0,
         "date": "2026-07-03", "category": "Subscriptions", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "21a2b3c4d5e6f702:hotstar_jul", "vendor": "Disney+ Hotstar", "amount": 299.0,
         "date": "2026-07-04", "category": "Subscriptions", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "21a2b3c4d5e6f703:prime_jul", "vendor": "Amazon Prime", "amount": 299.0,
         "date": "2026-07-06", "category": "Subscriptions", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "21a2b3c4d5e6f704:spotify_jul", "vendor": "Spotify", "amount": 119.0,
         "date": "2026-07-07", "category": "Subscriptions", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "22a2b3c4d5e6f701:rent_jul", "vendor": "Landlord - Dom's Residence", "amount": 20000.0,
         "date": "2026-07-02", "category": "Rent", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
    ],
    "missing_invoices": [
        {"date": "2026-07-20", "description": "PVR CINEMAS - MOVIE TICKETS", "amount": 850.0, "type": "debit"},
    ],
    "budget_summary": {
        "Groceries": {"spent": 3200.0, "budget": 3000, "over_budget": True},
        "Subscriptions": {"spent": 1366.0, "budget": 2000, "over_budget": False},
        "Shopping": {"spent": 3448.0, "budget": 2000, "over_budget": True},
        "SaaS": {"spent": 2999.0, "budget": 2500, "over_budget": True},
        "Rent": {"spent": 20000.0, "budget": 20000, "over_budget": False},
    },
}


def _write_session_state(session_service, session_id: str, state_delta: dict) -> None:
    """Writes a state_delta into ADK session state via a proper
    append_event/state_delta — a bare session.state[...] = ... assignment on
    the object returned by get_session_sync does NOT persist in
    InMemorySessionService (verified: it returns a snapshot, not the live
    object)."""
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
    """Writes _MOCK_RECONCILED_DATA into session state. No Gmail, Gemini, or
    Sheets calls happen here at all."""
    _write_session_state(session_service, session_id, {"reconciled_data": _MOCK_RECONCILED_DATA})


_inject_theme()

if not os.getenv("GOOGLE_API_KEY"):
    st.error("GOOGLE_API_KEY not found. Check your .env file.")
    st.stop()

_header_col, _theme_col = st.columns([6, 1])
with _header_col:
    st.markdown(
        f"""
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
with _theme_col:
    _theme_label = "☀️ Light" if st.session_state.theme == "dark" else "\U0001F319 Dark"
    if st.button(_theme_label, use_container_width=True, help="Switch theme"):
        st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"
        st.rerun()


# --- Gmail sign-in gate ---------------------------------------------------
# Blocks the rest of the app (sidebar config, chat, dashboard) until the
# user is authenticated with Google. This makes the OAuth prompt happen
# right when the demo opens, not silently mid-conversation the first time
# a Gmail tool happens to run — which is what used to happen (get_
# credentials() was only ever called lazily, deep inside fetch_invoice_
# emails). On every rerun we first check silently (load_cached_
# credentials — never opens a browser) so an already-signed-in session
# just sails through; the interactive flow only starts on an explicit
# button click.
if "gmail_email" not in st.session_state:
    st.session_state.gmail_email = None
if "gmail_authed" not in st.session_state:
    st.session_state.gmail_authed = False

if not st.session_state.gmail_authed:
    _cached_creds = load_cached_credentials()
    if _cached_creds:
        st.session_state.gmail_authed = True
        st.session_state.gmail_email = get_signed_in_email(_cached_creds)

if not st.session_state.gmail_authed:
    st.markdown(
        f"""
        <div style="max-width:440px; margin: 60px auto 24px; text-align:center;">
            <div class="recon-logo" style="margin:0 auto 18px; width:56px; height:56px; font-size:24px;">R</div>
            <h2 style="margin:0 0 8px; font-family:'Orbitron', sans-serif; letter-spacing:0.04em;">Sign in to ReconAI</h2>
            <p style="color:{TEXT_MUTED}; font-size:14px; line-height:1.5;">
                Connect your Google account so ReconAI can read invoices and
                receipts from Gmail (read-only — it can never send, delete,
                or modify anything). You'll pick your account in a browser
                window that opens next.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _gate_l, _gate_c, _gate_r = st.columns([1, 1, 1])
    with _gate_c:
        if st.button("🔐  Sign in with Google", use_container_width=True):
            with st.spinner("Waiting for you to finish signing in in the browser window..."):
                try:
                    creds = get_credentials()
                    st.session_state.gmail_authed = True
                    st.session_state.gmail_email = get_signed_in_email(creds)
                    st.rerun()
                except GoogleSignInError as e:
                    st.error(f"⚠️ {e}")
                except Exception:
                    # Anything not already translated into a friendly
                    # GoogleSignInError — never show raw library/stack
                    # trace text to the person signing in.
                    st.error(
                        "⚠️ Something went wrong while signing in. "
                        "Please try again in a moment."
                    )
    st.stop()
# --- end sign-in gate ------------------------------------------------------


@st.cache_resource
def get_runner():
    from google.adk.runners import InMemoryRunner
    return InMemoryRunner(agent=root_agent, app_name="reconai")


runner = get_runner()

# --- Sidebar: run configuration ---
with st.sidebar:
    st.caption(f"✅ Signed in as **{st.session_state.gmail_email or 'your Google account'}**")
    if st.button("Sign out / switch account", use_container_width=True):
        clear_cached_credentials()
        st.session_state.gmail_authed = False
        st.session_state.gmail_email = None
        st.rerun()
    st.divider()

    st.header("Reconciliation setup")
    st.caption(
        "🔒 **Google Sheets sync** — coming soon. This run reports straight "
        "from Gmail with no ledger write, to keep API/credit usage down."
    )
    budget_json = st.text_area(
        "Budget (optional, JSON)",
        value="",
        placeholder='{"Food": 8000, "Subscriptions": 2000}',
        height=80,
    )
    uploaded_csv = st.file_uploader("Bank statement CSV (optional)", type=["csv"])

    bank_csv_path = None
    if uploaded_csv is not None:
        tmp_dir = tempfile.gettempdir()
        bank_csv_path = os.path.join(tmp_dir, f"reconai_{uploaded_csv.name}")
        with open(bank_csv_path, "wb") as f:
            f.write(uploaded_csv.getbuffer())
        st.success(f"Saved: {uploaded_csv.name}")

    st.divider()
    st.caption(
        "Guardrails active: readonly Gmail scope, input/output filters, "
        "rate limiting, destructive-action blocklist, CSV sanitizing."
    )

# --- Session state: ADK session id + chat history, persisted across reruns ---
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


def _build_prompt(user_text: str) -> str:
    """Folds sidebar config into the prompt so Discovery/Reconciliation
    tools have what they need (budget, CSV path) without a separate
    plumbing layer — the agents' own instructions tell them to look for
    this context."""
    parts = [user_text]
    if bank_csv_path:
        parts.append(f"[context] Bank statement CSV file path: {bank_csv_path}")
    if budget_json.strip():
        try:
            budget = json.loads(budget_json)
            parts.append(f"[context] Monthly budget by category (JSON): {json.dumps(budget)}")
        except json.JSONDecodeError:
            st.warning("Budget JSON is invalid — ignoring it for this run.")
    return "\n".join(parts)


prompt = st.chat_input("e.g. 'Prepare July reconciliation' or ask a question about your ledger")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.chat_message("user").markdown(prompt)

    full_prompt = _build_prompt(prompt)
    content = types.Content(role="user", parts=[types.Part(text=full_prompt)])

    final_text_chunks = []
    # Captured directly from the check_duplicates_and_budget tool's own
    # return value (via ADK event.get_function_responses()), not from the
    # reconciliation_agent's own final text. output_key="reconciled_data"
    # stores whatever the model *says* at the end of its turn — usually
    # prose, sometimes a JSON dump, sometimes both — which is why the
    # dashboard was intermittently failing to parse it (falling back to
    # showing raw JSON instead of the styled dashboard). The tool's return
    # value is deterministic Python output with the exact shape the
    # dashboard below expects, so it's the reliable source of truth.
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
        _write_session_state(
            runner.session_service,
            st.session_state.adk_session_id,
            {"reconciled_data": tool_reconciled_data},
        )

# --- Dashboard: pulled from ADK session state after the last run ---
session = runner.session_service.get_session_sync(
    app_name="reconai", user_id="streamlit-user", session_id=st.session_state.adk_session_id
)
reconciled = (session.state or {}).get("reconciled_data") if session else None

if reconciled:
    try:
        data = reconciled if isinstance(reconciled, dict) else json.loads(reconciled)
        transactions = data.get("transactions", [])
        missing = data.get("missing_invoices", [])
        budget_summary = data.get("budget_summary", {})

        # Call the exact same deterministic function the Reporting Agent's
        # tool call uses, directly in Python, instead of recomputing totals
        # with separate ad-hoc logic here. This guarantees the dashboard can
        # never disagree with the chat report — both numbers come from the
        # one function call, not two independent implementations that can
        # drift apart (which is what caused the Frame Kro total mismatch:
        # the dashboard and the report used to compute things separately).
        report = generate_monthly_report(data)
        total = report["total_spent"]
        category_totals = report["category_breakdown"]
        category_vendors = report["category_vendors"]
        subscriptions = report["subscriptions"]
        recurring_investments = report["recurring_investments"]
        payments_pending = report["payments_pending"]
        duplicates = report["duplicate_count"]
        recurring = report["recurring_count"]
        gst_total = report["gst_eligible_total"]

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # --- metric row ---
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

            if budget_summary:
                st.markdown('<div class="panel">', unsafe_allow_html=True)
                st.markdown('<div class="panel-title">Budget status</div>', unsafe_allow_html=True)
                for cat, v in budget_summary.items():
                    spent = float(v.get("spent", 0))
                    cap = float(v.get("budget", 0)) or 1
                    over = v.get("over_budget", False)
                    pct = max(0, min(100, round(spent / cap * 100)))
                    bar_color = CORAL if over else TEAL
                    st.markdown(
                        f"""
                        <div class="budget-row">
                            <div class="budget-row-top">
                                <span>{cat}</span>
                                <span style="color:{bar_color if over else TEXT_MUTED};">{spent:,.0f} / {cap:,.0f}</span>
                            </div>
                            <div class="budget-track">
                                <div class="budget-fill" style="width:{pct}%; background:{bar_color};"></div>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                st.markdown("</div>", unsafe_allow_html=True)

        # --- recent transactions ---
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
                amount_color = CORAL if (is_dup or is_pending) else TEXT_PRIMARY
                tags = ""
                if is_dup:
                    tags += f'<span class="tag" style="background:{CORAL}22; color:{CORAL};">Duplicate</span>'
                if is_pending:
                    tags += f'<span class="tag" style="background:{AMBER}22; color:{AMBER};">Pending</span>'
                if is_rec:
                    tags += f'<span class="tag" style="background:{PURPLE}22; color:{PURPLE};">Recurring</span>'
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
                        <div class="txn-amount" style="color:{amount_color};">-{amount:,.2f}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="empty-hint">No transactions reconciled yet.</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # --- missing invoices callout ---
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

        # --- payments pending: bills generated but not yet paid ---
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

        # --- subscriptions + recurring investments, side by side ---
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
