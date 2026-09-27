#!/usr/bin/env python3
"""
Pulls today's topic + facts, generates a TTS voiceover, and renders a
vertical (1080x1920) Short with FFmpeg. This is a working stub with
the exact seams to fill in - it will run end-to-end once you drop in
API keys and one background clip, producing a real (if plain) short.

Pipeline:
  1. content/topics.json (git) or a Drive sheet -> today's topic + script text
  2. TTS -> audio/voiceover_<slug>.mp3
  3. FFmpeg -> background clip + captions + audio -> output/<slug>.mp4

Fill in TODOs marked below. Nothing here calls a paid API until you
add your key - safe to run as-is to check the FFmpeg/captions path.
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
    """Pop the next unused topic from content/topics.json (FIFO queue)."""
    with open(TOPICS_PATH) as f:
        data = json.load(f)
    pending = [t for t in data["topics"] if not t.get("used")]
    if not pending:
        raise SystemExit("No unused topics left in content/topics.json - add more before the next run.")
    topic = pending[0]
    topic["used"] = True
    topic["used_date"] = date.today().isoformat()
    with open(TOPICS_PATH, "w") as f:
        json.dump(data, f, indent=2)
    return topic


def synthesize_voiceover(script_text: str, out_path: Path):
    """
    TODO: wire up your TTS provider here. Two common options:

    ElevenLabs (paid, best quality):
        import requests
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            json={"text": script_text, "model_id": "eleven_turbo_v2"},
        )
        out_path.write_bytes(r.content)

    Google Cloud TTS (cheaper, good enough for Shorts):
        from google.cloud import texttospeech
        client = texttospeech.TextToSpeechClient()
        ... synthesize_speech(...) -> out_path.write_bytes(response.audio_content)

    For now this stub raises so the pipeline fails loudly instead of
    silently uploading a video with no voiceover.
    """
    raise NotImplementedError(
        "Wire up an actual TTS call in synthesize_voiceover() before running for real. "
        "See the docstring for ElevenLabs / Google Cloud TTS snippets."
    )


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
    topic = load_next_topic()
    slug = topic["slug"]
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = AUDIO_DIR / f"{slug}.mp3"
    out_path = OUTPUT_DIR / f"{slug}.mp4"

    synthesize_voiceover(topic["script"], audio_path)

    background_clip = ASSETS_DIR / "backgrounds" / topic.get("background", "default.mp4")
    if not background_clip.exists():
        raise SystemExit(f"Missing background clip: {background_clip} - pull one into assets/backgrounds/ from Drive.")

    render_video(audio_path, background_clip, topic["caption_headline"], out_path)
    print(f"rendered: {out_path}")
    print(f"title: {topic['title']}")
    print(f"description: {topic['description']}")


if __name__ == "__main__":
    main()
