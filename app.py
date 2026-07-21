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
from datetime import datetime

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
)
from tools.export_tools import export_report_to_pdf
from tools.reporting_tools import generate_monthly_report, save_report_to_sheet

load_dotenv()

st.set_page_config(page_title="ReconAI", page_icon="\U0001F4CA", layout="wide")

# --- Color palette: neon cyan/blue HUD. Dark only — there is deliberately
# no light mode and no theme toggle. The whole visual identity (glows,
# grid texture, neon accents) is built for a dark surface; the light
# variant never looked like the same product and every new widget had to
# be re-checked against two palettes. One palette, one code path. ---
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
        /* st.chat_input is a separate Streamlit widget from st.text_input -
        it has its own testids (stChatInput / stChatInputTextArea /
        stChatInputSubmitButton) that the .stTextInput override above never
        touches, so without this it keeps Streamlit's own built-in default
        styling instead of this app's palette. */
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

        /* --- Top-right toolbar corner -----------------------------------
        Streamlit's own toolbar sits top-right and holds [Deploy] [⋮].
        We hide the standalone Deploy button (deploying is still available
        — it's already an item inside the ⋮ menu, "Deploy this app", so
        nothing is actually lost) and float our own account/session
        controls into that space, leaving the ⋮ menu visible to their
        right. #recon-topbar is a plain fixed-position strip; the right
        offset is what reserves room for the ⋮ so the two never overlap. */
        [data-testid="stAppDeployButton"] {{
            display: none !important;
        }}
        [data-testid="stToolbar"], [data-testid="stAppToolbar"] {{
            z-index: 999992;
        }}
        /* Vertically aligned to the ⋮ menu by matching Streamlit's own
        header box exactly rather than guessing a top offset: its header
        is `height: 3.75rem` (theme.sizes.headerHeight) with
        `align-items: center`, so anchoring at top:0 with the same height
        and centering puts our controls on precisely the same centerline
        the ⋮ sits on. A hand-tuned `top` value drifted because our
        buttons (34px) aren't the same height as the ⋮ button. */
        .st-key-recon-topbar {{
            position: fixed;
            top: 0;
            height: 3.75rem;
            right: 3.4rem;   /* leaves the ⋮ menu uncovered, immediately right of us */
            z-index: 999991;
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
            width: auto !important;
            gap: 8px !important;
        }}
        /* Both children of the strip ("Clear screen" and the account
        popover) are Streamlit block-level elements that default to
        width:100% when stacked vertically - inside our horizontal strip
        that stretch is what pushed the popover onto its own line below
        "Clear screen" instead of sitting beside it. Popovers in
        particular aren't a plain widget (stElementContainer) but a whole
        layout block (stPopover), so it needs its own explicit override,
        not just the generic element-container one. */
        .st-key-recon-topbar [data-testid="stElementContainer"],
        .st-key-recon-topbar [data-testid="stPopover"] {{
            width: auto !important;
            flex: 0 0 auto !important;
        }}
        /* Google's own button spec (dark variant): #131314 surface,
        #8E918F hairline border, #E3E3E3 Roboto Medium 14px label, 20px
        "G" mark on the left, 4px radius. Deliberately NOT restyled with
        this app's neon palette — a Google sign-in control is supposed to
        look like Google's, not like the surrounding product. */
        .st-key-gate_signin button {{
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
        }}
        .st-key-gate_signin button:hover {{
            background-color: #1E1F20 !important;
            border-color: #E3E3E3 !important;
            box-shadow: none !important;
        }}
        /* Signed-in account control: a plain generic account-circle icon,
        no visible email/initial next to it — the address only shows once
        the popover is actually opened (hover also surfaces it via the
        button's own tooltip). A round icon button, not a text button.
        Scoped to our topbar strip (rather than a dedicated key) since
        st.popover doesn't accept a key= argument in this Streamlit
        version — [data-testid="stPopover"] is the only hook available,
        so we scope it under .st-key-recon-topbar to avoid touching any
        other popover that might exist elsewhere in the app. */
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
        /* The account_circle glyph itself (a Material Symbols font span,
        not an svg) — sized and centered explicitly, since with the label
        empty there's no text to size it against. */
        .st-key-recon-topbar [data-testid="stPopover"] button [data-testid="stIconMaterial"] {{
            font-size: 22px !important;
            width: 22px !important;
            height: 22px !important;
            line-height: 1 !important;
            margin: 0 !important;
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
        /* Streamlit's popover trigger always appends its own trailing
        "expand" chevron (StyledPopoverButtonIcon -> ExpandMore) after the
        label/icon, with no parameter to turn it off. On an icon-only
        trigger it reads as a stray mark floating beside the avatar.
        The chevron is the ONLY <svg> in the button: the account_circle
        icon is a Material-Symbols *font glyph* in a
        <span data-testid="stIconMaterial">, not an svg. So hiding every
        svg in the trigger removes the chevron and leaves the account
        icon untouched. */
        .st-key-recon-topbar [data-testid="stPopoverButton"] svg {{
            display: none !important;
        }}
        /* The popover's floating panel is rendered in a portal outside the
        normal layout, so it needs its own background/text-color rules —
        the .stApp-scoped rules above never reach it. */
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

        /* Hover-to-reveal "ⓘ" badge — used for the Sheet-ID setup steps
        in the sidebar, a lighter-weight alternative to a click-to-open
        expander for a short reference note. Pure CSS :hover, no JS.

        The tooltip's positioning context is the whole label ROW
        (.recon-info-row), not the tiny badge itself — with left:0/right:0
        it always spans exactly the row's own width, which is already
        constrained to the sidebar's content area. Anchoring it to the
        badge instead (a fixed pixel width growing sideways from wherever
        the badge happens to land after the label text wraps) is what
        made it spill past the sidebar's edge and get clipped by the
        sidebar's own overflow. */
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
        /* "Clear screen" takes the slot the Deploy button used to occupy,
        styled to sit quietly next to the Google control rather than
        competing with it. */
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


# Small colored initial-letter "doodle" standing in for the signed-in
# Google account's profile photo (which we never fetch — no extra scope,
# no extra API call, just a deterministic color from the email like a
# vendor avatar above). _ACCOUNT_AVATAR_COLORS deliberately isn't
# CATEGORY_COLORS: it needs to look right against both the popover
# trigger button (which is styled to Google's own dark spec, not this
# app's palette) and the popover panel body.
_ACCOUNT_AVATAR_COLORS = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#7C4DFF", "#00ACC1"]


def _account_avatar_html(email: str, size: int = 32) -> str:
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
         "date": "2026-07-01", "category": "Interest Income", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "18d9a41c220b5f3a:groceries_jul", "vendor": "BigBasket", "amount": 3200.0,
         "date": "2026-07-08", "category": "Groceries", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "18d9a41c220b5f3b:amazon_jul", "vendor": "Amazon", "amount": 2450.0,
         "date": "2026-07-12", "category": "Shopping", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1a2b3c4d5e6f7001:anthropic_jul", "vendor": "Anthropic PBC", "amount": 1899.0,
         "date": "2026-07-05", "category": "SaaS", "spend_type_guess": "business", "is_recurring_guess": True,
         "gst_eligible_guess": True, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1a2b3c4d5e6f7002:gcp_jul", "vendor": "Google Cloud Platform", "amount": 1100.0,
         "date": "2026-07-18", "category": "SaaS", "spend_type_guess": "business", "is_recurring_guess": True,
         "gst_eligible_guess": True, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1a2b3c4d5e6f7003:client_payment_jul", "vendor": "Razorpay - WebDesign Co (client)", "amount": 45000.0,
         "date": "2026-07-09", "category": "Client Payment", "spend_type_guess": "business", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1b3c5d7e9f102030:sip_jul", "vendor": "SIP Investment", "amount": 39000.0,
         "date": "2026-07-16", "category": "Investment", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1b3c5d7e9f102031:nach_sip_jul", "vendor": "NACH Mutual Fund SIP", "amount": 8500.0,
         "date": "2026-07-05", "category": "Investment", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1b3c5d7e9f102032:rd_jul", "vendor": "Post Office RD", "amount": 5000.0,
         "date": "2026-07-05", "category": "Recurring Deposit", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1b3c5d7e9f102033:gold_jul", "vendor": "SafeGold", "amount": 2500.0,
         "date": "2026-07-10", "category": "Gold", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1b3c5d7e9f102034:zerodha_jul", "vendor": "Zerodha", "amount": 12000.0,
         "date": "2026-07-11", "category": "Stock", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1b3c5d7e9f102035:mf_redemption_jul", "vendor": "NJ India Online", "amount": 18500.0,
         "date": "2026-07-13", "category": "Investment Gains", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1c4d5e6f70819202:framekro_confirm", "vendor": "Frame Kro", "amount": 499.0,
         "date": "2026-07-16", "category": "Shopping", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1c4d5e6f70819203:framekro_receipt", "vendor": "Frame Kro", "amount": 499.0,
         "date": "2026-07-17", "category": "Shopping", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": True, "duplicate_of": 7, "is_recurring": False},
        {"source_id": "1d5e6f7081920a1b:bescom_bill_jul", "vendor": "BESCOM", "amount": 1240.0,
         "date": "2026-07-16", "category": "Utilities", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "pending", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "1e6f708192a3b4c5:zomato_jul18", "vendor": "Sri Krishna Sagar", "amount": 239.0,
         "date": "2026-07-19", "category": "Food", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1e6f708192a3b4c6:swiggy_jul18", "vendor": "Indira Priyadarshini", "amount": 760.0,
         "date": "2026-07-18", "category": "Food", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1f708192a3b4c5d6:upi_1", "vendor": "q635075112@ybl", "amount": 80.0,
         "date": "2026-07-18", "category": "Transfer", "spend_type_guess": "unknown", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "1f708192a3b4c5d7:upi_2", "vendor": "q958687424@ybl", "amount": 80.0,
         "date": "2026-07-16", "category": "Transfer", "spend_type_guess": "unknown", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "208192a3b4c5d6e7:tata_dividend", "vendor": "Tata Power Company Ltd", "amount": 705.0,
         "date": "2026-07-14", "category": "Income (Dividends)", "spend_type_guess": "personal", "is_recurring_guess": False,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": False},
        {"source_id": "21a2b3c4d5e6f701:netflix_jul", "vendor": "Netflix", "amount": 649.0,
         "date": "2026-07-03", "category": "Subscriptions", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "21a2b3c4d5e6f702:hotstar_jul", "vendor": "Disney+ Hotstar", "amount": 299.0,
         "date": "2026-07-04", "category": "Subscriptions", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "21a2b3c4d5e6f703:prime_jul", "vendor": "Amazon Prime", "amount": 299.0,
         "date": "2026-07-06", "category": "Subscriptions", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "21a2b3c4d5e6f704:spotify_jul", "vendor": "Spotify", "amount": 119.0,
         "date": "2026-07-07", "category": "Subscriptions", "spend_type_guess": "personal", "is_recurring_guess": True,
         "gst_eligible_guess": False, "payment_status_guess": "paid", "status": "ok",
         "is_duplicate": False, "duplicate_of": None, "is_recurring": True},
        {"source_id": "22a2b3c4d5e6f701:rent_jul", "vendor": "Landlord - Dom's Residence", "amount": 20000.0,
         "date": "2026-07-02", "category": "Rent", "spend_type_guess": "personal", "is_recurring_guess": True,
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

_header_col, _export_col = st.columns([5, 3])
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
# Reserved slot for the "Save to Sheet" / "Export PDF" export controls,
# sitting in the header's right-hand column. Deliberately a placeholder
# rather than the buttons themselves: Streamlit runs the script top to
# bottom, and there is no report to export until the dashboard section
# far below has actually computed one. Filling this placeholder from
# there lets the buttons *render* up here beside the title while still
# only *existing* when there's something to export — the alternative
# (rendering them here unconditionally) would show two dead buttons on
# every fresh session before the first run.
_export_slot = _export_col.empty()


# --- Gmail sign-in gate ---------------------------------------------------
# Blocks the rest of the app (sidebar config, chat, dashboard) until the
# user is authenticated with Google. This makes the OAuth prompt happen
# right when the demo opens, not silently mid-conversation the first time
# a Gmail tool happens to run — which is what used to happen (get_
# credentials() was only ever called lazily, deep inside fetch_invoice_
# emails).
#
# Deliberately NOT auto-bypassing this screen even when a valid token.json
# is already cached on disk from a previous run: a brand-new Streamlit
# session (a new browser tab/window, or the process restarting) always
# lands on this screen and needs an explicit "Sign in with Google" click —
# that's the point of a login gate for a demo. Clicking it is still fast
# when a cached token exists (get_credentials() tries load_cached_
# credentials() first internally, so no browser popup is needed), it's
# just never silent/automatic before the user has clicked anything.
if "gmail_email" not in st.session_state:
    st.session_state.gmail_email = None
if "gmail_authed" not in st.session_state:
    st.session_state.gmail_authed = False


def _reset_session_for_new_account() -> None:
    """Wipes every trace of the previous signed-in account's data before
    showing the sign-in screen for a new one.

    Bug this fixes: "Sign out / switch account" used to only clear the
    OAuth token (gmail_authed/gmail_email) — it left st.session_state.
    messages (the whole chat history) and adk_session_id (which points at
    an ADK session still holding the previous run's reconciled_data) fully
    intact. So a second person signing in on the same browser/machine
    would see the first person's chat transcript and dashboard numbers
    the instant they finished signing in, before ever running anything
    themselves. Clearing every session_state key (and letting the sign-in
    gate's own init code recreate gmail_authed/gmail_email as fresh
    defaults, and the session-id block below recreate a brand-new empty
    ADK session) guarantees a completely blank slate for the next
    account, not just a cleared login.
    """
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def _clear_screen() -> None:
    """Resets the chat transcript and dashboard back to blank — a fresh ADK
    session with no reconciled_data — WITHOUT signing out. Unlike
    _reset_session_for_new_account, this keeps gmail_authed/gmail_email
    intact: it's for "start a new run" mid-session, not "a different person
    is signing in now"."""
    for key in ("messages", "adk_session_id"):
        if key in st.session_state:
            del st.session_state[key]


def _do_sign_in() -> None:
    """Shared by the corner button and the gate screen's centered button —
    both are the same action, so neither gets its own copy of the error
    handling."""
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
            # GoogleSignInError — never show raw library/stack trace text
            # to the person signing in.
            st.error("⚠️ Something went wrong while signing in. Please try again in a moment.")


# --- Top-right toolbar strip ----------------------------------------------
# Occupies the space Streamlit's own "Deploy" button used to sit in (that
# button is hidden via CSS; deploying is still reachable from the ⋮ menu's
# "Deploy this app" item, so no capability is removed). The ⋮ menu itself
# stays put, immediately to the right of this strip.
#
# Rendered before the sign-in gate's st.stop() so it's present once
# signed in: "Clear screen" plus a clickable account popover. The trigger
# is a plain generic account-circle icon — no visible email, no colored
# initial — the address only shows once the menu is actually opened.
# Signed out, this strip renders nothing at all: the centered "Sign in
# with Google" button on the gate screen below is the only sign-in
# control, so there's exactly one, not two duplicates on the same screen.
with st.container(key="recon-topbar", horizontal=True):
    if st.session_state.gmail_authed:
        if st.button("Clear screen", key="recon_topbtn_clear", help="Start a fresh run — keeps you signed in."):
            _clear_screen()
            st.rerun()
        _email = st.session_state.gmail_email or "your account"
        with st.popover("", icon=":material/account_circle:", help=f"Account: {_email}"):
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:10px; padding:2px 4px 12px;">
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
        if st.button("🔐  Sign in with Google", key="gate_signin", use_container_width=True):
            _do_sign_in()
    st.stop()
# --- end sign-in gate ------------------------------------------------------


@st.cache_resource
def get_runner():
    from google.adk.runners import InMemoryRunner
    return InMemoryRunner(agent=root_agent, app_name="reconai")


runner = get_runner()

# --- Sidebar: run configuration ---
with st.sidebar:
    # Account controls (Clear screen / Sign out) live in the top-right
    # toolbar strip now, not here — see the "Top-right toolbar strip"
    # section above. Keeping a second copy in the sidebar would mean two
    # widgets doing the same thing and two places to keep in sync.
    st.header("Reconciliation setup")
    st.caption(
        "🔒 **No automatic ledger yet** — each run reports straight from "
        "Gmail with nothing persisted between runs, to keep API/credit "
        "usage down. You can still explicitly save a snapshot of the "
        "current dashboard to a Google Sheet below (see 'Save to Sheet' "
        "beside the dashboard) — that's a one-off overwrite, not a ledger."
    )
    st.markdown(
        f"""
        <div class="recon-info-row" style="font-size:14px; margin-bottom:4px; display:flex; align-items:center; flex-wrap:wrap;">
            <span>Google Sheet ID <span style="color:{TEXT_MUTED}; margin-left:4px;">(optional, for 'Save to Sheet')</span></span>
            <span class="recon-info-badge">i</span>
            <div class="recon-info-tooltip">
                <b>Setting up the Sheet:</b><br>
                1. Create a blank Sheet at <b>sheets.new</b> while signed into
                the <b>same Google account</b> you signed into ReconAI with.<br>
                2. Different account? Either switch accounts, or share the
                Sheet with <b>Editor</b> access to your ReconAI account.<br>
                3. Copy the ID from the URL — the part between
                <code>/d/</code> and <code>/edit</code>.<br>
                4. Paste just that ID below, then click
                <b>💾 Save to Sheet</b> after a run.<br><br>
                Saves overwrite a <b>Dashboard</b> tab each time —
                they never append. Other tabs are left untouched.
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
    budget_json = st.text_area(
        "Budget (optional, JSON)",
        value="",
        placeholder='{"Food": 8000, "Subscriptions": 2000}',
        height=80,
        key="budget_json_input",  # explicit key so _reset_session_for_new_account
                                   # is guaranteed to clear it on account switch
    )
    uploaded_csv = st.file_uploader(
        "Bank statement CSV (optional)",
        type=["csv"],
        key="uploaded_csv_input",  # same reasoning as budget_json_input above
    )

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

        # --- Export controls, rendered up into the header slot beside the
        # RECONAI title (_export_slot, created near the top of the script).
        # They only ever appear once we're inside this `if reconciled:`
        # branch — i.e. only when there's actually a report to export.
        #
        # "Save to Sheet" overwrites a "Dashboard" tab in the pasted Sheet
        # with this run's numbers every time it's clicked — never
        # automatic, never a growing ledger. "Export PDF" renders the same
        # report dict as a single bookmarked/navigable PDF
        # (tools/export_tools.py), generated fresh on every click and
        # never cached or written anywhere.
        #
        # Save results come back as st.toast rather than inline
        # success/error boxes: the header strip is too narrow for a
        # message, and a toast doesn't shove the dashboard down the page. ---
        with _export_slot.container(horizontal=True, horizontal_alignment="right"):
            _save_clicked = st.button("💾 Save to Sheet", key="export_save_sheet")
            # Same report dict as the dashboard below, plus the last chat
            # reply (if any) as a narrative appendix — see tools/export_tools.py.
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

        if _save_clicked:
            _sheet_id = (st.session_state.get("sheet_id_input") or "").strip()
            if not _sheet_id:
                st.toast("Paste a Google Sheet ID in the sidebar first — hover the ⓘ next to it for setup steps.", icon="⚠️")
            else:
                with st.spinner("Saving this dashboard snapshot to your Sheet..."):
                    _save_result = save_report_to_sheet(report, _sheet_id)
                if _save_result["status"] == "ok":
                    _sheet_url = f"https://docs.google.com/spreadsheets/d/{_sheet_id}/edit"
                    st.toast("Saved to the Sheet's Dashboard tab.", icon="✅")
                    st.caption(f"💾 Saved — [open the Dashboard tab]({_sheet_url}).")
                else:
                    st.toast("Couldn't save to that Sheet.", icon="⚠️")
                    st.caption(
                        "Couldn't save to that Sheet — double-check the Sheet ID and that "
                        "your signed-in Google account has edit access to it."
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
                # Money coming IN (salary/interest/dividends/investment
                # gains) vs. money going OUT — same categories
                # generate_monthly_report already split into income_breakdown,
                # so a transaction's category showing up there means it's an
                # inflow, not a spend.
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

        # --- money in this period: salary/interest/dividends/investment
        # gains — never counted in total_spent above, shown as its own
        # clearly-labeled figure so investment profit or a salary credit
        # never reads as "spend". ---
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

        # --- business vs personal split: freelancers and small business
        # owners routinely run both through one inbox/account, so surface
        # both clearly tagged rather than pretending the account is purely
        # one or the other. ---
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
