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
import random
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
EMOJI_DIR = ASSETS_DIR / "emoji"

# Pre-rendered color emoji PNGs (Twemoji, CC-BY 4.0) used for the intro
# "burst" effect below. ffmpeg's drawtext/libass text-rendering path
# cannot render color/bitmap emoji fonts at all (confirmed: fails with
# "invalid library handle" at arbitrary sizes and "Monocromatic (1bpp)
# fonts are not supported" even at the font's own embedded bitmap size),
# so these are composited as image overlays instead - the standard,
# reliable workaround.
EMOJI_FILES = [
    "party_popper.png", "fireworks.png", "sparkles.png",
    "collision.png", "fire.png",
]

# Spoken + captioned outro CTAs. One is appended (as plain text) to the
# end of every topic's script before TTS synthesis, so it comes out of
# the pipeline as BOTH spoken audio (edge-tts) and an on-screen caption
# (via the existing word-boundary-timed .ass pipeline) automatically -
# no separate rendering path needed. Deliberately distinct from
# content/comment_prompts.json (which is posted as an actual YouTube
# comment) so a viewer doesn't hear/read and then see the exact same
# line twice.
OUTRO_CTAS = [
    "Drop a comment and tell me what you think.",
    "Let me know your thoughts down in the comments.",
    "Comment below and let's talk about it.",
    "Got a question about this? Ask it in the comments.",
    "Tell me in the comments if this helped you.",
    "Share your opinion in the comments below.",
    "What do you think? Comment and let me know.",
    "Drop your thoughts in the comments right now.",
]

# Purely visual (not spoken, not in the .ass captions) "subscribe" banner
# burned in near the end of every video via its own drawtext filter - see
# SUBSCRIBE_WINDOW_SECONDS in render_video(). Kept short so it fits the
# same banner box used for the opening hook. One is picked deterministically
# per-slug (same rng draw as OUTRO_CTAS, see main()) so reruns are stable.
SUBSCRIBE_CTAS = [
    "Subscribe for more tax tips",
    "Follow for daily tax tips",
    "Hit subscribe for more 2026 tips",
    "Subscribe - new tax tip daily",
    "Follow along for more tax tips",
]

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


def build_caption_events(word_boundaries, max_chunk_dur=11.0, max_gap=0.45):
    """
    Groups timed words into FULL-SENTENCE/clause caption blocks instead
    of tiny 3-4-word chunks. Previously captions flashed a few words at
    a time and were immediately replaced, which reads as choppy
    "subtitles coming and going." Now a chunk only breaks where
    edge-tts actually pauses (a gap between words longer than max_gap -
    in practice this lines up with sentence/clause punctuation in the
    script), so the full sentence the voice is currently speaking stays
    on screen together for as long as it's being read, instead of
    disappearing mid-thought. max_chunk_dur is just a safety cap for an
    unusually long run-on sentence with no detectable pause.
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
            if gap > max_gap or chunk_dur >= max_chunk_dur:
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


# Caption band: a fixed, non-scrolling zone pinned near the top of the
# frame, well clear of the intro hook headline (y=140-~270) above it
# and - more importantly - clear of YouTube/Instagram Shorts/Reels'
# own on-screen UI (profile pic, like/comment/share buttons, caption
# toggle, progress bar) which covers a large chunk of the BOTTOM of a
# vertical video on every platform. Captions used to sit in the lower
# third, which is exactly where that UI chrome can cover them. Pinning
# captions to one constant on-screen position for the whole video (no
# per-line repositioning) is what makes them read as "locked" rather
# than drifting/scrolling.
CAPTION_BAND_TOP = 290
CAPTION_BAND_HEIGHT = 380
CAPTION_MARGIN_V = 300  # distance from the top edge to the caption text block
CAPTION_FONTSIZE = 46   # smaller than the old 64 - full-sentence blocks need room to wrap to 2-4 lines


def write_ass_subtitles(events, ass_path: Path, video_w=1080, video_h=1920):
    """Burned-in captions: bold white text, black outline + semi-opaque
    per-line box, pinned to a fixed top-of-screen position (Alignment=8,
    top-center) so the text never moves/scrolls and never ends up
    hidden behind a platform's bottom-screen UI chrome. Full sentences
    (see build_caption_events) auto-wrap across up to a few lines within
    this style's margins."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,{CAPTION_FONTSIZE},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,3,0,8,60,60,{CAPTION_MARGIN_V},1

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


SUBSCRIBE_WINDOW_SECONDS = 4.0  # how long the subscribe banner stays up at the end


