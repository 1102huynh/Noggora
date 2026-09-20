"""Small sound effects for the video, synthesized with ffmpeg (so, like the
music, there's nothing to license): a "whoosh" under each clip transition and a
soft low "hit" when the title card appears.

To use your own sounds instead, drop files named whoosh.* / hit.* (mp3, wav,
m4a, ogg) into data/assets_local/sfx/ — they take priority over the generated
ones.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.utils import get_logger

log = get_logger("sfx")

_AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac")

# name -> (duration seconds, aevalsrc expression for one channel, extra filters)
# whoosh: a rising sine sweep (200 -> 3000 Hz) plus noise, swelling in and out.
# hit:    a 55 Hz thump that decays fast, plus a very short noise click.
_RECIPES = {
    "whoosh": (
        0.7,
        "0.9*(sin(2*PI*(200*t+2800*t*t/1.4))*0.25+(random(0)-0.5)*0.9)*pow(sin(PI*t/0.7),2)",
        "highpass=f=250,lowpass=f=7000",
    ),
    "hit": (
        0.9,
        "0.9*sin(2*PI*55*t)*exp(-6*t)+0.35*sin(2*PI*110*t)*exp(-9*t)+0.25*(random(0)-0.5)*exp(-70*t)",
        "lowpass=f=1800",
    ),
}
_CACHE_VERSION = "v1"


def _synthesize(name: str, out_path: Path) -> Path:
    duration, expr, filters = _RECIPES[name]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expr}|{expr}':s=44100:d={duration}",
            "-af", f"{filters},alimiter=limit=0.8", str(out_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed synthesizing sfx {name!r}:\n{result.stderr[-1500:]}")
    return out_path


def get_sfx(name: str, sfx_dir: Path) -> Path:
    """A user-supplied <sfx_dir>/<name>.* if present, else the cached
    synthesized one (created on first use)."""
    if sfx_dir.exists():
        for ext in _AUDIO_EXTS:
            user_file = sfx_dir / f"{name}{ext}"
            if user_file.exists():
                return user_file
    cached = sfx_dir / "generated" / f"{name}_{_CACHE_VERSION}.wav"
    if not cached.exists():
        log.info("synthesizing sfx %s -> %s", name, cached)
        _synthesize(name, cached)
    return cached
