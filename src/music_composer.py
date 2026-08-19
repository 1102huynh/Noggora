"""Pick + synthesize background music matched to a topic's mood, with ffmpeg
(sine oscillators + tremolo/lowpass/echo) instead of sourcing real tracks.

Why synthesize instead of searching for "royalty-free music matching topic"
online: there is no free, keyless, legally-safe API for that (unlike Pexels
for images), and getting music licensing wrong is exactly the kind of mistake
that gets a channel's audio muted/claimed. Fully-synthesized audio has zero
copyright risk by construction — nothing was sampled or downloaded — while
still being picked per-topic via a mood classifier, so it's not just one
generic track on every video.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from src.utils import get_logger

log = get_logger("music_composer")

# (root, third, fifth) in Hz, + tremolo rate, lowpass cutoff, echo delay(ms)/decay
_MOODS: dict[str, dict] = {
    # dark / hidden manipulation, secrets, being controlled
    "mysterious": {"chord": (110.00, 130.81, 164.81), "tremolo": 0.10, "lowpass": 2000, "echo_ms": 90, "echo_decay": 0.30},
    # fear, danger, loss, urgency
    "tense":      {"chord": (130.81, 138.59, 174.61), "tremolo": 0.22, "lowpass": 1800, "echo_ms": 50, "echo_decay": 0.25},
    # "why does X happen" open curiosity, paradoxes
    "curious":    {"chord": (146.83, 220.00, 293.66), "tremolo": 0.15, "lowpass": 3000, "echo_ms": 100, "echo_decay": 0.30},
    # trust, connection, actionable hope
    "warm":       {"chord": (130.81, 164.81, 196.00), "tremolo": 0.08, "lowpass": 2500, "echo_ms": 120, "echo_decay": 0.35},
    # light/quirky (furniture, coffee spills, games)
    "playful":    {"chord": (196.00, 246.94, 293.66), "tremolo": 0.28, "lowpass": 3200, "echo_ms": 70, "echo_decay": 0.25},
}

_MOOD_KEYWORDS: dict[str, list[str]] = {
    "mysterious": ["secret", "hidden", "manipulat", "control", "deceiv", "trick", "unconscious", "subconscious"],
    "tense": ["fear", "danger", "loss", "losing", "threat", "emergency", "crash", "risk", "scared",
              "anxious", "afraid", "alarm", "judgment", "pressure", "confess"],
    "curious": ["why", "notice", "wonder", "curious", "strange", "surprising", "paradox", "random"],
    "warm": ["trust", "friend", "closer", "help", "hope", "kind", "connection", "reciproc", "gift"],
    "playful": ["furniture", "coffee", "spill", "funny", "game", "lottery", "jam", "movie", "song", "mistake", "clumsy"],
}

DEFAULT_MOOD = "mysterious"


def pick_mood(topic: str, script: str) -> str:
    """Rule-based mood classifier: count keyword hits per mood, pick the top
    scorer. Falls back to DEFAULT_MOOD (fits the channel's dark/moody brand)
    when nothing matches — mirrors visual_fetcher's keyword approach."""
    text = f"{topic} {script}".lower()
    scores = {mood: sum(text.count(kw) for kw in kws) for mood, kws in _MOOD_KEYWORDS.items()}
    best_mood, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_mood if best_score > 0 else DEFAULT_MOOD


def _synthesize_mood_track(mood: str, out_path: Path, duration: int = 40) -> Path:
    params = _MOODS[mood]
    r, t3, t5 = params["chord"]
    filter_complex = (
        f"[0:a]volume=0.10[a0];[1:a]volume=0.07[a1];[2:a]volume=0.07[a2];"
        f"[a0][a1][a2]amix=inputs=3:duration=longest,"
        f"tremolo=f={params['tremolo']}:d=0.3,"
        f"lowpass=f={params['lowpass']},"
        f"aecho=0.6:0.6:{params['echo_ms']}:{params['echo_decay']}[out]"
    )
    args = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={r}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={t3}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={t5}:duration={duration}",
        "-filter_complex", filter_complex,
        "-map", "[out]", "-t", str(duration),
        str(out_path),
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed synthesizing mood track {mood!r}:\n{result.stderr[-2000:]}")
    return out_path


def get_music_for_topic(topic: str, script: str, cache_dir: Path) -> Path:
    """Return a background-music file matched to the topic's mood. Each mood
    is rendered once and cached (same mood -> same file across jobs) rather
    than re-synthesizing identical audio every run."""
    mood = pick_mood(topic, script)
    cache_path = cache_dir / f"mood_{mood}.mp3"
    if not cache_path.exists():
        log.info("synthesizing new mood track: %s -> %s", mood, cache_path)
        _synthesize_mood_track(mood, cache_path)
    else:
        log.info("using cached mood track: %s (%s)", mood, cache_path)
    return cache_path


if __name__ == "__main__":
    for topic, script in [
        ("Why does silence after a question make people confess more?", "That gap is pressure, judgment, confess"),
        ("Why do you love furniture you built yourself?", "a wobbly shelf, funny mistake"),
        ("Why do you trust someone after they share a secret?", "hidden, trust, closer, connection"),
    ]:
        mood = pick_mood(topic, script)
        print(f"{mood:12s} <- {topic}")
    out = get_music_for_topic("test danger emergency", "fear loss risk", Path("output/_selftest_music"))
    print("generated:", out)
