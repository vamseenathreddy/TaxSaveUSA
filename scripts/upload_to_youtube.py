#!/usr/bin/env python3
"""
Uploads a rendered short to YouTube via the Data API v3, then posts a
rotating "ask for opinions" comment on it to seed engagement.

One-time setup (do this once, not per run):
  1. Google Cloud Console -> new project -> enable "YouTube Data API v3"
  2. Create OAuth 2.0 Client ID (Desktop app type)
  3. Run scripts/youtube_auth_bootstrap.py once locally/interactively to
     produce a refresh token (see that script) - save the refresh token
     as a GitHub Actions secret (YT_REFRESH_TOKEN), alongside
     YT_CLIENT_ID / YT_CLIENT_SECRET. That script requests both the
     youtube.upload scope (for uploading) and youtube.force-ssl (for
     posting the rotating comment) - if your existing refresh token
     predates the comment feature, re-run the bootstrap script and
     replace YT_REFRESH_TOKEN, otherwise uploads keep working fine but
     comment posting silently no-ops (see post_engagement_comment below).

This script then runs unattended in CI using the refresh token - no
browser needed after the one-time bootstrap.

pip install --break-system-packages google-auth google-auth-oauthlib google-api-python-client
"""
import argparse
import json
import os
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

ROOT = Path(__file__).resolve().parent.parent
COMMENT_PROMPTS_PATH = ROOT / "content" / "comment_prompts.json"


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
    video_id = response["id"]
    print(f"Uploaded: https://youtube.com/watch?v={video_id}")

    post_engagement_comment(youtube, video_id)

    return video_id


def post_engagement_comment(youtube, video_id: str) -> None:
    """
    Posts one comment from the rotating pool in content/comment_prompts.json,
    advancing next_index so the pool cycles through ~36 different prompts
    before repeating instead of posting the same line on every video.

    Requires the youtube.force-ssl scope on the refresh token. If that
    scope isn't granted yet (old token), or the API call fails for any
    other reason, this prints a clear message and returns quietly -
    never fails the upload, which already succeeded by this point.
    """
    try:
        with open(COMMENT_PROMPTS_PATH, encoding="utf-8-sig") as f:
            data = json.load(f)
        prompts = data["prompts"]
        idx = data.get("next_index", 0) % len(prompts)
        comment_text = prompts[idx]
        data["next_index"] = (idx + 1) % len(prompts)
        with open(COMMENT_PROMPTS_PATH, "w") as f:
            json.dump(data, f, indent=2)

        youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {"snippet": {"textOriginal": comment_text}},
                }
            },
        ).execute()
        print(f"Posted engagement comment (#{idx}): {comment_text}")
    except HttpError as exc:
        print(
            f"Could not post engagement comment (non-fatal, upload already succeeded): {exc}. "
            "If this is a permissions error, YT_REFRESH_TOKEN likely needs the youtube.force-ssl "
            "scope - re-run scripts/youtube_auth_bootstrap.py and update the secret.",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"Could not post engagement comment (non-fatal, upload already succeeded): {exc}", file=sys.stderr)


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
