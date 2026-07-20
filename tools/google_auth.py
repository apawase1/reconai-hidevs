"""tools/google_auth.py — shared OAuth credential loading for Gmail/Sheets.

Single source of truth for auth so discovery_tools.py and
reconciliation_tools.py don't each reimplement the OAuth dance. Same logic
as the original test_auth.py, generalized into a reusable helper.

Scopes are least-privilege per the architecture doc's guardrail 5.1:
readonly for Gmail, read-write only for the Sheet you use as the ledger
(there is no less-privileged Sheets scope that still allows writing
reconciled rows). Drive scope has been dropped — Discovery is Gmail-only
for now (see RECONAI_ARCHITECTURE_ADDENDUM.md section D). If you re-add
Drive later, add drive.readonly back here AND delete token.json so the
next auth flow requests the new scope (a cached token only carries the
scopes it was first granted).

CAVEAT (Cloud Run): InstalledAppFlow.run_local_server() opens a local
browser and only works on a machine with a display, i.e. your laptop, not
inside the deployed container. For the deployed demo, mint token.json
locally first (run this module or test_auth.py once), then ship it to the
Cloud Run service as a mounted secret rather than baking it into the image.
Cloud Run instances are stateless, so expect to refresh/re-mint after long
idle periods. See README.md "Deployment" section.
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

_creds_cache = None  # process-local cache so we don't re-read disk every tool call


def load_cached_credentials() -> Credentials | None:
    """Silently loads and refreshes existing credentials from TOKEN_FILE,
    WITHOUT ever opening a browser or starting the interactive consent
    flow. Returns None if there's no valid (or refreshable) token yet.

    This is the piece that lets app.py show an explicit "Sign in with
    Google" screen at app open instead of only discovering there's no
    session mid-conversation, the first time a Gmail tool happens to run:
    the UI calls this once on load to check "is anyone already signed
    in?" and only falls through to the interactive flow (via
    get_credentials(), below) when the user deliberately clicks a
    sign-in button.
    """
    global _creds_cache
    if _creds_cache and _creds_cache.valid:
        return _creds_cache

    if not os.path.exists(TOKEN_FILE):
        return None

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    except (ValueError, OSError):
        return None

    if creds and creds.valid:
        _creds_cache = creds
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            return None
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        _creds_cache = creds
        return creds

    return None


def get_credentials() -> Credentials:
    """Loads (or refreshes, or mints) OAuth credentials for Gmail/Sheets.

    Tries the silent path first (load_cached_credentials); only starts the
    interactive browser consent flow if there's genuinely no usable token.

    Returns:
        A valid google.oauth2.credentials.Credentials object.

    Raises:
        FileNotFoundError: if no token.json exists and credentials.json is
            also missing, so no flow can be started.
    """
    global _creds_cache

    cached = load_cached_credentials()
    if cached:
        return cached

    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"{CREDENTIALS_FILE} not found. Download OAuth Desktop "
            "credentials from Google Cloud Console and place it here, "
            "or run test_auth.py locally once and ship the resulting "
            "token.json to your deployment as a secret."
        )
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    _creds_cache = creds
    return creds


def get_signed_in_email(creds: Credentials) -> str | None:
    """Best-effort fetch of the signed-in Gmail address, purely for display
    ("Signed in as ..."). Returns None on any failure rather than raising —
    this is cosmetic, not load-bearing for the actual pipeline."""
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception:
        return None


def get_service(api_name: str, version: str):
    """Builds a cached googleapiclient service (e.g. get_service('gmail', 'v1'))."""
    return build(api_name, version, credentials=get_credentials())


def clear_cached_credentials() -> None:
    """Signs out: drops the in-memory credential cache and deletes
    token.json from disk, so the next load_cached_credentials()/
    get_credentials() call has nothing to find and the UI's sign-in
    screen reappears. Used by app.py's "Sign out / switch account"
    button — the same effect as the manual `rm token.json` + restart
    dance, without leaving the running process."""
    global _creds_cache
    _creds_cache = None
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
