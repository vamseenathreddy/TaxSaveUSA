#!/usr/bin/env python3
"""
Pulls today's topic + facts, condenses it to one short, clear message,
generates a TTS voiceover, and renders a vertical (1080x1920) Short
with FFmpeg - target length ~20 seconds, one message per video.

Pipeline:
  1. content/topics.json (git) -> today's topic + script text
  2. condense_script() trims the script down to its lead sentence(s)
     (the ones carrying the actual number/fact) within CORE_SCRIPT_WORD
     _BUDGET words, so the final video stays around ~20s
  3. edge-tts -> audio/<slug>.mp3 (+ word-boundary timings, used only
     to measure the actual spoken duration for the caption/-t cap)
  4. The full condensed script + outro CTA is burned in as ONE static
     caption for the entire video - not a scrolling/flashing
     word-by-word track - so the whole message is visible together and
     never moves
  5. FFmpeg -> background clip + static caption + hook headline +
     audio -> output/<slug>.mp4
"""
import argparse
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

# Target ~20-second Shorts: the full script is condensed down to just
# its lead sentence(s) (the ones with the actual number/fact - these
# topic scripts are written "lead with the number" style, so the first
# 1-2 sentences already carry the core message) within this many words,
# before the outro CTA is appended. ~2.4 words/sec is a rough estimate
# for edge-tts's "-3%" rate English speech, so budget*~0.42s plus the
# outro's own ~3s keeps the final video comfortably under 20s - watch
# actual render durations (ffprobe on build/audio/*.mp3) and tune this
# down further if they're running long.
CORE_SCRIPT_WORD_BUDGET = 34


# Matches a number that reads as an actual dollar figure/limit rather
# than an incidental small number (an age, a year, a percentage): a
# comma-grouped amount (24,500), a 2-decimal amount (202.90), or a bare
# 3+ digit number immediately followed by "dollar(s)" (750 dollars).
# Deliberately does NOT match a bare "2026" or "50" - those are
# incidental context, not the headline figure.
# Matches the sentence carrying the video's actual headline number, so
# condense_script() (below) never drops it. Originally dollar-figures
# only (comma-grouped thousands, 2-decimal cents, "N dollars"), but that
# missed topics whose headline fact is a PERCENTAGE instead of a dollar
# amount - e.g. "the penalty is 25 percent of the amount you should have
# withdrawn" (rmd-age-73-rules-2026) got condensed down to two sentences
# that never once mention the 25% penalty the video's own title promises.
# Percentage alternative added to close that gap: "N percent"/"N%".
_MONEY_RE = re.compile(
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"
    r"|\b\d+\.\d{2}\b"
    r"|\b\d{3,}\s*dollars?\b"
    r"|\b\d{1,3}(?:\.\d+)?\s*(?:%|percent)\b",
    re.IGNORECASE,
)


def condense_script(script_text: str, word_budget: int = CORE_SCRIPT_WORD_BUDGET) -> str:
    """Condense down to a short, clear message instead of reading the
    full script - but length is never allowed to cost the actual fact.
    These scripts are usually structured as a hook sentence, then the
    headline dollar figure, then secondary/bonus details that often
    mention even bigger cumulative numbers (e.g. a 50+ or 60-63 catch-up
    total) - so "keep whichever sentence has the most digits" actually
    picks a later, narrower bonus fact instead of the main headline
    number, and "keep adding sentences until the budget runs out" can
    use the whole budget on the hook/teaser and cut off right before
    any number is said. Instead this always keeps (a) the first
    sentence (the hook) and (b) the FIRST sentence (in original order)
    that contains a real dollar-figure-shaped number per _MONEY_RE -
    i.e. the headline number, not just the one with the most digits -
    even if that pair alone goes over word_budget, and only then fills
    in any other sentences, in their original order, while there's
    budget left."""
    sentences = re.split(r"(?<=[.?!])\s+", script_text.strip())
    if not sentences:
        return script_text.strip()

    key_idx = next((i for i, s in enumerate(sentences) if _MONEY_RE.search(s)), None)
    must_keep = {0}
    if key_idx is not None:
        must_keep.add(key_idx)

    selected = set(must_keep)
    count = sum(len(sentences[i].split()) for i in selected)
    for i, s in enumerate(sentences):
        if i in selected:
            continue
        wc = len(s.split())
        if count + wc <= word_budget:
            selected.add(i)
            count += wc

    return " ".join(sentences[i] for i in sorted(selected))


