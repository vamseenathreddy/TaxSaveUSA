#!/usr/bin/env python3
"""
Generates assets/audio/applause.mp3 - a ~3 second synthetic "crowd
applause" sting, procedurally generated (not a downloaded/licensed
sample) so it's unambiguously free to use with no attribution or
licensing question, consistent with the channel's free-tools-only,
no-billing constraint.

Technique: real applause is many individual hand-claps (short,
broadband noise transients, fast attack/quick decay) overlapping at
slightly different times - this layers ~200 randomized band-passed
noise bursts of varying center frequency/duration/amplitude across 3
seconds, plus a faint continuous noise bed for "room" texture, under an
overall fade-in/fade-out swell. This is a standard sound-design
technique for synthetic applause/crowd-noise beds.

Re-run this (python3 scripts/make_applause.py) to regenerate the asset
if it ever needs to sound different (louder, longer, denser claps) -
it's deterministic (fixed RNG seed) so re-running without code changes
reproduces the exact same file.

pip install --break-system-packages numpy scipy
"""
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "assets" / "audio" / "applause.mp3"

SR = 44100
DUR = 3.0
N = int(SR * DUR)
RNG = np.random.default_rng(42)  # fixed seed -> reproducible output


def bandpass_noise(n: int, lo: float, hi: float, sr: int = SR) -> np.ndarray:
    noise = RNG.normal(0, 1, n)
    b, a = butter(4, [lo / (sr / 2), hi / (sr / 2)], btype="band")
    return lfilter(b, a, noise)


def make_applause() -> np.ndarray:
    out = np.zeros(N)

    # Individual "claps": short noise bursts with fast attack, quick
    # decay, randomized center frequency/timing/amplitude, scattered
    # across the full duration - simulates many hands clapping at
    # slightly different times.
    n_claps = 220
    for _ in range(n_claps):
        t0 = RNG.uniform(0, DUR - 0.08)
        start = int(t0 * SR)
        dur = RNG.uniform(0.02, 0.05)
        length = int(dur * SR)
        if start + length > N:
            length = N - start
        lo = RNG.uniform(1500, 3000)
        hi = lo + RNG.uniform(2000, 5000)
        burst = bandpass_noise(length, lo, min(hi, SR / 2 - 100))
        env = np.exp(-np.linspace(0, 12, length))  # fast attack, exponential decay
        burst *= env
        amp = RNG.uniform(0.3, 1.0)
        out[start:start + length] += burst[:length] * amp

    # Faint continuous broadband "crowd/room" texture under the claps.
    bed = bandpass_noise(N, 800, 8000) * 0.05
    out += bed

    # Overall swell: quick fade-in, sustain, fade-out.
    fade_in = int(0.15 * SR)
    fade_out = int(0.4 * SR)
    env = np.ones(N)
    env[:fade_in] = np.linspace(0, 1, fade_in)
    env[-fade_out:] = np.linspace(1, 0, fade_out)
    out *= env

    return out / np.max(np.abs(out)) * 0.85  # normalize to a safe peak


if __name__ == "__main__":
    import subprocess
    import tempfile

    audio = make_applause()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp_wav:
        wavfile.write(tmp_wav.name, SR, (audio * 32767).astype(np.int16))
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_wav.name, "-q:a", "4", str(OUT_PATH)],
            check=True,
        )
    print(f"wrote {OUT_PATH}")