def render_video(audio_path: Path, background_clip: Path, ass_path: Path, hook_text: str,
                  out_path: Path, subscribe_text: str):
    """
    Vertical 1080x1920 render: loops/crops the background clip to
    length, burns in the word-synced captions, and opens with a
    "blast" attention-grab: a quick white flash-cut, a short
    synthesized attention tone under the audio, a bold high-contrast
    hook headline, and a staggered burst of color emoji (party popper,
    fireworks, sparkles, collision, fire) popping in around the hook -
    a scroll-stopping pattern interrupt for the first ~2 seconds,
    before settling into the normal captioned voiceover.

    In the last SUBSCRIBE_WINDOW_SECONDS of the video, a second banner
    (same position/box style as the opening hook, so no new on-screen
    real estate is introduced) fades in asking the viewer to subscribe.
    It's a plain drawtext overlay - independent of the spoken outro CTA
    and the .ass word captions - so it shows up purely on screen without
    being read aloud or duplicated in the caption track.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    hook_escaped = hook_text.replace("'", "\u2019").replace(":", "\\:")
    subscribe_escaped = subscribe_text.replace("'", "\u2019").replace(":", "\\:")

    emoji_paths = [EMOJI_DIR / name for name in EMOJI_FILES]
    have_emoji = all(p.exists() for p in emoji_paths)

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
    # Subscribe banner: same slot/box style as the hook (top of frame,
    # clear of the lower-third captions) but blue instead of red so it
    # reads as a distinct prompt, timed to the last SUBSCRIBE_WINDOW_SECONDS
    # of the clip. Clamped so it can never start before the hook has long
    # finished (hook's own enable window ends at t=3).
    sub_start = max(audio_duration - SUBSCRIBE_WINDOW_SECONDS, 3.5)
    sub_end = max(audio_duration - 0.05, sub_start + 0.1)
    subscribe_drawtext = (
        f"drawtext=text='{subscribe_escaped}':fontcolor=white:fontsize=56:"
        f"bordercolor=black:borderw=4:"
        f"box=1:boxcolor=blue@0.75:boxborderw=22:x=(w-text_w)/2:y=140:"
        f"enable='between(t,{sub_start:.2f},{sub_end:.2f})'"
    )
    # A single white flash frame at t=0 - a classic pattern-interrupt
    # cut used to stop the scroll before the eye even reads the hook.
    flash_drawbox = "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=fill:enable='lt(t,0.08)'"

    # Full-width translucent "banner" behind the caption zone, drawn for
    # the whole video (no enable clause) - gives every caption line a
    # consistent readable backdrop regardless of what's happening in the
    # background footage underneath, like a fixed semi-transparent PNG
    # overlay. Drawn before the subtitles filter so the text renders on
    # top of it.
    caption_band_drawbox = (
        f"drawbox=x=0:y={CAPTION_BAND_TOP}:w=iw:h={CAPTION_BAND_HEIGHT}:"
        f"color=black@0.45:t=fill"
    )

    base_vf = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"{caption_band_drawbox},"
        f"subtitles='{ass_escaped}',"
        f"{hook_drawtext},"
        f"{subscribe_drawtext},"
        f"{flash_drawbox}[vbase]"
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
    ]

    if have_emoji:
        # Staggered emoji "burst": each icon pops in a beat after the
        # last. Positioned in the clear middle band of the frame - below
        # the hook headline (y~140-270) AND the taller full-sentence
        # caption band (y=290-670), and well above where Shorts/Reels UI
        # chrome (profile pic, like/comment/share buttons) typically
        # sits near the bottom - so nothing overlaps the locked captions.
        for p in emoji_paths:
            cmd += ["-i", str(p)]
        positions = [
            (70, 720), (850, 720), (70, 1000), (850, 1000), (460, 860),
        ]
        filter_parts = [base_vf]
        scale_parts = []
        for i in range(len(emoji_paths)):
            scale_parts.append(f"[{3 + i}:v]scale=130:130[e{i}]")
        filter_parts.extend(scale_parts)

        chain_label = "vbase"
        for i, (x, y) in enumerate(positions):
            start = 0.1 + i * 0.12
            end = start + 1.3
            out_label = f"vb{i}" if i < len(positions) - 1 else "vout"
            filter_parts.append(
                f"[{chain_label}][e{i}]overlay=x={x}:y={y}:"
                f"enable='between(t,{start:.2f},{end:.2f})'[{out_label}]"
            )
            chain_label = out_label
        vf = ";".join(filter_parts)
    else:
        vf = base_vf.replace("[vbase]", "[vout]")

    cmd += [
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

    # Append a spoken + on-screen outro CTA asking the viewer to comment.
    # Because it's appended to the plain script text before TTS, it goes
    # through the exact same pipeline as the rest of the script and comes
    # out as BOTH spoken audio and a synced on-screen caption - no extra
    # rendering path needed. random.seed on the slug keeps it deterministic
    # per-video (reruns of the same topic pick the same CTA) while still
    # varying across different topics/videos.
    rng = random.Random(slug)
    outro_cta = rng.choice(OUTRO_CTAS)
    full_script = f"{topic['script'].rstrip()} {outro_cta}"

    # Visual-only "subscribe" banner (see SUBSCRIBE_CTAS / render_video) -
    # drawn from the same per-slug rng right after outro_cta so picks stay
    # deterministic per video without the two lists' choices being coupled.
    subscribe_text = rng.choice(SUBSCRIBE_CTAS)

    word_boundaries = synthesize_voiceover(full_script, audio_path, voice_index=already_used_count)
    events = build_caption_events(word_boundaries)
    write_ass_subtitles(events, ass_path)

    background_clip = ASSETS_DIR / "backgrounds" / topic.get("background", "default.mp4")
    if not background_clip.exists():
        raise SystemExit(f"Missing background clip: {background_clip} - pull one into assets/backgrounds/ from Drive.")

    render_video(audio_path, background_clip, ass_path, topic["caption_headline"], out_path, subscribe_text)

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
