"""Background music matched to a topic's mood.

Two sources, in priority order:
  1. Real tracks you drop into the music folder (data/assets_local/music/*.mp3,
     or data/assets_local/music/<mood>/*.mp3 for mood-specific ones). Use these
     if you have royalty-free music you're licensed to use — nothing beats it.
  2. Otherwise a track synthesized with ffmpeg: a slow 4-chord ambient pad
     (stereo, detuned voices with a few harmonics, soft noise bed, echo/reverb
     tail). Fully synthesized, so zero copyright risk by construction — nothing
     was sampled or downloaded — while still being picked per-topic via a mood
     classifier so it isn't one generic drone on every video.

Why not search for "royalty-free music matching topic" online: there is no
free, keyless, legally-safe API for that (unlike Pexels for footage), and
getting music licensing wrong is exactly the kind of mistake that gets a
channel's audio muted/claimed.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

from src.utils import get_logger

log = get_logger("music_composer")

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
_CACHE_VERSION = "v2"  # bump to invalidate cached synthesized tracks after a recipe change

# MIDI note numbers per chord (A4 = 69). Four chords per mood, each held ~17s
# and crossfaded, so a ~60s track never loops audibly within a 45s video.
_MOODS: dict[str, dict] = {
    # dark / hidden manipulation, secrets, being controlled — A minor drifting to a tense E
    "mysterious": {"chords": [[45, 52, 57, 60], [41, 48, 52, 57], [38, 45, 50, 53], [40, 47, 52, 56]],
                   "tremolo": 0.10, "lowpass": 1800},
    # fear, danger, loss, urgency — minor with a diminished turn
    "tense": {"chords": [[36, 48, 51, 55], [32, 44, 48, 51], [41, 48, 53, 56], [35, 47, 50, 53]],
              "tremolo": 0.20, "lowpass": 1600},
    # "why does X happen" open curiosity, paradoxes — bright, floating major-7ths
    "curious": {"chords": [[38, 45, 50, 54], [35, 47, 50, 54], [43, 50, 54, 59], [45, 52, 57, 61]],
                "tremolo": 0.14, "lowpass": 2600},
    # trust, connection, actionable hope — plain warm C major
    "warm": {"chords": [[36, 48, 52, 55], [45, 52, 57, 60], [41, 48, 53, 57], [43, 50, 55, 59]],
             "tremolo": 0.10, "lowpass": 2300},  # ffmpeg's tremolo rejects f < 0.1
    # light/quirky (furniture, coffee spills, games) — G major, quicker shimmer
    "playful": {"chords": [[43, 55, 59, 62], [40, 52, 55, 59], [48, 55, 60, 64], [50, 57, 62, 66]],
                "tremolo": 0.26, "lowpass": 3000},
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

_TRACK_SEC = 62
_CHORD_SEC = 17
_CROSSFADE_SEC = 2


def pick_mood(topic: str, script: str) -> str:
    """Rule-based mood classifier: count keyword hits per mood, pick the top
    scorer. Falls back to DEFAULT_MOOD (fits the channel's dark/moody brand)
    when nothing matches — mirrors visual_fetcher's keyword approach."""
    text = f"{topic} {script}".lower()
    scores = {mood: sum(text.count(kw) for kw in kws) for mood, kws in _MOOD_KEYWORDS.items()}
    best_mood, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_mood if best_score > 0 else DEFAULT_MOOD


def _midi_hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _pad_expression(notes: list[int], detune: float) -> str:
    """aevalsrc expression for one stereo channel of a chord: each note is a
    fundamental + two quieter harmonics + a slightly detuned twin (the beating
    between them is what makes a pad sound wide and alive), all breathing with
    a slow LFO."""
    terms = []
    for note in notes:
        f = _midi_hz(note)
        terms.append(
            f"sin(2*PI*{f:.3f}*t)"
            f"+0.35*sin(2*PI*{2 * f:.3f}*t)"
            f"+0.12*sin(2*PI*{3 * f:.3f}*t)"
            f"+0.55*sin(2*PI*{f * detune:.3f}*t)"
        )
    return f"0.045*({'+'.join(terms)})*(0.85+0.15*sin(2*PI*0.18*t))"


def _synthesize_mood_track(mood: str, out_path: Path) -> Path:
    params = _MOODS[mood]
    chords = params["chords"]
    args = ["ffmpeg", "-y"]
    for notes in chords:
        left = _pad_expression(notes, 1.0035)
        right = _pad_expression(notes, 0.9965)
        args += ["-f", "lavfi", "-i", f"aevalsrc='{left}|{right}':s=44100:d={_CHORD_SEC}"]
    args += ["-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.02:sample_rate=44100:d={_TRACK_SEC}"]
    noise_idx = len(chords)

    parts = []
    for i in range(len(chords)):
        parts.append(f"[{i}:a]afade=t=in:st=0:d=3,afade=t=out:st={_CHORD_SEC - 3}:d=3[c{i}]")
    last = "c0"
    for i in range(1, len(chords)):
        parts.append(f"[{last}][c{i}]acrossfade=d={_CROSSFADE_SEC}[cf{i}]")
        last = f"cf{i}"
    parts.append(f"[{noise_idx}:a]lowpass=f=700,aformat=channel_layouts=stereo[noise]")
    parts.append(
        f"[{last}][noise]amix=inputs=2:duration=first:normalize=0,"
        f"tremolo=f={params['tremolo']}:d=0.25,"
        f"lowpass=f={params['lowpass']},"
        f"aecho=0.8:0.7:60|150|320:0.35|0.25|0.15,"
        f"afade=t=in:st=0:d=2,afade=t=out:st={_TRACK_SEC - 4}:d=4[out]"
    )
    args += ["-filter_complex", ";".join(parts), "-map", "[out]", "-t", str(_TRACK_SEC), "-b:a", "192k", str(out_path)]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed synthesizing mood track {mood!r}:\n{result.stderr[-2000:]}")
    return out_path


def _find_library_track(library_dir: Path | None, mood: str) -> Path | None:
    """A user-supplied track: prefer <library>/<mood>/, else any file directly
    in <library>. Ignores the 'generated' cache folder."""
    if library_dir is None or not library_dir.exists():
        return None
    for folder in (library_dir / mood, library_dir):
        if not folder.is_dir():
            continue
        tracks = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
        if tracks:
            return random.choice(tracks)
    return None


def get_music_for_topic(topic: str, script: str, cache_dir: Path, library_dir: Path | None = None) -> Path:
    """Return a background-music file matched to the topic's mood: a
    user-supplied track from library_dir if there is one, else a synthesized
    pad. Each synthesized mood is rendered once and cached (same mood -> same
    file across jobs) rather than re-synthesizing identical audio every run."""
    mood = pick_mood(topic, script)
    library_track = _find_library_track(library_dir, mood)
    if library_track is not None:
        log.info("using library track for mood %s: %s", mood, library_track)
        return library_track
    cache_path = cache_dir / f"mood_{mood}_{_CACHE_VERSION}.mp3"
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
