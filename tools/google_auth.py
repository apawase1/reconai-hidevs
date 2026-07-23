"""tools/google_auth.py — shared OAuth credential loading for Gmail/Sheets."""

import json
import os
import threading
import wsgiref.simple_server

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google_auth_oauthlib.flow import _RedirectWSGIApp, _WSGIRequestHandler
from googleapiclient.discovery import build

class GoogleSignInError(Exception):
    """Raised for any Google sign-in failure, with a message already safe to show directly in the UI."""


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

_creds_cache = None

_SIGNED_IN_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Signed in</title>
<style>
body { margin:0; height:100vh; display:flex; align-items:center; justify-content:center;
  background:#040A14; color:#E8FBFF; font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
.card { text-align:center; padding:32px 40px; border-radius:14px; background:#081726;
  border:1px solid rgba(34,211,238,.3); }
h1 { font-size:19px; margin:0 0 8px; }
p { font-size:13px; color:#5E86A0; margin:0; }
</style></head>
<body><div class="card">
<h1>&#9989; Signed in</h1>
<p>This window closes automatically &mdash; you're back in ReconAI.</p>
</div>
<script>setTimeout(function () { window.close(); }, 400);</script>
</body></html>"""


def _serve_success_page_as_html() -> None:
    """Patches google_auth_oauthlib's OAuth redirect page to serve real HTML instead of hardcoded text/plain, so the auto-close script in _SIGNED_IN_HTML can actually run."""
    if getattr(_RedirectWSGIApp, "_reconai_html_patch", False):
        return

    def _html_call(self, environ, start_response):
        import wsgiref.util
        start_response("200 OK", [("Content-type", "text/html; charset=utf-8")])
        self.last_request_uri = wsgiref.util.request_uri(environ)
        return [self._success_message.encode("utf-8")]

    _RedirectWSGIApp.__call__ = _html_call
    _RedirectWSGIApp._reconai_html_patch = True


def load_cached_credentials() -> Credentials | None:
    """Loads/refreshes existing credentials from TOKEN_FILE without ever opening the interactive consent flow."""
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


def _new_flow() -> InstalledAppFlow:
    """Loads credentials.json and builds a fresh InstalledAppFlow, or raises a UI-safe GoogleSignInError."""
    if not os.path.exists(CREDENTIALS_FILE):
        raise GoogleSignInError(
            "Google sign-in isn't set up for this app yet. Please contact "
            "the app owner to finish setup before signing in."
        )
    try:
        with open(CREDENTIALS_FILE) as f:
            content = f.read()
        if not content.strip():
            raise ValueError("empty file")
        json.loads(content)
        return InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    except (json.JSONDecodeError, ValueError, KeyError, OSError):
        raise GoogleSignInError(
            "Google sign-in is temporarily unavailable — there's a setup "
            "issue on our end. Please contact the app owner."
        )


class PendingSignIn:
    """An interactive sign-in whose popup is open but hasn't redirected back yet; call poll() on each rerun until it returns Credentials."""

    def __init__(self, flow: InstalledAppFlow, server: wsgiref.simple_server.WSGIServer):
        self._flow = flow
        self._server = server
        self._wsgi_app = server.get_app()
        self._thread = threading.Thread(target=server.handle_request, daemon=True)
        self._thread.start()

    def poll(self) -> Credentials | None:
        """Returns Credentials once the popup's redirect has landed and the code has been exchanged, or None if still waiting."""
        if self._wsgi_app.last_request_uri is None:
            return None
        try:
            authorization_response = self._wsgi_app.last_request_uri.replace("http", "https")
            self._flow.fetch_token(authorization_response=authorization_response)
            return self._flow.credentials
        finally:
            self.cancel()

    def cancel(self) -> None:
        """Closes the local redirect server if it's still listening."""
        try:
            self._server.server_close()
        except OSError:
            pass


def begin_interactive_sign_in() -> tuple[str, PendingSignIn]:
    """Starts the OAuth flow without blocking: opens a local redirect server on a background thread and returns (auth_url, pending) — the caller renders auth_url as a real link (st.link_button) and polls pending.poll()."""
    flow = _new_flow()
    _serve_success_page_as_html()

    wsgi_app = _RedirectWSGIApp(_SIGNED_IN_HTML)
    wsgiref.simple_server.WSGIServer.allow_reuse_address = False
    server = wsgiref.simple_server.make_server(
        "localhost", 0, wsgi_app, handler_class=_WSGIRequestHandler
    )
    server.timeout = 300  # give up waiting for the redirect after 5 minutes so the background thread can't hang forever
    flow.redirect_uri = f"http://localhost:{server.server_port}/"
    auth_url, _ = flow.authorization_url()

    return auth_url, PendingSignIn(flow, server)


def get_credentials() -> Credentials:
    """Returns valid OAuth credentials, trying the silent path first and falling back to a blocking interactive consent flow."""
    global _creds_cache

    cached = load_cached_credentials()
    if cached:
        return cached

    flow = _new_flow()
    try:
        _serve_success_page_as_html()
        creds = flow.run_local_server(port=0, success_message=_SIGNED_IN_HTML)
    except Exception:
        raise GoogleSignInError(
            "Sign-in didn't finish — this can happen if the browser "
            'window was closed or the sign-in was cancelled. Please click '
            '"Sign in with Google" and try again.'
        )

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    _creds_cache = creds
    return creds


def finish_credentials(creds: Credentials) -> Credentials:
    """Caches and persists Credentials obtained via the async begin_interactive_sign_in()/PendingSignIn.poll() path."""
    global _creds_cache
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    _creds_cache = creds
    return creds


def get_signed_in_email(creds: Credentials) -> str | None:
    """Best-effort fetch of the signed-in Gmail address for display; returns None on any failure."""
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception:
        return None


def get_service(api_name: str, version: str):
    """Builds a googleapiclient service (e.g. get_service('gmail', 'v1'))."""
    return build(api_name, version, credentials=get_credentials())


def clear_cached_credentials() -> None:
    """Signs out: drops the in-memory credential cache and deletes token.json from disk."""
    global _creds_cache
    _creds_cache = None
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
