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


def upload_short(video_path: Path, title: str, description: str, tags: list[str],
                  publish_at_iso_utc: str | None = None, privacy_override: str | None = None):
    youtube = get_authenticated_service()

    # privacy_override lets a caller (e.g. a manual re-upload for review
    # before the channel owner approves it going live) force the initial
    # privacyStatus to "private"/"unlisted" regardless of publish_at -
    # the normal automated flow still defaults to "public" (or "private"
    # when a publishAt schedule is given, which YouTube auto-flips to
    # public at that time).
    privacy_status = privacy_override or ("private" if publish_at_iso_utc else "public")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags,
            "categoryId": "27",  # Education; consider "22" People & Blogs if it fits better
            # Declares the video's language explicitly instead of leaving
            # it unset - without this YouTube can misjudge which locale's
            # search/suggested results to surface the video in, which is
            # a quiet but real reach-limiting gap for English-language
            # search and recommendations.
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "selfDeclaredMadeForKids": False,
            "privacyStatus": privacy_status,
        },
    }
    if publish_at_iso_utc and not privacy_override:
        body["status"]["publishAt"] = publish_at_iso_utc  # schedules it; auto-flips to public at that time

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    video_id = response["id"]
    print(f"Uploaded ({privacy_status}): https://youtube.com/watch?v={video_id}")

    post_engagement_comment(youtube, video_id)

    return video_id, privacy_status


def list_recent_uploads(max_results: int = 8):
    """Lists this channel's most recent uploads (video id, title,
    published time, privacy status) via its uploads playlist - used to
    find the video id of an already-published Short so it can be
    deleted or have its privacy changed (the automated pipeline never
    records video ids anywhere in the repo, so this is the way to
    recover one after the fact)."""
    youtube = get_authenticated_service()
    channels_resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_playlist_id = channels_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items_resp = youtube.playlistItems().list(
        part="snippet,contentDetails", playlistId=uploads_playlist_id, maxResults=max_results,
    ).execute()
    video_ids = [item["contentDetails"]["videoId"] for item in items_resp.get("items", [])]
    status_resp = youtube.videos().list(part="status", id=",".join(video_ids)).execute() if video_ids else {"items": []}
    privacy_by_id = {v["id"]: v["status"]["privacyStatus"] for v in status_resp.get("items", [])}

    results = []
    for item in items_resp.get("items", []):
        vid = item["contentDetails"]["videoId"]
        sn = item["snippet"]
        results.append({
            "video_id": vid,
            "title": sn["title"],
            "published_at": sn.get("videoPublishedAt") or sn.get("publishedAt"),
            "privacy_status": privacy_by_id.get(vid, "unknown"),
            "url": f"https://youtube.com/watch?v={vid}",
        })
    return results


def delete_video(video_id: str) -> None:
    youtube = get_authenticated_service()
    youtube.videos().delete(id=video_id).execute()
    print(f"Deleted: {video_id}")


def set_video_privacy(video_id: str, privacy_status: str) -> None:
    youtube = get_authenticated_service()
    youtube.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": privacy_status}},
    ).execute()
    print(f"Set privacy of {video_id} to {privacy_status}")


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
    parser.add_argument("--video", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--tags", default="")
    parser.add_argument("--publish-at", default=None, help="ISO 8601 UTC, e.g. 2026-09-29T23:58:24Z")
    parser.add_argument("--privacy", default=None, choices=["public", "private", "unlisted"],
                         help="Force the initial privacyStatus on upload (overrides --publish-at's "
                              "default). Use 'private' or 'unlisted' to upload for review before "
                              "going live.")
    # One-off channel-management ops, mutually exclusive with uploading:
    parser.add_argument("--list-recent", type=int, default=None, metavar="N",
                         help="List the channel's N most recent uploads (id/title/privacy) and exit.")
    parser.add_argument("--delete", default=None, metavar="VIDEO_ID",
                         help="Delete this video id and exit.")
    parser.add_argument("--set-privacy", nargs=2, default=None, metavar=("VIDEO_ID", "STATUS"),
                         help="Set an existing video's privacyStatus (public/private/unlisted) and exit.")
    parser.add_argument("--result-file", default=None,
                         help="Write a JSON summary of whichever action ran to this path, so a CI "
                              "run can commit it back to the repo for later reading (CI logs aren't "
                              "otherwise retrievable after the fact).")
    args = parser.parse_args()

    result = None

    if args.list_recent is not None:
        result = {"action": "list-recent", "uploads": list_recent_uploads(args.list_recent)}
        for u in result["uploads"]:
            print(f"{u['video_id']}  [{u['privacy_status']:>9}]  {u['published_at']}  {u['title']}")
    elif args.delete is not None:
        delete_video(args.delete)
        result = {"action": "delete", "video_id": args.delete}
    elif args.set_privacy is not None:
        video_id, status = args.set_privacy
        set_video_privacy(video_id, status)
        result = {"action": "set-privacy", "video_id": video_id, "privacy_status": status}
    else:
        if not (args.video and args.title and args.description):
            parser.error("--video/--title/--description are required unless using "
                          "--list-recent/--delete/--set-privacy")
        video_id, privacy_status = upload_short(
            Path(args.video),
            args.title,
            args.description,
            [t.strip() for t in args.tags.split(",") if t.strip()],
            args.publish_at,
            args.privacy,
        )
        result = {"action": "upload", "video_id": video_id, "privacy_status": privacy_status,
                   "url": f"https://youtube.com/watch?v={video_id}"}

    if args.result_file:
        result_path = Path(args.result_file)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, indent=2))
