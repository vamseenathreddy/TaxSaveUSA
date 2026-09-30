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
  2. In the Google Cloud Console OAuth consent screen, make sure the
     youtube.force-ssl scope is added to the app (same place you added
     youtube.upload) - this is the scope that lets the bot post the
     rotating engagement comment on each upload. It's still a
     restricted/sensitive scope, so it works the same way youtube.upload
     already does while the app is in Testing mode (test users only).
  3. python3 youtube_auth_bootstrap.py
  4. Copy the printed refresh_token into the YT_REFRESH_TOKEN secret,
     and the client_id/client_secret into YT_CLIENT_ID / YT_CLIENT_SECRET.
     If you already have a YT_REFRESH_TOKEN from before, this new one
     REPLACES it - the old token only covers youtube.upload and can't
     post comments, so uploads would keep working but comment posting
     would silently no-op until you update the secret with this new
     token.
  5. Delete client_secret.json locally afterwards - don't commit it.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # needed to post the rotating engagement comment
]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\n--- SAVE THESE AS GITHUB ACTIONS SECRETS ---")
print(f"YT_CLIENT_ID={creds.client_id}")
print(f"YT_CLIENT_SECRET={creds.client_secret}")
print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
