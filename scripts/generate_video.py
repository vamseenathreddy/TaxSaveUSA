#!/usr/bin/env python3
"""
Pulls today's topic + facts, generates a TTS voiceover, and renders a
vertical (1080x1920) Short with FFmpeg. This is a working stub with
the exact seams to fill in - it will run end-to-end once you drop in
a background clip, producing a real short.

Pipeline:
  1. content/topics.json (git) or a Drive sheet -> today's topic + script text
  2. TTS -> audio/voiceover_<slug>.mp3
  3. FFmpeg -> background clip + captions + audio -> output/<slug>.mp4
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPICS_PATH = ROOT / "content" / "topics.json"
AUDIO_DIR = ROOT / "build" / "audio"
OUTPUT_DIR = ROOT / "build" / "output"
ASSETS_DIR = ROOT / "assets"  # background clips / music pulled from Drive land here


def load_next_topic():
    """Pop the next unused topic from content/topics.json (FIFO queue).
    Also returns how many topics were already used before this one -
    used to alternate voice gender deterministically across publishes."""
    with open(TOPICS_PATH) as f:
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

    pip install edge-tts

    Alternates male/female by voice_index (0=male, 1=female, repeating)
    so consecutive publishes don't sound identical.
    """
    import asyncio
    import edge_tts

    VOICES = ["en-US-GuyNeural", "en-US-AriaNeural"]  # male, female
    VOICE_NAME = VOICES[voice_index % len(VOICES)]

    async def _run():
        communicate = edge_tts.Communicate(script_text, voice=VOICE_NAME, rate="-3%")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await communicate.save(str(out_path))

    asyncio.run(_run())


def render_video(audio_path: Path, background_clip: Path, caption_text: str, out_path: Path):
    """
    Vertical 1080x1920 render: loops/crops the background clip to length,
    overlays captions, muxes in the voiceover track.

    Requires ffmpeg on PATH (apt-get install -y ffmpeg, or use the
    official ffmpeg GitHub Action on the runner).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    drawtext = (
        f"drawtext=text='{caption_text}':fontcolor=white:fontsize=54:"
        f"box=1:boxcolor=black@0.5:boxborderw=20:x=(w-text_w)/2:y=h-400"
    )
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(background_clip),
        "-i", str(audio_path),
        "-vf", f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,{drawtext}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        "-c:v", "libx264", "-c:a", "aac",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def main():
    topic, already_used_count = load_next_topic()
    slug = topic["slug"]
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = AUDIO_DIR / f"{slug}.mp3"
    out_path = OUTPUT_DIR / f"{slug}.mp4"

    synthesize_voiceover(topic["script"], audio_path, voice_index=already_used_count)

    background_clip = ASSETS_DIR / "backgrounds" / topic.get("background", "default.mp4")
    if not background_clip.exists():
        raise SystemExit(f"Missing background clip: {background_clip} - pull one into assets/backgrounds/ from Drive.")

    render_video(audio_path, background_clip, topic["caption_headline"], out_path)
    print(f"rendered: {out_path}")
    print(f"title: {topic['title']}")
    print(f"description: {topic['description']}")


if __name__ == "__main__":
    main()
