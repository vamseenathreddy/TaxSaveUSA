#!/usr/bin/env python3
"""
Pulls today's topic + facts, generates a TTS voiceover with word-level
timing, builds synced captions, and renders a vertical (1080x1920)
Short with FFmpeg.

Pipeline:
  1. content/topics.json (git) -> today's topic + script text
  2. edge-tts -> audio/<slug>.mp3 + word-boundary timings (captured
     live during synthesis - same source as the audio, so captions
     are always perfectly in sync, no separate transcription step)
  3. word boundaries -> grouped caption chunks -> .ass subtitle file
  4. FFmpeg -> background clip + burned-in captions + hook headline +
     audio -> output/<slug>.mp4
"""
import json
import re
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = ROOT / "content" / "topics.json"
AUDIO_DIR = ROOT / "build" / "audio"
CAPTIONS_DIR = ROOT / "build" / "captions"
OUTPUT_DIR = ROOT / "build" / "output"
ASSETS_DIR = ROOT / "assets"

# Base tags applied to every video, on top of each topic's own tags -
# see content/topics.json "tags" field. Keep broad + niche mixed for reach.
BASE_TAGS = [
    "personal finance", "money tips", "tax tips 2026", "save money",
    "financial freedom", "USA taxes", "tax season", "IRS", "money hacks",
    "finance tips for beginners",
]

# Small, safe keyword -> emoji map, currently unused (see ENABLE_EMOJI
# below) - the default fonts on the render environment don't have
# emoji glyphs, so enabling this without a proper emoji font installed
# would render as blank boxes instead of emoji.
ENABLE_EMOJI = False
EMOJI_MAP = {
    "tax": "\U0001F4B0", "taxes": "\U0001F4B0", "irs": "\U0001F3DB",
    "401k": "\U0001F4B5", "hsa": "\U0001FA7A", "gift": "\U0001F381",
    "deduction": "\U0001F4C9", "dollars": "\U0001F4B5", "free": "\u2705",
    "money": "\U0001F4B0", "save": "\U0001F4B8",
}


def load_next_topic():
    """Pop the next unused topic from content/topics.json (FIFO queue).
    Also returns how many topics were already used before this one -
    used to alternate voice gender deterministically across publishes."""
    with open(TOPICS_PATH, encoding="utf-8-sig") as f:
        data = json.load(f)
    already_used_count = sum(1 for t in data["topics"] if t.get("used"))
    pending = [t for t in data["topics"] if not t.get("used")]
    if not pending:
        raise SystemExit("No unused topics left in content/topics.json - add more before the next run.")
    topic = pending[0]
    topic["used"] = True
    topic["used_date"] = date.today().isoformat()
    with open(TOPICS_PATH, "w") as f:
        json.dump(data, f, indent=2)
    return topic, already_used_count