def probe_audio_duration(audio_path: Path) -> float:
    """Exact duration (seconds) of a rendered audio file via ffprobe -
    the one source of truth for how long the video actually is, used
    both to cap the ffmpeg render (render_video()) and, critically, to
    size the caption's on-screen duration (main()) instead of trusting
    edge-tts's own WordBoundary events for that - see the comment on
    caption_end in main() for why that distinction matters."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(probe.stdout.strip())


def compute_sentence_end_times(script_text: str, word_boundaries: list, audio_duration: float) -> list:
    """Maps each sentence of script_text to the timestamp (seconds) it
    ends at - used to pop an emoji in right as each sentence finishes
    being spoken (see render_video()'s sentence_end_times param),
    instead of a fixed burst near the start regardless of what's
    actually being said.

    Prefers edge-tts's own word_boundaries (walking a running word-count
    through them, since edge-tts's tokenization of contractions/
    punctuation doesn't always split identically to a plain str.split()
    on the sentence text - counts are far more robust than trying to
    align the words themselves). But word_boundaries is a stream of
    events from a live TTS connection, and has been observed to come
    back materially short of the actual spoken audio (its last
    timestamp far earlier than audio_duration) without the synthesis
    itself failing - if that's detected here, this falls back to a
    simple proportional split of audio_duration by each sentence's word
    count instead, so emoji timing degrades gracefully to "roughly
    right" instead of all clustering in the first second or two of a
    20-second video."""
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", script_text.strip()) if s.strip()]
    if not sentences:
        return []

    word_boundaries_look_complete = bool(word_boundaries) and word_boundaries[-1]["end"] >= audio_duration * 0.75
    if word_boundaries_look_complete:
        times = []
        word_idx = 0
        for s in sentences:
            word_idx += len(s.split())
            boundary_idx = min(word_idx, len(word_boundaries)) - 1
            if boundary_idx < 0:
                continue
            times.append(word_boundaries[boundary_idx]["end"])
        return times

    total_words = sum(len(s.split()) for s in sentences) or 1
    times = []
    word_idx = 0
    for s in sentences:
        word_idx += len(s.split())
        times.append(audio_duration * word_idx / total_words)
    return times


# Base tags applied to every video, on top of each topic's own tags -
# see content/topics.json "tags" field. Keep broad + niche mixed for
# reach: a few very high-volume broad terms so the video has a shot at
# competitive searches, the existing finance/tax niche terms so it
# ranks well for people actually looking for this content, and explicit
# "shorts"-family tags since YouTube's Shorts feed itself is a huge,
# separate discovery surface from regular search/suggested.
BASE_TAGS = [
    "personal finance", "money tips", "tax tips 2026", "save money",
    "financial freedom", "USA taxes", "tax season", "IRS", "money hacks",
    "finance tips for beginners", "shorts", "youtube shorts",
    "money tips 2026", "financial education", "taxes explained",
]

# YouTube only auto-routes a video into the Shorts feed/shelf for
# API-uploaded videos (as opposed to ones recorded in the mobile Shorts
# camera) when it is <=3 min, vertical, AND the title or description
# contains the literal #Shorts hashtag - this isn't optional metadata,
# it's the actual discovery-surface switch, so it's applied to every
# video rather than left to each topic entry in topics.json.
SHORTS_HASHTAG = "#Shorts"


def _with_shorts_tag_in_title(title: str) -> str:
    """Appends ' #Shorts' to the title if there's room under YouTube's
    100-char title limit and it isn't already present, since having the
    tag in BOTH the title and description costs nothing and only helps
    Shorts-shelf eligibility."""
    if SHORTS_HASHTAG.lower() in title.lower():
        return title
    candidate = f"{title} {SHORTS_HASHTAG}"
    return candidate if len(candidate) <= 100 else title


def _with_shorts_tag_in_description(description: str) -> str:
    """Prepends #Shorts as its own line at the very top of the
    description. YouTube shows a description's first ~3 hashtags as
    clickable chips above the title, so leading with it (rather than
    appending it after the topic's own hashtag line) maximizes the
    odds it's one of the ones that actually get surfaced/counted."""
    if SHORTS_HASHTAG.lower() in description.lower():
        return description
    return f"{SHORTS_HASHTAG}\n\n{description}"

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


# NOTE: not used by the current default pipeline (see main(), which now
# writes one single static caption event covering the whole condensed
# script+outro - "all text on one page, nothing moves"). Kept here in
# case a sentence-by-sentence caption style is wanted again later.
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


# Caption band: a fixed, non-scrolling zone sized to hold the ENTIRE
# condensed script + outro CTA as ONE static block for the whole video -
# "all text on one page" that "doesn't move." Positioned in the clear
# middle of the frame: below the intro hook headline/emoji burst
# (y~140-540, only active for the first ~1.7s) and well above where
# YouTube/Instagram Shorts/Reels' own on-screen UI (profile pic,
# like/comment/share buttons, caption toggle, progress bar) typically
# covers the BOTTOM of a vertical video. A smaller font than the old
# per-chunk captions so ~30-45 words of text wraps to fit the band
# without overflowing.
CAPTION_BAND_TOP = 580
CAPTION_BAND_HEIGHT = 760  # taller band to fit the larger font below
CAPTION_MARGIN_V = 600  # distance from the top edge to the caption text block
CAPTION_FONTSIZE = 54  # bumped up from 42 for readability (user: "expand text size")

# Background footage (the mp4 clip itself, not the caption/text) is
# dimmed by this much - eq's brightness is additive, so -0.30 reads as
# roughly 30% darker overall. Pushed past the original 10-25% ask because
# the user wants the viewer's attention OFF the background video entirely
# and fully on the caption text; combined with a mild blur below so the
# footage reads as ambient motion, not content competing with the text.
BACKGROUND_BRIGHTNESS_ADJUST = -0.30

# Mild Gaussian blur applied to the background footage itself (the
# "boxblur" ffmpeg filter, luma_radius:luma_power) so it no longer reads
# as something to watch/focus on - just soft, out-of-focus motion behind
# the text. This is separate from the caption text's own outline below.
BACKGROUND_BOXBLUR = "6:1"

# Caption text style: plain bold white with a thick black outline and a
# soft shadow - no colored (gold) outline/glow, which the user felt
# looked bad. Clean, high-legibility "movie subtitle" look instead, sized
# up to match the larger CAPTION_FONTSIZE.
CAPTION_OUTLINE_WIDTH = 5
CAPTION_SHADOW = 2

# Both libass (the subtitles= ffmpeg filter, used for the burned-in
# caption) and the drawtext filter (hook headline + subscribe banner)
# need an ACTUAL font to draw glyphs with. Neither was ever given one
# explicitly - the ASS style just named the family "Arial" and drawtext
# had no "font"/"fontfile" at all - which silently works on a machine
# that happens to have a matching font installed via fontconfig (true of
# this sandbox) but silently renders NO TEXT AT ALL, with no error, on
# one that doesn't (true of the bare "apt-get install ffmpeg" GitHub
# Actions runner this pipeline actually publishes from - it was never
# told to install any font package). This is the confirmed cause of
# published Shorts showing their background/caption band/boxes but no
# caption text: the render "succeeded" (ffmpeg doesn't treat a missing
# font as fatal), it just drew nothing. Resolving an explicit, bundled
# font file - rather than hoping a system font happens to be present -
# fixes this for good and fails loudly (not silently) if it's ever
# missing again.
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu Sans"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "Liberation Sans"),
]


