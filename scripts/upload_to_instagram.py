#!/usr/bin/env python3
"""
Publishes a rendered Short as an Instagram Reel via the Instagram Graph
API (Content Publishing API) - free, no billing, same "set up once"
pattern as the YouTube upload script.

Instagram's API can't accept a raw file upload for Reels - it needs a
*public URL* it can fetch the video from. The workflow hosts the mp4
temporarily as a GitHub Release asset (free, part of the repo you
already have) just long enough for Instagram to pull it, then deletes
the release.

One-time setup (do this once, not per run):
  1. Convert your Instagram account to a Professional (Business or
     Creator) account, and link it to a Facebook Page you control
     (Instagram app -> Settings -> Account type, then link/create a
     Facebook Page from the same screen).
  2. Go to developers.facebook.com -> My Apps -> Create App -> type
     "Business". Add the "Instagram Graph API" product to it.
  3. Use the Graph API Explorer (developers.facebook.com/tools/explorer):
     - Select your app, select your user, and request these permissions:
       instagram_basic, instagram_content_publish, pages_show_list,
       pages_read_engagement.
     - Generate a User Access Token, then click "Open in Access Token
       Debugger" and use "Extend Access Token" to get a long-lived
       token (60 days).
     - Call GET /me/accounts with that token to find your Facebook
       Page and its Page Access Token. A Page Access Token generated
       from a long-lived User Access Token does not expire under
       normal use.
     - Call GET /{page-id}?fields=instagram_business_account with the
       Page Access Token to get your Instagram Business Account ID.
  4. Add two GitHub Actions repo secrets:
       IG_USER_ID       -> the Instagram Business Account ID from step 3
       IG_ACCESS_TOKEN  -> the long-lived Page Access Token from step 3
  Until both secrets exist, this script no-ops (prints a notice and
  exits 0) so it never breaks the YouTube upload or the rest of the run.

pip install --break-system-packages requests
"""
import argparse
import os
import sys
import time

import requests

GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 600  # 10 min - Instagram's own processing can take a few minutes


def _get(url, **params):
    resp = requests.get(url, params=params, timeout=30)
    data = resp.json()
    if resp.status_code >= 400:
        raise RuntimeError(f"GET {url} failed: {data}")
    return data


def _post(url, **params):
    resp = requests.post(url, data=params, timeout=30)
    data = resp.json()
    if resp.status_code >= 400:
        raise RuntimeError(f"POST {url} failed: {data}")
    return data


def create_media_container(ig_user_id: str, access_token: str, video_url: str, caption: str) -> str:
    data = _post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        media_type="REELS",
        video_url=video_url,
        caption=caption[:2200],
        share_to_feed="true",
        access_token=access_token,
    )
    return data["id"]


def wait_until_ready(creation_id: str, access_token: str) -> None:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        data = _get(f"{GRAPH_BASE}/{creation_id}", fields="status_code,status", access_token=access_token)
        status_code = data.get("status_code")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram failed to process the video: {data}")
        if status_code == "EXPIRED":
            raise RuntimeError("Instagram container expired before it finished processing.")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError("Timed out waiting for Instagram to finish processing the video.")


def publish_media(ig_user_id: str, access_token: str, creation_id: str) -> str:
    data = _post(f"{GRAPH_BASE}/{ig_user_id}/media_publish", creation_id=creation_id, access_token=access_token)
    return data["id"]


def upload_reel(video_url: str, caption: str) -> str | None:
    ig_user_id = os.environ.get("IG_USER_ID")
    access_token = os.environ.get("IG_ACCESS_TOKEN")
    if not ig_user_id or not access_token:
        print("Instagram not configured yet (IG_USER_ID / IG_ACCESS_TOKEN secrets missing) - skipping Instagram publish.")
        return None

    creation_id = create_media_container(ig_user_id, access_token, video_url, caption)
    wait_until_ready(creation_id, access_token)
    media_id = publish_media(ig_user_id, access_token, creation_id)
    print(f"Published Instagram Reel: media id {media_id}")
    return media_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-url", required=True, help="Publicly reachable URL Instagram can fetch the mp4 from")
    parser.add_argument("--caption", required=True)
    args = parser.parse_args()

    try:
        upload_reel(args.video_url, args.caption)
    except Exception as exc:
        # Never fail the whole workflow run over Instagram - YouTube publish
        # already succeeded by the time this step runs, and content isn't
        # lost, it just didn't cross-post this one time.
        print(f"Instagram publish failed (non-fatal): {exc}", file=sys.stderr)
        sys.exit(0)
