#!/usr/bin/env python3
"""
Long (3 to 6 minute, 16:9) video for TaxSaveUSA. Two formats that alternate day by day:

  tax   an original explainer from content/long_tax.json ("7 tax breaks people miss"), one slide per point
  news  "Top trending US stories today": 6 to 8 safe stories from the same free feeds the Shorts use
        (scripts/trending.py), each credited to its publisher with a link in the description

Narration is edge-tts (free), slides are drawn with Pillow and animated with a slow zoom in ffmpeg, so there is
no stock footage or music to license. Prints the same key: value lines as generate_video.py so the workflow can
upload the result (plus thumb: and chapters in the description).

  python3 scripts/generate_long.py [--mode auto|news|tax] [--out build/long]
  FAKE_TTS=1 uses silence instead of the network voice (layout testing only).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trending  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LONG_TAX = ROOT / "content" / "long_tax.json"
W, H = 1920, 1080
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
FONT_REG_CANDIDATES = [p.replace("-Bold", "") for p in FONT_CANDIDATES]
GOLD, WHITE, SOFT = (255, 205, 90), (255, 255, 255), (214, 226, 245)
PALETTES = [((9, 30, 66), (24, 82, 160)), ((12, 40, 52), (18, 110, 120)), ((40, 20, 70), (98, 52, 156)),
            ((60, 24, 24), (150, 52, 52)), ((18, 38, 30), (44, 112, 76)), ((30, 30, 52), (74, 84, 140))]
VOICES = ["en-US-GuyNeural", "en-US-AriaNeural"]
PAUSE = 0.8
MIN_NEWS = 5
MAX_NEWS = 8


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for p in (FONT_CANDIDATES if bold else FONT_REG_CANDIDATES):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    raise SystemExit("DejaVu fonts missing: apt-get install fonts-dejavu-core")


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        t = f"{cur} {word}".strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _gradient(c1, c2) -> Image.Image:
    im = Image.new("RGB", (W, H), c1)
    px = ImageDraw.Draw(im)
    for y in range(H):
        t = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    return im


def slide(head: str, body: str, tag: str, idx: int, out: Path, big: bool = False) -> Path:
    c1, c2 = PALETTES[idx % len(PALETTES)]
    im = _gradient(c1, c2)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 96], fill=(0, 0, 0))
    d.text((70, 24), "TaxSaveUSA", font=_font(46), fill=GOLD)
    tf = _font(34)
    d.text((W - 70 - d.textlength(tag.upper(), font=tf), 31), tag.upper(), font=tf, fill=SOFT)
    margin = 130
    hs = 104 if big else 84
    while hs > 52:
        hf = _font(hs)
        hl = _wrap(d, head, hf, W - 2 * margin)
        if len(hl) <= 3:
            break
        hs -= 6
    y = 190 if body else 330
    for ln in hl:
        d.text((margin, y), ln, font=hf, fill=WHITE, stroke_width=2, stroke_fill=(0, 0, 0))
        y += int(hs * 1.22)
    if body:
        y += 26
        d.rectangle([margin, y, margin + 220, y + 8], fill=GOLD)
        y += 46
        bs = 46
        while bs > 30:
            bf = _font(bs, bold=False)
            bl = _wrap(d, body, bf, W - 2 * margin)
            if y + len(bl) * int(bs * 1.42) <= H - 90:
                break
            bs -= 2
        for ln in bl:
            d.text((margin, y), ln, font=bf, fill=SOFT)
            y += int(bs * 1.42)
    im.save(out)
    return out


def thumbnail(line1: str, line2: str, kicker: str, idx: int, out: Path) -> Path:
    c1, c2 = PALETTES[idx % len(PALETTES)]
    im = _gradient(c1, c2).resize((1280, 720))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1280, 20], fill=GOLD)
    kf = _font(54)
    pill_w = d.textlength(kicker, font=kf) + 70
    d.rounded_rectangle([60, 70, 60 + pill_w, 150], radius=40, fill=GOLD)
    d.text((95, 80), kicker, font=kf, fill=(20, 20, 20))
    for i, (txt, col) in enumerate(((line1, WHITE), (line2, GOLD))):
        size = 190
        while size > 70 and d.textlength(txt, font=_font(size)) > 1160:
            size -= 8
        d.text((60, 190 + i * 215), txt, font=_font(size), fill=col, stroke_width=5, stroke_fill=(0, 0, 0))
    d.text((60, 640), "TaxSaveUSA", font=_font(44), fill=SOFT)
    im.save(out, "PNG")
    return out


# ------------------------------------------------------------------ audio
def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def speak(text: str, voice: str, out: Path) -> Path:
    import os

    if os.environ.get("FAKE_TTS") == "1":
        secs = max(2.0, len(text.split()) / 2.6)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{secs:.2f}",
                        "-c:a", "libmp3lame", str(out)], check=True)
        return out
    import edge_tts

    last = None
    for v in (voice, VOICES[0], VOICES[1]):
        for _ in range(2):
            try:
                asyncio.run(edge_tts.Communicate(text, v, rate="-3%").save(str(out)))
                if out.exists() and out.stat().st_size > 1000:
                    return out
            except Exception as exc:  # noqa: BLE001
                last = exc
    print(f"::error::narration failed: {last}")
    raise SystemExit(f"narration failed: {last}")


def silence(path: Path, secs: float) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{secs:.2f}",
                    "-c:a", "libmp3lame", str(path)], check=True)
    return path


def clip(image: Path, seconds: float, out: Path) -> Path:
    frames = int(seconds * 24)
    vf = (f"scale=2400:-1,zoompan=z='min(1+0.0003*on,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps=24,"
          f"fade=t=in:st=0:d=0.5,fade=t=out:st={max(seconds - 0.5, 0.1):.2f}:d=0.5,format=yuv420p")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(image), "-vf", vf, "-t", f"{seconds:.2f}",
                    "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(out)], check=True)
    return out


# ---------------------------------------------------------------- content
def _ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def tax_plan(rng) -> dict | None:
    data = json.loads(LONG_TAX.read_text(encoding="utf-8"))
    pending = [v for v in data["videos"] if not v.get("used")]
    if not pending:
        return None
    v = pending[0]
    secs = [{"head": v["title"], "body": "General tax information, not personal advice.", "say": v["intro"], "chapter": "Introduction", "big": True}]
    for i, p in enumerate(v["points"], 1):
        secs.append({"head": f"{i}. {p['head']}", "body": "", "say": p["text"], "chapter": p["head"], "caption": p["text"]})
    secs.append({"head": "Subscribe to TaxSaveUSA", "body": "New tax and money tips every day.", "say": v["outro"], "chapter": "Recap", "big": True})
    n = len(v["points"])
    desc = (f"{v['title']}\n\n{v['intro']}\n\nIn this video:\n")
    return {"kind": "tax", "slug": v["slug"], "title": v["title"], "sections": secs, "tags": v["tags"], "playlist": v["playlist"],
            "thumb": (v["thumb_line1"], v["thumb_line2"], v["hook"]), "desc_head": desc, "mark_used": v["slug"],
            "footer": "Sources: general guidance published by the IRS (irs.gov). Rules, limits and dates change every year, so check irs.gov or ask a tax professional before you file. This video is general information, not tax advice.",
            "hashtags": "#Taxes #IRS #TaxTips #Money #TaxSaveUSA", "n": n}


# long video formats: categories used, title, playlist, thumbnail, tags, hashtags, window (hours), min/max stories
LONG_KINDS = {
    "news": dict(cats=["trending", "news", "money", "tech", "science"], title="Top {n} Trending US Stories Today | {nice}",
                 intro="Here are the top {n} trending stories in the United States today, {nice}.",
                 playlist="Trending Now in the US", thumb=("TRENDING IN", "THE USA TODAY"), kicker="{nice}",
                 tags=["trending news", "us news today", "news today", "trending in usa", "daily news roundup", "usa news", "top stories", "headlines today", "news summary"],
                 hashtags="#TrendingNow #USNews #NewsToday #Headlines #TaxSaveUSA", hours=36, lo=5, hi=8, wrap="daily US trending news and tax tips"),
    "tech": dict(cats=["tech", "gadgets"], title="Top {n} Tech and Gadget Stories This Week | {nice}",
                 intro="Here are the top {n} tech and gadget stories this week, {nice}.",
                 playlist="Tech and Gadgets Weekly", thumb=("TECH & GADGETS", "THIS WEEK"), kicker="{nice}",
                 tags=["tech news", "gadgets", "new gadgets 2026", "tech news this week", "technology news", "ai news", "smartphones", "tech roundup", "usa tech news"],
                 hashtags="#TechNews #Gadgets #Technology #AI #TaxSaveUSA", hours=168, lo=5, hi=8, wrap="weekly tech and gadget news"),
    "science": dict(cats=["science"], title="Top {n} Science and Space Stories This Week | {nice}",
                    intro="Here are the top {n} science and space stories this week, {nice}.",
                    playlist="Science and Space Weekly", thumb=("SCIENCE & SPACE", "THIS WEEK"), kicker="{nice}",
                    tags=["science news", "space news", "nasa", "science this week", "discoveries", "science explained", "space exploration", "science roundup", "usa science news"],
                    hashtags="#Science #Space #NASA #Discovery #TaxSaveUSA", hours=168, lo=4, hi=7, wrap="weekly science and space news"),
    "ahead": dict(cats=["upcoming"], title="The Week Ahead: {n} US Dates You Need to Know | {nice}",
                  intro="Here are {n} important dates coming up in the United States, as of {nice}.",
                  playlist="Coming Up: Dates to Know", thumb=("DATES YOU", "MUST KNOW"), kicker="COMING UP",
                  tags=["important dates", "week ahead", "upcoming dates", "tax deadlines", "fed meeting", "open enrollment", "money dates", "what's coming", "usa dates"],
                  hashtags="#WeekAhead #TaxDeadlines #Money #TaxSaveUSA", hours=36, horizon=75, lo=3, hi=8, wrap="upcoming deadlines and dates that matter"),
}
WEEKDAY_KIND = {0: "ahead", 1: "tax", 2: "tech", 3: "science", 4: "news", 5: "tax", 6: "news"}  # Mon..Sun


def news_plan(rng, kind: str = "news") -> dict:
    cfg = LONG_KINDS[kind]
    now = datetime.now(timezone.utc)
    st = trending.load_state()
    order = list(cfg["cats"])
    rng.shuffle(order)
    trending.MAX_AGE_HOURS = cfg["hours"]
    trending.UPCOMING_HORIZON = cfg.get("horizon", 21)
    MIN_NEWS, MAX_NEWS = cfg["lo"], cfg["hi"]
    picked, per_cat = [], {}
    for rnd in range(max(2, -(-MAX_NEWS // len(order)))):   # pass 1: one story per category; later passes fill up
        for category in order:
            if len(picked) >= MAX_NEWS or per_cat.get(category, 0) > rnd:
                continue
            cat = next(c for c in trending.CATEGORIES if c[0] == category)
            for headline, core, link, publisher in trending._candidates_for(category, now):
                if trending._is_dup(headline, st["seen"]) or trending._is_dup(headline, [{"key": "", "tokens": sorted(trending._tokens(p["headline"]))} for p in picked]):
                    continue
                text = core[len(cat[3]):].strip() if core.startswith(cat[3]) else core
                picked.append({"category": category, "label": cat[1], "headline": headline, "say": text, "link": link, "publisher": publisher})
                per_cat[category] = per_cat.get(category, 0) + 1
                break
    if len(picked) < MIN_NEWS:
        print(f"::error::only {len(picked)} safe stories found")
        raise SystemExit(f"only {len(picked)} safe stories found (need {MIN_NEWS}); try again later")
    for p in picked:
        st["seen"].append({"key": __import__("hashlib").sha1(trending._norm(p["headline"]).encode()).hexdigest()[:12], "tokens": sorted(trending._tokens(p["headline"])),
                           "t": now.isoformat(), "category": p["category"]})
    trending.save_state(st)
    today = date.today()
    nice = f"{today.strftime('%B')} {today.day}, {today.year}"
    n = len(picked)
    secs = [{"head": cfg["title"].split(" |")[0].format(n=n, nice=nice), "body": nice, "say": cfg["intro"].format(n=n, nice=nice) + " Every story is credited to the source that reported it, with links in the description.", "chapter": "Intro", "big": True}]
    for i, p in enumerate(picked, 1):
        secs.append({"head": p["headline"], "body": "", "tag": p["label"], "say": f"Story number {i}. {p['say']}", "chapter": p["headline"][:60], "caption": p["say"]})
    secs.append({"head": "Subscribe to TaxSaveUSA", "body": cfg["wrap"].capitalize() + ".", "say": f"That is the roundup. Subscribe to TaxSaveUSA for {cfg['wrap']}, and tell us in the comments which story surprised you most.", "chapter": "Wrap up", "big": True})
    src = "\n".join(f"- {p['headline']} ({p['publisher']}){': ' + p['link'] if p['link'] else ''}" for p in picked)
    return {"kind": "news", "slug": f"{kind}-{today.isoformat()}", "title": cfg["title"].format(n=n, nice=nice),
            "sections": secs, "tags": cfg["tags"], "playlist": cfg["playlist"],
            "thumb": (*cfg["thumb"], cfg["kicker"].format(nice=nice.upper())),
            "desc_head": f"{cfg['intro'].format(n=n, nice=nice)} Summarized in plain English.\n\nStories and sources:\n{src}\n\nChapters:\n",
            "footer": "Each story is a short summary of a report by the source named above. Open the link for the full report; details can change as stories develop. General information only, not financial, tax or legal advice.",
            "hashtags": cfg["hashtags"], "n": n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "news", "tax", "tech", "science", "ahead"], default="auto")
    ap.add_argument("--out", default=str(ROOT / "build" / "long"))
    ap.add_argument("--keep-queue", action="store_true", help="do not mark the tax video as used (test runs)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    today = date.today()
    rng = random.Random(today.isoformat())
    plan = None
    mode = args.mode
    if mode == "auto":
        mode = WEEKDAY_KIND[today.weekday()]
    if mode == "tax":
        plan = tax_plan(rng)
        if plan is None and args.mode == "tax":
            raise SystemExit("no unused tax explainer left in content/long_tax.json")
    if plan is None:
        kind = mode if mode in LONG_KINDS else "news"
        try:
            plan = news_plan(rng, kind)
        except SystemExit as exc:
            if args.mode not in ("auto", "tax"):
                raise
            print(f"{kind} roundup unavailable ({exc}); trying the others")
            for alt in ["news", "tech", "ahead"]:
                if alt == kind:
                    continue
                try:
                    plan = news_plan(rng, alt)
                    break
                except SystemExit:
                    continue
            if plan is None:
                plan = tax_plan(rng)
            if plan is None:
                raise SystemExit("no content available for the long video")

    voice = VOICES[today.toordinal() % 2]
    clips, voice_parts, chapters, t = [], [], [], 0.0
    gap = silence(out / "gap.mp3", PAUSE)
    for i, s in enumerate(plan["sections"]):
        a = speak(s["say"], voice, out / f"v{i}.mp3")
        secs = duration(a) + PAUSE
        img = slide(s["head"], s.get("caption") if s.get("caption") and plan["kind"] == "tax" and False else s.get("body", ""),
                    s.get("tag", "Tax Tips" if plan["kind"] == "tax" else "Trending"), i, out / f"s{i}.png", big=s.get("big", False))
        if s.get("caption") and plan["kind"] == "news":
            img = slide(s["head"], s["caption"], s.get("tag", "Trending"), i, out / f"s{i}.png")
        elif s.get("caption") and plan["kind"] == "tax":
            img = slide(s["head"], s["caption"], "Tax Tips", i, out / f"s{i}.png")
        clips.append(clip(img, secs, out / f"c{i}.mp4"))
        voice_parts += [a, gap]
        chapters.append((t, s["chapter"]))
        t += secs
    (out / "clips.txt").write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    (out / "voice.txt").write_text("".join(f"file '{p.resolve()}'\n" for p in voice_parts), encoding="utf-8")
    final = out / f"{plan['slug']}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(out / "clips.txt"), "-f", "concat", "-safe", "0",
                    "-i", str(out / "voice.txt"), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
                    "-shortest", "-movflags", "+faststart", str(final)], check=True)
    thumb = thumbnail(*plan["thumb"][:2], plan["thumb"][2], rng.randint(0, 5), out / "thumb.png")

    desc = plan["desc_head"] + "\n".join(f"{_ts(ts)} {label}" for ts, label in chapters)
    if plan["kind"] == "tax":
        desc = plan["desc_head"].split("In this video:")[0] + "Chapters:\n" + "\n".join(f"{_ts(ts)} {label}" for ts, label in chapters)
    import monetization
    desc += f"\n\n{plan['footer']}"
    if monetization.footer():
        desc += f"\n\n{monetization.footer()}"
    desc += f"\n\nSubscribe to TaxSaveUSA: https://youtube.com/@TaxSaveUSA\n\n{plan['hashtags']}"
    if plan.get("mark_used") and not args.keep_queue:
        data = json.loads(LONG_TAX.read_text(encoding="utf-8"))
        for v in data["videos"]:
            if v["slug"] == plan["mark_used"]:
                v["used"] = True
                v["used_date"] = today.isoformat()
        LONG_TAX.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"diag: kind={plan['kind']} sections={len(plan['sections'])} duration={t:.0f}s")
    print(f"rendered: {final}")
    print(f"title: {plan['title'][:100]}")
    print(f"description: {desc.replace(chr(10), chr(92) + 'n')}")
    print(f"tags: {','.join(plan['tags'])}")
    print(f"playlist: {plan['playlist']}")
    print(f"thumb: {thumb}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - surface the reason as a GitHub annotation (run logs are not downloadable)
        import traceback

        print(f"::error::generate_long failed: {type(exc).__name__}: {exc} | {traceback.format_exc()[-400:].replace(chr(10), ' | ')}")
        sys.exit(1)