def resolve_caption_font() -> tuple[Path, str]:
    """Returns (font_file_path, font_family_name) for the first bold
    sans-serif font found on disk, checked in order of preference. Raises
    SystemExit with a clear, actionable message if neither is present,
    instead of letting the render "succeed" with blank text - see the
    comment above _FONT_CANDIDATES for why that's the real failure mode
    being guarded against here."""
    for path_str, family in _FONT_CANDIDATES:
        path = Path(path_str)
        if path.exists():
            return path, family
    raise SystemExit(
        "No usable bold sans-serif font found on disk (checked: "
        + ", ".join(p for p, _ in _FONT_CANDIDATES)
        + "). Without one, ffmpeg renders the caption text, hook "
        "headline, and subscribe banner as BLANK instead of failing - "
        "install fonts-dejavu-core (or fonts-liberation) before "
        "rendering."
    )


def write_ass_subtitles(events, ass_path: Path, font_family: str, video_w=1080, video_h=1920):
    """Burned-in captions: bold white text with a thick black outline and
    a soft drop shadow (BorderStyle=1, no colored glow) on top of the
    dark highlight band drawn separately in render_video(), pinned to a
    fixed top-of-screen position (Alignment=8, top-center) so the text
    never moves/scrolls and never ends up hidden behind a platform's
    bottom-screen UI chrome. Full sentences (see build_caption_events)
    auto-wrap across up to a few lines within this style's margins.
    font_family must be a family name actually resolvable to an
    installed font file (see resolve_caption_font()/fontsdir on the
    subtitles= filter in render_video()) - libass silently draws nothing
    for a family it can't match, rather than erroring."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_family},{CAPTION_FONTSIZE},&H00FFFFFF,&H000000FF,&H00000000,&H40000000,-1,0,0,0,100,100,0,0,1,{CAPTION_OUTLINE_WIDTH},{CAPTION_SHADOW},8,60,60,{CAPTION_MARGIN_V},1

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
                  out_path: Path, subscribe_text: str, font_path: Path, sentence_end_times=()):
    """
    Vertical 1080x1920 render: loops/crops the background clip to
    length, burns in the word-synced captions, and opens with a
    "blast" attention-grab: a quick white flash-cut, a short
    synthesized attention tone under the audio, a bold high-contrast
    hook headline, and color emoji (party popper, fireworks, sparkles,
    collision, fire) popping in one-per-sentence, each timed to the end
    of the sentence it "punctuates" (see sentence_end_times) - rather
    than all firing in a fixed burst near the start regardless of what's
    actually being said - before settling into the normal captioned
    voiceover.

    In the last SUBSCRIBE_WINDOW_SECONDS of the video, a second banner
    (same position/box style as the opening hook, so no new on-screen
    real estate is introduced) fades in asking the viewer to subscribe.
    It's a plain drawtext overlay - independent of the spoken outro CTA
    and the .ass word captions - so it shows up purely on screen without
    being read aloud or duplicated in the caption track.

    font_path is the resolved on-disk font file from
    resolve_caption_font() - passed in explicitly (rather than looked up
    again here) so the caller only has to resolve it once and the whole
    render fails fast, before any ffmpeg work starts, if no font is
    installed. It's used two ways: directly as drawtext's fontfile= for
    the hook/subscribe banners, and as fontsdir= on the subtitles=
    filter so libass loads the caption font from this exact file instead
    of searching the system's fontconfig database (which is what
    silently produced blank caption text in CI - see the comment on
    _FONT_CANDIDATES above).

    sentence_end_times is a list of timestamps (seconds into the
    voiceover), one per sentence of the spoken script, from
    compute_sentence_end_times() - emoji[i] pops in right as
    sentence_end_times[i] is reached, one emoji per sentence, in order.
    If there are more sentences than emoji icons the extra sentences
    just don't get one; if there are fewer, the unused icons simply
    don't appear that video - no icon is ever shown without a sentence
    actually ending at that moment.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
    hook_escaped = hook_text.replace("'", "\u2019").replace(":", "\\:")
    subscribe_escaped = subscribe_text.replace("'", "\u2019").replace(":", "\\:")
    font_path_escaped = str(font_path).replace("\\", "/").replace(":", "\\:")
    font_dir_escaped = str(font_path.parent).replace("\\", "/").replace(":", "\\:")

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
    # fontfile= points drawtext straight at the resolved font file
    # instead of relying on fontconfig to find "a" font on its own -
    # without it, drawtext silently draws nothing (see font_path's
    # docstring note in render_video() above).
    hook_drawtext = (
        f"drawtext=fontfile='{font_path_escaped}':text='{hook_escaped}':fontcolor=yellow:fontsize=68:"
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
        f"drawtext=fontfile='{font_path_escaped}':text='{subscribe_escaped}':fontcolor=white:fontsize=56:"
        f"bordercolor=black:borderw=4:"
        f"box=1:boxcolor=blue@0.75:boxborderw=22:x=(w-text_w)/2:y=140:"
        f"enable='between(t,{sub_start:.2f},{sub_end:.2f})'"
    )
    # A single white flash frame at t=0 - a classic pattern-interrupt
    # cut used to stop the scroll before the eye even reads the hook.
    flash_drawbox = "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=fill:enable='lt(t,0.08)'"

    # De-emphasize the background footage (not the text/overlays) so the
    # viewer's attention stays on the caption text, not on what's playing
    # in the clip: dimmed (~30% darker, eq's brightness is additive) AND
    # mildly blurred (boxblur) so it reads as soft ambient motion behind
    # the text rather than content competing for attention.
    background_dim = f"eq=brightness={BACKGROUND_BRIGHTNESS_ADJUST}"
    background_blur = f"boxblur={BACKGROUND_BOXBLUR}"

    # Full-width translucent "banner" behind the caption zone, drawn for
    # the whole video (no enable clause) - gives every caption line a
    # consistent readable backdrop regardless of what's happening in the
    # background footage underneath, like a fixed semi-transparent PNG
    # overlay. Drawn before the subtitles filter so the text renders on
    # top of it. A thin white accent line along its top edge separates it
    # from the (now blurred/dimmed) footage above without pulling extra
    # attention the way the earlier yellow line did.
    caption_band_drawbox = (
        f"drawbox=x=0:y={CAPTION_BAND_TOP}:w=iw:h={CAPTION_BAND_HEIGHT}:"
        f"color=black@0.6:t=fill"
    )
    caption_band_accent = (
        f"drawbox=x=0:y={CAPTION_BAND_TOP}:w=iw:h=4:color=white@0.6:t=fill"
    )

    base_vf = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"{background_dim},"
        f"{background_blur},"
        f"{caption_band_drawbox},"
        f"{caption_band_accent},"
        f"subtitles='{ass_escaped}':fontsdir='{font_dir_escaped}',"
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

    # One emoji per sentence, timed to that sentence's own end - not a
    # fixed burst at the start regardless of script length/content.
    # Capped to however many (icons, sentence-end-times) pairs actually
    # exist; if there are more sentences than icons the extras just
    # don't get one, and if emoji assets or sentence timing aren't
    # available at all, no emoji overlay is added (falls through to the
    # plain base_vf path below, same as before this feature existed).
    n_emoji = min(len(emoji_paths), len(sentence_end_times)) if have_emoji else 0

    if n_emoji:
        emoji_paths_used = emoji_paths[:n_emoji]
        for p in emoji_paths_used:
            cmd += ["-i", str(p)]
        # Positioned in the gap between the hook headline (y~140-270)
        # and the big static caption band (y=580-1340, holds the whole
        # message for the whole video) - so nothing overlaps either.
        positions = [
            (60, 300), (890, 300), (60, 430), (890, 430), (475, 365),
        ]
        filter_parts = [base_vf]
        scale_parts = []
        for i in range(n_emoji):
            scale_parts.append(f"[{3 + i}:v]scale=130:130[e{i}]")
        filter_parts.extend(scale_parts)

        chain_label = "vbase"
        for i in range(n_emoji):
            x, y = positions[i % len(positions)]
            # Pops in right as its sentence finishes (a tiny lead-in so
            # it doesn't feel like it's trailing the word) and holds
            # briefly - 0.9s reads clearly without lingering into the
            # next sentence's own pop.
            start = max(sentence_end_times[i] - 0.1, 0.0)
            end = start + 0.9
            out_label = f"vb{i}" if i < n_emoji - 1 else "vout"
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
    """Merges the topic's own niche tags with BASE_TAGS, topic tags
    first (they're the most specific/relevant to this exact video, so
    they should survive any trimming before the generic broad ones).
    YouTube rejects the whole tags list if the joined string exceeds
    500 characters, so this trims by actual character budget rather
    than just a tag count, leaving a safety margin for the comma
    separators the caller joins with."""
    topic_tags = topic.get("tags", [])
    seen = set()
    merged = []
    for t in topic_tags + BASE_TAGS:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            merged.append(t)

    budget = 460  # stay under YouTube's 500-char hard limit (joined w/ commas)
    selected = []
    used = 0
    for t in merged:
        added = len(t) + (1 if selected else 0)  # +1 for the joining comma
        if used + added > budget:
            continue
        selected.append(t)
        used += added
    return selected


