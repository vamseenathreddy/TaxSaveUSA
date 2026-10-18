#!/usr/bin/env python3
"""
Reads channel + recent-Short performance from the YouTube Data API and
adjusts how many Shorts/day the scheduler posts (state/pace.json), so the
channel ramps up only while the numbers support it.

Uses only the Data API (videos.list / channels.list statistics), which the
existing OAuth token's scope already covers - no extra permissions.

Rule (deliberately conservative; reviewed at most once every 7 days):
  - Compare the average views of the 10 most recent Shorts that are at
    least 48h old against the same measure at the previous review.
  - Subscribers must not have fallen since the previous review.
  - UP one step when avg views are flat-or-better (>= 90% of previous)
    AND subscribers did not fall AND avg views >= MIN_AVG_VIEWS.
  - DOWN one step when avg views fell by more than 40%.
  - Otherwise HOLD.
  Ladder: 2 -> 3 -> 4 -> 5 -> 6 posts/day. First review starts at 2/day.

Run:  python3 scripts/analytics_pace.py            (needs YT_* env vars)
Test: python3 scripts/analytics_pace.py --selftest
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACE_PATH = ROOT / "state" / "pace.json"
ANALYTICS_PATH = ROOT / "state" / "analytics.json"
LADDER = [2, 3, 4, 5, 6]
MIN_AVG_VIEWS = 30
REVIEW_EVERY_DAYS = 7
SAMPLE = 10
MIN_AGE_HOURS = 48


def decide_pace(current, avg_views, prev_avg_views, subs, prev_subs):
    """Pure function: returns (new_posts_per_day, reason)."""
    idx = LADDER.index(current) if current in LADDER else 0
    if prev_avg_views is None or prev_subs is None:
        return current, "first review: baseline recorded, holding"
    if prev_avg_views > 0 and avg_views < 0.6 * prev_avg_views:
        return LADDER[max(idx - 1, 0)], f"avg views fell {avg_views:.0f} vs {prev_avg_views:.0f}: stepping down"
    if (avg_views >= 0.9 * prev_avg_views and subs >= prev_subs
            and avg_views >= MIN_AVG_VIEWS):
        return LADDER[min(idx + 1, len(LADDER) - 1)], (
            f"avg views {avg_views:.0f} (prev {prev_avg_views:.0f}), subs {subs} (prev {prev_subs}): stepping up")
    return current, f"avg views {avg_views:.0f} (prev {prev_avg_views:.0f}), subs {subs} (prev {prev_subs}): holding"


def load_pace():
    try:
        return json.loads(PACE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"posts_per_day": LADDER[0], "last_review": None, "avg_views": None, "subscribers": None, "history": []}


def fetch_stats():
    sys.path.insert(0, str(ROOT / "scripts"))
    from upload_to_youtube import get_authenticated_service, list_recent_uploads
    yt = get_authenticated_service()
    ch = yt.channels().list(part="statistics", mine=True).execute()["items"][0]["statistics"]
    subs = int(ch.get("subscriberCount", 0))
    uploads = [u for u in list_recent_uploads(25) if u["privacy_status"] == "public"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MIN_AGE_HOURS)
    ids = [u["video_id"] for u in uploads
           if datetime.fromisoformat(u["published_at"].replace("Z", "+00:00")) <= cutoff][:SAMPLE]
    videos = []
    if ids:
        resp = yt.videos().list(part="statistics,snippet", id=",".join(ids)).execute()
        for v in resp.get("items", []):
            s = v["statistics"]
            videos.append({"id": v["id"], "title": v["snippet"]["title"], "views": int(s.get("viewCount", 0)),
                           "likes": int(s.get("likeCount", 0)), "comments": int(s.get("commentCount", 0))})
    return subs, videos


def main():
    pace = load_pace()
    now = datetime.now(timezone.utc)
    if pace.get("last_review"):
        last = datetime.fromisoformat(pace["last_review"])
        if now - last < timedelta(days=REVIEW_EVERY_DAYS):
            print(f"reviewed {last.date()}, next review after {(last + timedelta(days=REVIEW_EVERY_DAYS)).date()}; no change")
            return
    subs, videos = fetch_stats()
    avg = sum(v["views"] for v in videos) / len(videos) if videos else 0.0
    new, reason = decide_pace(pace["posts_per_day"], avg, pace.get("avg_views"), subs, pace.get("subscribers"))
    pace["history"] = (pace.get("history", []) + [{"date": now.date().isoformat(), "from": pace["posts_per_day"],
                                                   "to": new, "avg_views": round(avg, 1), "subscribers": subs, "reason": reason}])[-52:]
    pace.update({"posts_per_day": new, "last_review": now.isoformat(), "avg_views": avg, "subscribers": subs})
    PACE_PATH.parent.mkdir(exist_ok=True)
    PACE_PATH.write_text(json.dumps(pace, indent=2))
    ANALYTICS_PATH.write_text(json.dumps({"as_of": now.isoformat(), "subscribers": subs,
                                          "avg_views_recent": avg, "videos": videos}, indent=2))
    print(f"pace {pace['history'][-1]['from']} -> {new}/day: {reason}")


def selftest():
    assert decide_pace(2, 100, None, 10, None)[0] == 2
    assert decide_pace(2, 100, 90, 12, 10)[0] == 3
    assert decide_pace(6, 500, 400, 50, 40)[0] == 6
    assert decide_pace(3, 100, 90, 9, 10)[0] == 3      # subs fell -> hold
    assert decide_pace(3, 10, 90, 12, 10)[0] == 2      # views collapsed -> down
    assert decide_pace(2, 10, 12, 12, 10)[0] == 2      # below MIN_AVG_VIEWS -> hold
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