def synthesize_voiceover(script_text: str, out_path: Path, voice_index: int):
    """
    edge-tts: free, no API key, no billing account. Uses Microsoft
    Edge's neural voices (same ones behind Edge's "Read Aloud").

    Streams the synthesis manually (instead of the one-line .save()
    helper) so we can capture WordBoundary events as they come back -
    edge-tts reports the exact offset/duration of every spoken word,
    which is what lets the captions match the audio exactly with no
    separate transcription step.

    Returns a list of {"start": seconds, "end": seconds, "text": word}.
    """
    import asyncio
    import edge_tts

    VOICES = ["en-US-GuyNeural", "en-US-AriaNeural"]  # male, female
    VOICE_NAME = VOICES[voice_index % len(VOICES)]

    async def _run():
        communicate = edge_tts.Communicate(script_text, voice=VOICE_NAME, rate="-3%")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        boundaries = []
        with open(out_path, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # edge-tts reports offset/duration in 100-nanosecond units
                    start = chunk["offset"] / 10_000_000
                    dur = chunk["duration"] / 10_000_000
                    boundaries.append({"start": start, "end": start + dur, "text": chunk["text"]})
        return boundaries

    return asyncio.run(_run())


def build_caption_events(word_boundaries, max_words=4, max_chunk_dur=2.2, max_gap=0.5):
    """
    Groups individual timed words into short on-screen caption chunks
    (the fast-paced, few-words-at-a-time style used on Shorts/Reels),
    breaking a chunk early on a long pause (likely a sentence break)
    so captions don't run two separate thoughts together.
    """
    events = []
    current = []

    def flush():
        if not current:
            return
        text = " ".join(w["text"] for w in current)
        # Emoji append is intentionally disabled: tested against the
        # GitHub Actions runner's default fonts (Liberation Sans /
        # unifont) and glyphs like the money-bag emoji fail to render
        # ("Glyph not found, broken font?"), producing a blank/tofu
        # box on the video instead of an emoji. Re-enable by setting
        # ENABLE_EMOJI = True once a proper color-emoji font (e.g.
        # Noto Color Emoji) is installed on the runner and confirmed
        # to render correctly with libass.
        if ENABLE_EMOJI:
            for w in current:
                key = re.sub(r"[^a-z0-9]", "", w["text"].lower())
                if key in EMOJI_MAP:
                    text = f"{text} {EMOJI_MAP[key]}"
                    break
        events.append({"start": current[0]["start"], "end": current[-1]["end"], "text": text})

    for w in word_boundaries:
        if current:
            gap = w["start"] - current[-1]["end"]
            chunk_dur = current[-1]["end"] - current[0]["start"]
            if gap > max_gap or len(current) >= max_words or chunk_dur >= max_chunk_dur:
                flush()
                current = []
        current.append(w)
    flush()
    return events


def _fmt_ass_time(seconds: float) -> str:
    cs = round(seconds * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass_subtitles(events, ass_path: Path, video_w=1080, video_h=1920):
    """Burned-in captions: bold white text, black outline + semi-opaque
    box, lower-third position - the standard readable Shorts caption look."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,4,0,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for ev in events:
            start = _fmt_ass_time(ev["start"])
            end = _fmt_ass_time(ev["end"])
            text = ev["text"].replace("\n", " ")
            f.write(f"Dialogue: 0,{start},{end},Caption,,0,0,0,,{text}\n")


def render_video(audio_path: Path, background_clip: Path, ass_path: Path, hook_text: str, out_path: Path):
    """
    Vertical 1080x1920 render: loops/crops the background clip to
    length, burns in the word-synced captions, and opens with a
    "blast" attention-grab: a quick white flash-cut, a short
    synthesized attention tone under the audio, then a bold
    high-contrast hook headline - a scroll-stopping pattern interrupt
    for the first ~3 seconds, before settling into the normal captioned
    voiceover.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    hook_escaped = hook_text.replace("'", "\u2019").replace(":", "\\:")

    # -shortest does not reliably cut the render when the background is
    # an infinitely looped input (-stream_loop -1) feeding a dual-chain
    # -filter_complex (video chain + audio amix chain) - confirmed by
    # testing: the render just kept going well past the voiceover's
    # actual length instead of stopping. Probing the voiceover's exact
    # duration and passing it as an explicit -t cap is deterministic
    # regardless of filter complexity, so that's used instead below.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    audio_duration = float(probe.stdout.strip())

    # Bold, high-contrast hook: yellow text, black outline, red box -
    # appears right as the flash clears (0.05s) for a punch-in feel.
    hook_drawtext = (
        f"drawtext=text='{hook_escaped}':fontcolor=yellow:fontsize=68:"
        f"bordercolor=black:borderw=5:"
        f"box=1:boxcolor=red@0.75:boxborderw=26:x=(w-text_w)/2:y=140:"
        f"enable='between(t,0.05,3)'"
    )
    # A single white flash frame at t=0 - a classic pattern-interrupt
    # cut used to stop the scroll before the eye even reads the hook.
    flash_drawbox = "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=fill:enable='lt(t,0.08)'"

    vf = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"subtitles='{ass_escaped}',"
        f"{hook_drawtext},"
        f"{flash_drawbox}[vout]"
    )
    # Short synthesized "blip" tone mixed under the very start of the
    # voiceover - an audio pattern-interrupt to match the visual flash.
    # volume=1.8 after amix compensates for amix's default level drop
    # (it averages inputs) so the voice doesn't come out quieter overall.
    af = "[1:a][2:a]amix=inputs=2:duration=first:dropout_transition=0,volume=1.8[aout]"

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(background_clip),
        "-i", str(audio_path),
        "-f", "lavfi", "-i", "sine=frequency=1400:duration=0.18",
        "-filter_complex", f"{vf};{af}",
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{audio_duration:.3f}",
        "-c:v", "libx264", "-c:a", "aac",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def build_tags(topic):
    topic_tags = topic.get("tags", [])
    seen = set()
    merged = []
    for t in topic_tags + BASE_TAGS:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            merged.append(t)
    return merged[:30]  # YouTube allows up to 500 chars total across tags; 30 short tags is a safe ceiling


def main():
    topic, already_used_count = load_next_topic()
    slug = topic["slug"]
    audio_path = AUDIO_DIR / f"{slug}.mp3"
    ass_path = CAPTIONS_DIR / f"{slug}.ass"
    out_path = OUTPUT_DIR / f"{slug}.mp4"

    word_boundaries = synthesize_voiceover(topic["script"], audio_path, voice_index=already_used_count)
    events = build_caption_events(word_boundaries)
    write_ass_subtitles(events, ass_path)

    background_clip = ASSETS_DIR / "backgrounds" / topic.get("background", "default.mp4")
    if not background_clip.exists():
        raise SystemExit(f"Missing background clip: {background_clip} - pull one into assets/backgrounds/ from Drive.")

    render_video(audio_path, background_clip, ass_path, topic["caption_headline"], out_path)

    tags = build_tags(topic)
    # Description may contain embedded newlines (e.g. the hashtag line).
    # build_meta.txt is parsed line-by-line downstream (grep '^description:'),
    # which would otherwise silently truncate to just the first line - so
    # newlines are escaped to a literal "\n" here and unescaped again in
    # the workflow's bash step with `printf '%b'` before upload.
    description_escaped = topic["description"].replace("\n", "\\n")
    print(f"rendered: {out_path}")
    print(f"title: {topic['title']}")
    print(f"description: {description_escaped}")
    print(f"tags: {','.join(tags)}")


if __name__ == "__main__":
    main()