def load_topic_by_slug(slug: str):
    """Looks up one specific topic by slug regardless of its "used"
    flag, and - unlike load_next_topic() - never mutates topics.json.
    Used for re-rendering an already-published topic (e.g. to fix a bug
    and re-upload) without touching the queue's rotation/used-state."""
    with open(TOPICS_PATH, encoding="utf-8-sig") as f:
        data = json.load(f)
    for t in data["topics"]:
        if t["slug"] == slug:
            already_used_count = sum(1 for x in data["topics"] if x.get("used"))
            return t, already_used_count
    raise SystemExit(f"No topic with slug '{slug}' found in content/topics.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slug", default=None,
        help="Re-render this specific (already-used-or-not) topic by slug instead of "
             "popping the next unused one from the queue - doesn't touch topics.json. "
             "Used to fix and re-publish a specific video.",
    )
    args = parser.parse_args()

    # Resolve the caption/hook/subscribe-banner font once, up front, and
    # fail immediately (before any TTS or ffmpeg work) if no usable font
    # is installed - see resolve_caption_font()'s docstring. This is the
    # fix for published Shorts rendering with no visible caption text.
    font_path, font_family = resolve_caption_font()

    if args.slug:
        topic, already_used_count = load_topic_by_slug(args.slug)
    else:
        topic, already_used_count = load_next_topic()
    slug = topic["slug"]
    audio_path = AUDIO_DIR / f"{slug}.mp3"
    ass_path = CAPTIONS_DIR / f"{slug}.ass"
    out_path = OUTPUT_DIR / f"{slug}.mp4"

    rng = random.Random(slug)

    # Condense the script down to one short, clear message (its lead
    # sentence(s), within CORE_SCRIPT_WORD_BUDGET words) instead of the
    # full script, then append a spoken + on-screen outro CTA asking the
    # viewer to comment. Because it's all appended as plain text before
    # TTS, it goes through the exact same pipeline and comes out as BOTH
    # spoken audio and the on-screen caption - no extra rendering path
    # needed. random.seed on the slug keeps the CTA pick deterministic
    # per-video (reruns of the same topic pick the same CTA) while still
    # varying across different topics/videos.
    condensed_script = condense_script(topic["script"])
    outro_cta = rng.choice(OUTRO_CTAS)
    full_script = f"{condensed_script.rstrip()} {outro_cta}"

    # Visual-only "subscribe" banner (see SUBSCRIBE_CTAS / render_video) -
    # drawn from the same per-slug rng right after outro_cta so picks stay
    # deterministic per video without the two lists' choices being coupled.
    subscribe_text = rng.choice(SUBSCRIBE_CTAS)

    word_boundaries = synthesize_voiceover(full_script, audio_path, voice_index=already_used_count)

    # The actual spoken audio length - the one reliable source of truth
    # for how long the video is. Deliberately NOT derived from
    # word_boundaries: edge-tts's WordBoundary events are a stream from
    # a live connection and have been observed to cut off well short of
    # the actual audio (e.g. ending around 1s into a 20s clip) without
    # the synthesis itself failing or raising - a caption end time taken
    # from word_boundaries[-1] in that case makes the caption disappear
    # seconds into the video even though the voiceover keeps playing
    # (this is what the user reported: "text appearing only one
    # second"). Probing the actual rendered audio file instead sidesteps
    # that failure mode entirely.
    audio_duration = probe_audio_duration(audio_path)

    # One single static caption for the ENTIRE video - the whole message
    # on one page, visible the whole time, never replaced/scrolling -
    # instead of word-by-word or sentence-by-sentence chunks. End time is
    # the full probed audio duration (plus a small pad) rather than
    # word_boundaries' last timestamp - see audio_duration's comment
    # above. render_video() separately -t caps the actual ffmpeg render
    # to this same audio file's length, so the two always agree.
    caption_end = audio_duration + 0.4
    events = [{"start": 0.0, "end": caption_end, "text": full_script}]
    write_ass_subtitles(events, ass_path, font_family)

    # One emoji per sentence, each timed to that sentence's own end -
    # see compute_sentence_end_times()/render_video()'s docstring. Also
    # guarded against the same word_boundaries-truncation failure mode
    # via the audio_duration check inside compute_sentence_end_times().
    sentence_end_times = compute_sentence_end_times(full_script, word_boundaries, audio_duration)

    # Diagnostics - not parsed by the workflow (which only greps the
    # title:/description:/tags:/rendered: prefixes below), just useful
    # for spotting a bad render (e.g. word_boundaries way shorter than
    # the script, which would make captions disappear early) from the
    # printed build log without needing to watch the video itself.
    wb_last = word_boundaries[-1]["end"] if word_boundaries else 0.0
    print(f"diag: script_words={len(full_script.split())} word_boundaries={len(word_boundaries)} "
          f"audio_duration={audio_duration:.2f}s word_boundaries_last_end={wb_last:.2f}s "
          f"caption_end={caption_end:.2f}s sentence_end_times={['%.2f' % t for t in sentence_end_times]}")

    background_clip = ASSETS_DIR / "backgrounds" / topic.get("background", "default.mp4")
    if not background_clip.exists():
        raise SystemExit(f"Missing background clip: {background_clip} - pull one into assets/backgrounds/ from Drive.")

    render_video(audio_path, background_clip, ass_path, topic["caption_headline"], out_path, subscribe_text,
                 font_path, sentence_end_times)

    tags = build_tags(topic)
    seo_title = _with_shorts_tag_in_title(topic["title"])
    seo_description = _with_shorts_tag_in_description(topic["description"])
    # Description may contain embedded newlines (e.g. the hashtag line).
    # build_meta.txt is parsed line-by-line downstream (grep '^description:'),
    # which would otherwise silently truncate to just the first line - so
    # newlines are escaped to a literal "\n" here and unescaped again in
    # the workflow's bash step with `printf '%b'` before upload.
    description_escaped = seo_description.replace("\n", "\\n")
    print(f"rendered: {out_path}")
    print(f"title: {seo_title}")
    print(f"description: {description_escaped}")
    print(f"tags: {','.join(tags)}")


if __name__ == "__main__":
    main()
