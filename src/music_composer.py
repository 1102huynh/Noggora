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

import json
import re
import subprocess
from dataclasses import dataclass
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

# Fallback classifier, run on the TOPIC only (topics written by Claude carry
# their own `mood`, see daily_topic.py). "why" is deliberately absent: nearly
# every topic starts with it, so it used to drag everything into one mood.
_MOOD_KEYWORDS: dict[str, list[str]] = {
    "mysterious": ["secret", "hidden", "manipulat", "control", "deceiv", "trick", "unconscious", "subconscious",
                   "illusion", "fooled", "hear", "notice", "see "],
    "tense": ["fear", "danger", "loss", "losing", "threat", "emergency", "risk", "scared", "anxious", "afraid",
              "judgment", "pressure", "confess", "stress", "nervous", "worse", "blame", "regret"],
    "curious": ["wonder", "strange", "surprising", "paradox", "random", "suddenly", "everywhere", "remember",
                "memory", "forget", "seem", "feel bigger", "feel better"],
    "warm": ["trust", "friend", "closer", "help", "hope", "kind", "connection", "reciproc", "favor", "love",
             "partner", "gift", "like someone", "happy"],
    "playful": ["furniture", "coffee", "spill", "funny", "game", "lottery", "jam", "movie", "song", "mistake",
                "clumsy", "shelf", "price"],
}

DEFAULT_MOOD = "mysterious"
MOODS = tuple(_MOODS)  # the five names, also the names of the library sub-folders

_TRACK_SEC = 62
_CHORD_SEC = 17
_CROSSFADE_SEC = 2


def pick_mood(topic: str, script: str = "") -> str:
    """Rule-based mood from the topic's keywords: count hits per mood, pick the
    top scorer. Only if the topic says nothing is the script consulted, and
    then DEFAULT_MOOD (fits the channel's dark/moody brand)."""
    for text in (topic.lower(), script.lower()):
        scores = {mood: sum(text.count(kw) for kw in kws) for mood, kws in _MOOD_KEYWORDS.items()}
        best_mood, best_score = max(scores.items(), key=lambda kv: kv[1])
        if best_score > 0:
            return best_mood
    return DEFAULT_MOOD


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


def _natural_key(name: str) -> list:
    """Sort key so "2 - a.mp3" comes before "10 - b.mp3" and case is ignored."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name.lower())]


def list_tracks(folder: Path) -> list[Path]:
    """The audio files directly in `folder`, in the order they will be used:
    by file name (natural order) — number them "01 - ...", "02 - ..." to control it."""
    if not folder.is_dir():
        return []
    tracks = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
    return sorted(tracks, key=lambda p: _natural_key(p.name))


def _read_state(state_path: Path | None) -> dict:
    if state_path is None or not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _next_in_order(tracks: list[Path], last_used: str | None) -> Path:
    """The track after `last_used` in file-name order, wrapping around to the
    first. Comparing names (not positions) keeps the order sensible when files
    are added, renamed or removed between runs."""
    if last_used:
        last_key = _natural_key(last_used)
        for track in tracks:
            if _natural_key(track.name) > last_key:
                return track
    return tracks[0]


@dataclass
class MusicChoice:
    path: Path
    mood: str
    source: str  # "library" (your track) or "synth"
    state_key: str | None = None  # which folder's turn this was, for record_used()


def choose_music(
    topic: str, script: str, cache_dir: Path, library_dir: Path | None = None,
    state_path: Path | None = None, mood: str | None = None,
) -> MusicChoice:
    """Pick the background music for a video.

    The mood is `mood` if given (Claude classifies its own topics), else derived
    from the topic's keywords. Then, in order:
      1. <library_dir>/<mood>/ — the next track after the one used last time,
         in file-name order, wrapping around;
      2. audio files loose in <library_dir> — same rule;
      3. a synthesized pad for the mood (cached per mood).
    Nothing is recorded here: call record_used() once the video succeeded, so a
    failed render doesn't skip a track.
    """
    mood = mood if mood in MOODS else pick_mood(topic, script)
    state = _read_state(state_path)
    if library_dir is not None and library_dir.exists():
        for key, folder in ((mood, library_dir / mood), ("_any", library_dir)):
            tracks = list_tracks(folder)
            if tracks:
                track = _next_in_order(tracks, state.get(key))
                log.info("mood %s -> library track %d/%d in %s: %s",
                         mood, tracks.index(track) + 1, len(tracks), folder.name, track.name)
                return MusicChoice(track, mood, "library", key)
    cache_path = cache_dir / f"mood_{mood}_{_CACHE_VERSION}.mp3"
    if not cache_path.exists():
        log.info("no tracks in %s — synthesizing a %s pad -> %s", (library_dir or Path('?')) / mood, mood, cache_path)
        _synthesize_mood_track(mood, cache_path)
    else:
        log.info("mood %s -> cached synthesized pad (%s)", mood, cache_path.name)
    return MusicChoice(cache_path, mood, "synth")


def record_used(choice: MusicChoice, state_path: Path | None) -> None:
    """Remember that `choice` was used, so the next video with the same mood
    takes the following track. No-op for synthesized music."""
    if choice.source != "library" or choice.state_key is None or state_path is None:
        return
    state = _read_state(state_path)
    state[choice.state_key] = choice.path.name
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_library_folders(library_dir: Path) -> list[Path]:
    """Create <library_dir>/<mood>/ for each mood (where you put your tracks)."""
    folders = []
    for mood in MOODS:
        folder = library_dir / mood
        folder.mkdir(parents=True, exist_ok=True)
        folders.append(folder)
    return folders


if __name__ == "__main__":
    for topic in [
        "Why does silence after a question make people confess more?",
        "Why do you love furniture you built yourself?",
        "Why do you trust someone after they share a secret?",
        "Why do you keep watching a bad movie to the end?",
    ]:
        print(f"{pick_mood(topic):11s} <- {topic}")
    lib = Path("data/assets_local/music")
    for mood in MOODS:
        print(f"{mood:11s}: {len(list_tracks(lib / mood))} track(s) in {lib / mood}")
