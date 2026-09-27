#!/usr/bin/env python3
"""
Run this ONCE, interactively, on your own machine (not in CI) to get a
refresh token. It opens a browser, you sign in as the channel's
Google account, and it prints the refresh token to save as a GitHub
Actions secret.

pip install --break-system-packages google-auth-oauthlib

Usage:
  1. Download your OAuth client JSON from Google Cloud Console
     (APIs & Services -> Credentials -> your Desktop OAuth client)
     and save it as client_secret.json next to this script.
  2. python3 youtube_auth_bootstrap.py
  3. Copy the printed refresh_token into the YT_REFRESH_TOKEN secret,
     and the client_id/client_secret into YT_CLIENT_ID / YT_CLIENT_SECRET.
  4. Delete client_secret.json locally afterwards - don't commit it.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\n--- SAVE THESE AS GITHUB ACTIONS SECRETS ---")
print(f"YT_CLIENT_ID={creds.client_id}")
print(f"YT_CLIENT_SECRET={creds.client_secret}")
print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
