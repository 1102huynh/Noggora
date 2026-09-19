"""Split the voice track into scenes, so each B-roll clip lines up with what is
actually being said instead of the video being cut into equal slices that
land mid-sentence.

The plan is derived from voice.srt (one cue per caption phrase; a cue that ends
in . ! or ? closes a sentence). Cuts land in the pause between two phrases:
pauses between sentences are strongly preferred, but a long sentence can still
be split at a phrase boundary so no single clip has to hold the screen for
10+ seconds. The plan drives both which footage is searched for (per-scene
text) and how long each clip stays on screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pysubs2

_SENTENCE_END = (".", "!", "?")
_INF = float("inf")

# Cost model (seconds^2): a scene costs (length - even_share)^2, plus these.
_MID_SENTENCE_CUT_PENALTY = 6.0   # ~2.4s of extra length imbalance to justify cutting inside a sentence
_TOO_SHORT_SEC = 2.5
_TOO_SHORT_PENALTY = 25.0


@dataclass
class Scene:
    start: float  # seconds into the voice track
    end: float
    text: str  # everything spoken during this scene

    @property
    def duration(self) -> float:
        return self.end - self.start


def _read_cues(srt_path: Path) -> list[tuple[float, float, str]]:
    subs = pysubs2.load(str(srt_path), encoding="utf-8")
    return [(line.start / 1000, line.end / 1000, line.text.replace("\\N", " ").strip()) for line in subs]


def plan_scenes(srt_path: Path, total_duration: float, n_scenes: int) -> list[Scene]:
    """Return up to n_scenes contiguous scenes covering [0, total_duration],
    each close to an even share of the runtime (fewer scenes if the script has
    fewer caption phrases than n_scenes)."""
    cues = _read_cues(srt_path)
    m = len(cues)
    n = max(1, min(n_scenes, m))
    if n == 1:
        return [Scene(0.0, total_duration, " ".join(c[2] for c in cues))]

    # cut[i] = time of the pause between cue i-1 and cue i (i = 1..m-1)
    cut = [0.0] + [(cues[i - 1][1] + cues[i][0]) / 2 for i in range(1, m)]
    sentence_cut = [False] + [cues[i - 1][2].rstrip().endswith(_SENTENCE_END) for i in range(1, m)]
    target = total_duration / n

    def cost(a: int, b: int) -> float:
        """Scene made of cues a..b-1."""
        t0 = 0.0 if a == 0 else cut[a]
        t1 = total_duration if b == m else cut[b]
        length = t1 - t0
        c = (length - target) ** 2
        if length < _TOO_SHORT_SEC:
            c += _TOO_SHORT_PENALTY
        if b < m and not sentence_cut[b]:
            c += _MID_SENTENCE_CUT_PENALTY
        return c

    best = [[_INF] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for k in range(1, n + 1):
        for b in range(k, m + 1):
            for a in range(k - 1, b):
                c = best[k - 1][a] + cost(a, b)
                if c < best[k][b]:
                    best[k][b], back[k][b] = c, a

    edges = [m]
    for k in range(n, 0, -1):
        edges.append(back[k][edges[-1]])
    edges.reverse()  # [0, cut_1, ..., m]

    scenes = []
    for a, b in zip(edges, edges[1:]):
        t0 = 0.0 if a == 0 else cut[a]
        t1 = total_duration if b == m else cut[b]
        scenes.append(Scene(t0, t1, " ".join(c[2] for c in cues[a:b])))
    return scenes
