#!/usr/bin/env python3
"""
Staged-rollout scheduler for the Shorts pipeline.

Run this on a tight cron (every 15-30 min) from GitHub Actions. Note:
GitHub does NOT reliably honor short cron intervals for scheduled
workflows - ticks have been observed landing every 5-7 hours instead
of every 30 min, especially on lower-traffic/public repos. This
script is written to be resilient to that: it never assumes the
previous tick happened on time.

On each run it:
  1. Ensures TODAY (US-Eastern date) has picked posting times, randomly
     chosen inside today's windows (config/rollout.json), DST-safe via
     zoneinfo. Saved to state/schedule_<date>.json.
  2. Scans EVERY state/schedule_<date>.json file (not just today's) for
     the oldest picked time that has been crossed and not yet fired.
     This is the key fix for GitHub's unreliable scheduling: if no tick
     lands between a late-evening ET slot and ET midnight, the very
     next tick - even if it's already the next ET calendar day - will
     still find and fire that missed slot instead of silently losing
     it forever in an abandoned prior-day file.
  3. Fires at most one slot per tick (oldest overdue first), prints
     "fire" (exit 0) so the workflow's next step runs generate_video.py
     + upload_to_youtube.py/upload_to_instagram.py. Otherwise prints
     "wait" (exit 0) and the workflow skips the publish step.

Because ticks can be sparse, a missed slot is delayed rather than
dropped - it fires on the next tick that happens to run, whenever
that is. If you need publish times closer to the randomly chosen ones,
consider triggering this workflow from an external cron pinger (e.g.
a free service like cron-job.org hitting the workflow_dispatch REST
API) instead of relying solely on GitHub's own `schedule:` trigger.
"""
import json
import random
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "rollout.json"
STATE_DIR = ROOT / "state"
ET = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Calcutta")
UTC = ZoneInfo("UTC")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def active_stage(cfg, today_et: date):
    start = date.fromisoformat(cfg["rollout_start_date"])
    days_since_start = (today_et - start).days
    for stage in cfg["stages"]:
        lo, hi = stage["day_range"]
        if days_since_start >= lo and (hi is None or days_since_start <= hi):
            return stage, days_since_start
    # Fallback: before rollout_start_date, or config gap -> most conservative stage
    return cfg["stages"][0], days_since_start


def parse_window(window_str, on_date: date):
    """'19:00-22:00' + a date -> (start_dt, end_dt) localized to ET."""
    start_s, end_s = window_str.split("-")
    sh, sm = map(int, start_s.split(":"))
    eh, em = map(int, end_s.split(":"))
    start_dt = datetime(on_date.year, on_date.month, on_date.day, sh, sm, tzinfo=ET)
    end_dt = datetime(on_date.year, on_date.month, on_date.day, eh, em, tzinfo=ET)
    return start_dt, end_dt


def pick_times_for_today(stage, today_et: date, seed_key: str):
    """Deterministically random: same seed_key -> same picks if rerun."""
    rng = random.Random(seed_key)
    n = stage["posts_per_day"]
    windows = stage["windows_et"]
    # Spread posts across windows as evenly as possible
    picks = []
    for i in range(n):
        window = windows[i % len(windows)]
        start_dt, end_dt = parse_window(window, today_et)
        span_seconds = int((end_dt - start_dt).total_seconds())
        offset = rng.randint(0, max(span_seconds, 0))
        picks.append((start_dt + timedelta(seconds=offset)).isoformat())
    picks.sort()
    return picks


def state_path_for(day: date):
    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / f"schedule_{day.isoformat()}.json"


def ensure_today_scheduled(cfg, today_et: date):
    """Creates today's state file (with freshly picked times) if it
    doesn't exist yet. Returns (stage, day_num) for today."""
    stage, day_num = active_stage(cfg, today_et)
    state_file = state_path_for(today_et)
    if not state_file.exists():
        picks = pick_times_for_today(stage, today_et, seed_key=today_et.isoformat())
        state = {
            "stage": stage["name"],
            "day_num_since_rollout_start": day_num,
            "picked_times_et": picks,
            "fired": {},
        }
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
    return stage, day_num


def find_oldest_overdue_slot(now_et: datetime):
    """
    Scans every state/schedule_*.json file (oldest date first), not
    just today's, and returns (state_file_path, state_dict, slot_iso)
    for the oldest picked time that is <= now and not yet fired. This
    is what lets a slot missed by a sparse/late GitHub Actions tick
    still fire on a later tick, even after the ET calendar date has
    rolled over past midnight.

    Returns (None, None, None) if nothing is overdue.
    """
    STATE_DIR.mkdir(exist_ok=True)
    state_files = sorted(STATE_DIR.glob("schedule_*.json"))
    for state_file in state_files:
        try:
            with open(state_file) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for t in sorted(state.get("picked_times_et", [])):
            target = datetime.fromisoformat(t)
            if target <= now_et and not state.get("fired", {}).get(t):
                return state_file, state, t
    return None, None, None


def main():
    cfg = load_config()
    now_utc = datetime.now(tz=UTC)
    now_et = now_utc.astimezone(ET)
    today_et = now_et.date()

    # Always make sure today has a schedule, regardless of whether we
    # end up firing something from an older, missed day this tick.
    stage, day_num = ensure_today_scheduled(cfg, today_et)

    state_file, state, slot = find_oldest_overdue_slot(now_et)

    if state_file is None:
        print("wait")
        sys.exit(0)

    state["fired"][slot] = now_utc.isoformat()
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    print("fire")
    print(
        f"# fired_from={state_file.name} slot_et={slot} now_ist={now_utc.astimezone(IST).isoformat()}",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
