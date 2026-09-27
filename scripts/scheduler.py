#!/usr/bin/env python3
"""
Staged-rollout scheduler for the Shorts pipeline.

Run this on a tight cron (every 15-30 min) from GitHub Actions.
On each run it:
  1. Works out which rollout stage today falls into (config/rollout.json).
  2. If today has no picked posting times yet, randomly picks
     `posts_per_day` timestamps inside today's US-Eastern windows
     (DST-safe via zoneinfo) and saves them to state/schedule_<date>.json.
  3. Checks whether any picked time has just been crossed and not yet
     fired. If so, prints "FIRE" (exit code 0, stdout "fire") so the
     workflow's next step runs generate_video.py + upload_to_youtube.py.
     Otherwise prints "WAIT" (exit code 0, stdout "wait") and the
     workflow skips the publish step for this tick.

State is kept in state/schedule_<date>.json so re-running mid-day (or
after a workflow retry) does not repick times or double-fire.
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


def state_path_for(today_et: date):
    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / f"schedule_{today_et.isoformat()}.json"


def main():
    cfg = load_config()
    now_utc = datetime.now(tz=UTC)
    now_et = now_utc.astimezone(ET)
    today_et = now_et.date()

    stage, day_num = active_stage(cfg, today_et)
    state_file = state_path_for(today_et)

    if state_file.exists():
        with open(state_file) as f:
            state = json.load(f)
    else:
        picks = pick_times_for_today(stage, today_et, seed_key=today_et.isoformat())
        state = {
            "stage": stage["name"],
            "day_num_since_rollout_start": day_num,
            "picked_times_et": picks,
            "fired": {},
        }
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

    fired_any = False
    for t in state["picked_times_et"]:
        target = datetime.fromisoformat(t)
        if target <= now_et and not state["fired"].get(t):
            state["fired"][t] = now_utc.isoformat()
            fired_any = True
            break  # fire one slot per tick; next tick handles the next slot

    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    if fired_any:
        print("fire")
        print(f"# stage={stage['name']} slot_et={t} now_ist={now_utc.astimezone(IST).isoformat()}", file=sys.stderr)
        sys.exit(0)
    else:
        print("wait")
        sys.exit(0)


if __name__ == "__main__":
    main()
