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


def get_credentials() -> Credentials:
    """Loads (or refreshes, or mints) OAuth credentials for Gmail/Sheets.

    Returns:
        A valid google.oauth2.credentials.Credentials object.

    Raises:
        FileNotFoundError: if no token.json exists and credentials.json is
            also missing, so no flow can be started.
    """
    global _creds_cache
    if _creds_cache and _creds_cache.valid:
        return _creds_cache

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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


def get_service(api_name: str, version: str):
    """Builds a cached googleapiclient service (e.g. get_service('gmail', 'v1'))."""
    return build(api_name, version, credentials=get_credentials())
