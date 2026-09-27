#!/usr/bin/env python3
"""
Uploads a rendered short to YouTube via the Data API v3.

One-time setup (do this once, not per run):
  1. Google Cloud Console -> new project -> enable "YouTube Data API v3"
  2. Create OAuth 2.0 Client ID (Desktop app type)
  3. Run scripts/youtube_auth_bootstrap.py once locally/interactively to
     produce a refresh token (see that script) - save the refresh token
     as a GitHub Actions secret (YT_REFRESH_TOKEN), alongside
     YT_CLIENT_ID / YT_CLIENT_SECRET.

This script then runs unattended in CI using the refresh token - no
browser needed after the one-time bootstrap.

pip install --break-system-packages google-auth google-auth-oauthlib google-api-python-client
"""
import argparse
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_authenticated_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def upload_short(video_path: Path, title: str, description: str, tags: list[str], publish_at_iso_utc: str | None = None):
    youtube = get_authenticated_service()

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags,
            "categoryId": "27",  # Education; consider "22" People & Blogs if it fits better
        },
        "status": {
            "selfDeclaredMadeForKids": False,
            "privacyStatus": "private" if publish_at_iso_utc else "public",
        },
    }
    if publish_at_iso_utc:
        body["status"]["publishAt"] = publish_at_iso_utc  # schedules it; auto-flips to public at that time

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"Uploaded: https://youtube.com/watch?v={response['id']}")
    return response["id"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--tags", default="")
    parser.add_argument("--publish-at", default=None, help="ISO 8601 UTC, e.g. 2026-09-29T23:58:24Z")
    args = parser.parse_args()

    upload_short(
        Path(args.video),
        args.title,
        args.description,
        [t.strip() for t in args.tags.split(",") if t.strip()],
        args.publish_at,
    )
