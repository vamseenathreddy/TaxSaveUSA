#!/usr/bin/env python3
"""weekly_review.py: weekly health review of the TaxSaveUSA channel (Data API only), saved to reports/.

Writes reports/LATEST.md (flags first), reports/weekly/<date>.json and appends a row to reports/history.json.
Flags: REACH COLLAPSE (median views of settled public posts under 20), POSTING STALLED (no public upload in 2 days),
NO SUBSCRIBER GROWTH (net change <= 0 since last week's row), and which kinds of post (Short, long) do best."""
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports"


def _seconds(iso: str) -> int:
    import re
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    h, mi, se = (int(x or 0) for x in m.groups()) if m else (0, 0, 0)
    return h * 3600 + mi * 60 + se


def main():
    sys.path.insert(0, str(ROOT / "scripts"))
    from upload_to_youtube import get_authenticated_service, list_recent_uploads
    yt = get_authenticated_service()
    now = datetime.now(timezone.utc)
    ch = yt.channels().list(part="statistics", mine=True).execute()["items"][0]["statistics"]
    subs, total_views = int(ch.get("subscriberCount", 0)), int(ch.get("viewCount", 0))
    ups = [u for u in list_recent_uploads(50) if u["privacy_status"] == "public"]
    ids = [u["video_id"] for u in ups]
    vids = {}
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="statistics,snippet,contentDetails", id=",".join(ids[i:i + 50])).execute().get("items", []):
            vids[v["id"]] = v
    rows = []
    for u in ups:
        v = vids.get(u["video_id"])
        if not v:
            continue
        age = (now - datetime.fromisoformat(u["published_at"].replace("Z", "+00:00"))).total_seconds() / 86400
        dur = v["contentDetails"]["duration"]
        rows.append({"id": u["video_id"], "title": v["snippet"]["title"], "age": round(age, 1), "views": int(v["statistics"].get("viewCount", 0)),
                     "kind": "long" if _seconds(dur) > 180 else "short"})
    settled = [r for r in rows if 2 <= r["age"] <= 9]
    last7 = [r for r in rows if r["age"] <= 7]
    med = statistics.median(r["views"] for r in settled) if settled else None
    hist_path = OUT / "history.json"
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else []
    prev = hist[-1] if hist else None
    flags = []
    if med is not None and len(settled) >= 5 and med < 20:
        flags.append(f"REACH COLLAPSE: median views of {len(settled)} settled posts is {med:.0f}")
    if not rows or min(r["age"] for r in rows) >= 2:
        flags.append("POSTING STALLED: no public upload in the last 2 days")
    if prev and subs - prev["subs"] <= 0:
        flags.append("NO SUBSCRIBER GROWTH since last week")
    by_kind = {}
    for k in ("short", "long"):
        xs = [r["views"] for r in settled if r["kind"] == k]
        by_kind[k] = statistics.median(xs) if xs else None
    row = {"date": now.date().isoformat(), "subs": subs, "total_views": total_views, "uploads_7d": len(last7), "median_settled": med, "median_short": by_kind["short"], "median_long": by_kind["long"]}
    hist.append(row)
    OUT.mkdir(exist_ok=True)
    (OUT / "weekly").mkdir(exist_ok=True)
    hist_path.write_text(json.dumps(hist, indent=1))
    best = sorted(settled, key=lambda r: -r["views"])[:3]
    worst = sorted(settled, key=lambda r: r["views"])[:3]
    md = [f"# TaxSaveUSA weekly review, {row['date']}\n", "## Flags\n" + ("".join(f"- {f}\n" for f in flags) or "- none\n"),
          f"- subscribers {subs}" + (f" ({subs - prev['subs']:+d} since last review)" if prev else ""),
          f"- total views {total_views}, uploads in the last 7 days {len(last7)}",
          f"- median views of settled posts: {med}, Shorts {by_kind['short']}, long videos {by_kind['long']}\n"]
    md += [f"- best: {r['views']} views ({r['kind']}), {r['title'][:70]}" for r in best] + [f"- weakest: {r['views']} views ({r['kind']}), {r['title'][:70]}" for r in worst]
    text = "\n".join(md) + "\n"
    (OUT / "LATEST.md").write_text(text)
    (OUT / "weekly" / f"{row['date']}.json").write_text(json.dumps({"row": row, "posts": rows}, indent=1))
    print(text)
    for f in flags:
        print(f"::warning title=weekly review::{f}")


if __name__ == "__main__":
    main()
