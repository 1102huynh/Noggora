"""Plain helper functions shared by several test modules."""

from __future__ import annotations

from pathlib import Path


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> Path:
    """cues = [(start_sec, end_sec, text), ...]"""

    def ts(sec: float) -> str:
        ms = int(round(sec * 1000))
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = [f"{i}\n{ts(a)} --> {ts(b)}\n{text}\n" for i, (a, b, text) in enumerate(cues, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path
