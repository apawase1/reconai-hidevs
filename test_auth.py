"""
test_auth.py — standalone OAuth verification.

Confirms the OAuth flow works and Gmail API access is live,
before any Streamlit or LangChain code touches it.

Run: python test_auth.py
First run opens a browser for Google consent and saves token.json.
Subsequent runs reuse token.json silently.
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes must match what you added to the OAuth consent screen.
# If you add more scopes later, delete token.json and re-run —
# a cached token only carries the scopes it was first granted.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/tasks",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def get_credentials():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"{CREDENTIALS_FILE} not found. Download it from "
                    "Google Cloud Console > APIs & Services > Credentials "
                    "and place it in this directory."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds


def list_recent_subjects(creds, max_results=5):
    service = build("gmail", "v1", credentials=creds)
    results = (
        service.users()
        .messages()
        .list(userId="me", maxResults=max_results)
        .execute()
    )
    messages = results.get("messages", [])

    if not messages:
        print("No messages found. (Auth works, inbox may just be empty/filtered.)")
        return

    print(f"\nAuth successful. Fetching {len(messages)} recent subjects:\n")
    for msg in messages:
        msg_data = (
            service.users()
            .messages()
            .get(userId="me", id=msg["id"], format="metadata",
                 metadataHeaders=["Subject", "From"])
            .execute()
        )
        headers = msg_data.get("payload", {}).get("headers", [])
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(no subject)")
        sender = next((h["value"] for h in headers if h["name"] == "From"), "(unknown sender)")
        print(f"  - {subject}  |  from: {sender}")


if __name__ == "__main__":
    print("Starting OAuth flow (browser will open on first run)...")
    credentials = get_credentials()
    list_recent_subjects(credentials)
    print("\nDone. token.json is now saved for future runs.")
