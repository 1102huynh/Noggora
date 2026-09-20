"""Fetch B-roll for a video, one clip per scene, matched to what is being said.

Every scene gets its own search queries, in priority order:
  1. the topic's hand-written `visual_keywords[i]` (from topic_bank.csv),
  2. concrete things mentioned in that scene's sentences (see _CONCEPT_MAP),
  3. a shuffled pool of on-brand niche queries (NICHE_KEYWORDS).
Scenes alternate between the online sources that have an API key (Pexels,
Pixabay) so one video mixes footage from both, and a source that comes up
empty is covered by the other. If nothing online works, a local fallback (then
a synthesized placeholder) guarantees this function never returns an empty
list — this is the step most likely to fail (no key, no results, rate limit),
so it must degrade gracefully rather than crash the job.

Both Pexels and Pixabay are used specifically because their standard license
grants free commercial use with no attribution required — the same
requirement that rules out sourcing clips from places like Pinterest, which
mostly re-hosts other people's images with no license to redistribute.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
from pathlib import Path

import requests

from src.utils import get_logger, retry_network

log = get_logger("visual_fetcher")

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"

# Niche queries keep a scene on the channel's psychology/mind visual identity
# when nothing more specific was found. Deliberately varied (a single term like
# "psychology" over-indexes on generic therapist/clinic stock footage), and
# shuffled per call so re-running a topic doesn't hit the same clips.
NICHE_KEYWORDS = [
    "person thinking",
    "human brain",
    "decision making",
    "contemplation closeup",
    "thoughtful face",
    "silhouette thinking",
    "abstract mind concept",
    "focused person alone",
    "overthinking",
    "emotions face closeup",
]

# Concrete words that might appear in a scene's text -> a stock-footage query
# that actually shows them. First matching entries win, so keep specific words
# ahead of vague ones.
_CONCEPT_MAP: list[tuple[tuple[str, ...], str]] = [
    (("movie", "film", "cinema"), "cinema audience watching"),
    (("coffee", "cafe"), "coffee cup table"),
    (("phone", "smartphone", "scroll"), "person using smartphone"),
    (("crowd", "crowded", "bystander", "stranger"), "crowd of people walking"),
    (("team", "group", "meeting", "boss", "coworker", "office"), "office meeting people"),
    (("friend", "friends", "closer", "connection"), "friends talking together"),
    (("mirror",), "person looking in mirror"),
    (("study", "exam", "test", "learn", "student", "cramming"), "student studying"),
    (("shopping", "price", "jacket", "sale", "store", "buy", "dollars", "money", "pay"), "shopping store"),
    (("traffic", "driver", "driving", "car"), "city traffic driving"),
    (("night", "evening", "sleep", "tired"), "person at window night"),
    (("song", "songs", "music", "hum"), "headphones listening music"),
    (("gym", "exercise", "workout"), "gym workout"),
    (("cook", "cooked", "meal", "takeout", "food", "dessert"), "cooking in kitchen"),
    (("clock", "hour", "week", "minutes", "waiting", "deadline"), "clock ticking"),
    (("laugh", "laughing", "joke", "funny"), "people laughing"),
    (("afraid", "fear", "danger", "scared", "nervous", "anxious"), "anxious person"),
    (("comment", "criticism", "compliments", "praise"), "person reading phone reaction"),
    (("memory", "remember", "memories", "nostalgia", "school", "old days"), "old photographs memories"),
    (("choice", "choices", "options", "decide", "decision"), "choosing between options"),
    (("secret", "confess", "silence", "quiet", "question"), "quiet conversation two people"),
    (("mistake", "trip", "spill", "clumsy"), "person spilling coffee"),
    (("project", "goal", "finish", "progress", "sprint", "task", "tasks"), "person working laptop"),
    (("furniture", "shelf", "build", "assembled", "home"), "assembling furniture"),
]

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm"}


def _concept_queries(scene_text: str, n: int = 2) -> list[str]:
    """Up to n stock-footage queries for concrete things named in scene_text."""
    words = set(re.findall(r"[a-z']+", scene_text.lower()))
    padded = f" {scene_text.lower()} "
    found: list[str] = []
    for triggers, query in _CONCEPT_MAP:
        if any((t in words) or (" " in t and f" {t} " in padded) for t in triggers):
            found.append(query)
        if len(found) >= n:
            break
    return found


def scene_queries(index: int, scene_text: str, visual_keywords: list[str] | None) -> list[str]:
    """Search queries for scene `index`, best first (see module docstring)."""
    queries: list[str] = []
    if visual_keywords and index < len(visual_keywords):
        queries.append(visual_keywords[index])
    queries.extend(_concept_queries(scene_text))
    niche = NICHE_KEYWORDS.copy()
    random.shuffle(niche)
    queries.extend(niche)
    seen: set[str] = set()
    return [q for q in queries if not (q in seen or seen.add(q))]


@retry_network(max_attempts=3)
def _search_pexels(query: str, api_key: str) -> list[dict]:
    resp = requests.get(
        PEXELS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": query, "orientation": "portrait", "per_page": 10},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("videos", [])


def _pick_video_file(video: dict) -> str | None:
    """Prefer a portrait file around 1080p wide — good enough quality without
    downloading a needlessly huge 4K source."""
    files = [f for f in video.get("video_files", []) if f.get("height", 0) >= f.get("width", 1)]
    if not files:
        files = video.get("video_files", [])
    if not files:
        return None
    files.sort(key=lambda f: abs((f.get("width") or 0) - 1080))
    return files[0]["link"]


@retry_network(max_attempts=3)
def _search_pixabay(query: str, api_key: str) -> list[dict]:
    resp = requests.get(
        PIXABAY_SEARCH_URL,
        params={"key": api_key, "q": query, "video_type": "film", "safesearch": "true", "per_page": 10},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("hits", [])


def _pick_pixabay_video_file(video: dict) -> str | None:
    """Pixabay has no orientation filter, so most hits are landscape. The
    assembler letterboxes those over a blurred copy of themselves, so the
    useful thing here is the sharpest rendition available (up to ~1080p tall),
    not the one nearest 1080 wide — that picked 720p and got upscaled ~2.7x."""
    sizes = video.get("videos", {})
    files = [s for s in sizes.values() if s.get("url")]
    if not files:
        return None
    # Never 4K: the output is 1080x1920, so 2160p is just a slower download and render.
    usable = [f for f in files if (f.get("height") or 0) <= 1080]
    if usable:
        return max(usable, key=lambda f: f.get("height") or 0)["url"]
    return min(files, key=lambda f: f.get("height") or 0)["url"]


def _is_portrait(source: str, video: dict) -> bool:
    if source == "pexels":
        return (video.get("height") or 0) >= (video.get("width") or 1)
    sizes = [s for s in video.get("videos", {}).values() if s.get("url")]
    return any((s.get("height") or 0) >= (s.get("width") or 1) for s in sizes)


def _download(url: str, dest: Path) -> Path:
    with requests.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest


_QUERY_STOP = {"the", "and", "with", "for", "from", "person", "people", "man", "woman"}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):  # crude: "meetings"/"meeting", "papers"/"paper"
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _relevance(query: str, source: str, video: dict) -> float:
    """0..1: how many of the query's words appear in what the source says about
    the clip — Pixabay's `tags`, or the description slug in a Pexels clip's URL
    ("/video/woman-typing-on-laptop-123/"). The APIs' own ranking is loose, so
    this is what stops "coffee menu choosing" returning a random baby."""
    if source == "pixabay":
        text = str(video.get("tags", ""))
    else:
        match = re.search(r"/video/([^/]+?)-?\d*/?$", str(video.get("url", "")))
        text = match.group(1) if match else ""
    have = {_stem(w) for w in re.findall(r"[a-z]+", text.lower())}
    # generic nouns ("person", "people") match far too much to count as evidence
    want = {_stem(w) for w in re.findall(r"[a-z]+", query.lower()) if len(w) >= 3 and w not in _QUERY_STOP}
    return len(want & have) / len(want) if want else 0.0


# Take a clip as soon as enough of its query words are confirmed. Pixabay's
# tags are long and reliable, so half the words is a fair bar. A Pexels URL slug
# is only a few words, so the bar is lower — but Pexels ranks results by real
# (semantic) relevance, which the slug can't show, so its top three results get
# a bonus: rank alone isn't enough to pass, but rank + one confirmed word is.
_GOOD_ENOUGH = {"pexels": 0.3, "pixabay": 0.5}
_PEXELS_TOP_RANK_BONUS = 0.25


def _fetch_scene_clip(
    sources: list[tuple], queries: list[str], needed_sec: float, clips_dir: Path, used_ids: set[str],
    max_queries: int = 6,
) -> Path | None:
    """Download one clip for a scene. `sources` is (name, search_fn, pick_fn,
    api_key) in preference order for this scene.

    Walks the queries best-first and scores every result for relevance to the
    query (see _relevance). The first clip that clears _GOOD_ENOUGH (per
    source) wins
    (portrait, then long-enough-to-not-loop, break ties); if no query gets
    there, the best-scoring clip seen overall is used, so a scene always gets
    something."""
    fallback: list[tuple] = []  # (name, pick_fn, video, query, relevance) — best first once sorted

    def rank(name: str, video: dict, rel: float) -> tuple:
        return (-rel, not _is_portrait(name, video), video.get("duration", 0) < needed_sec)

    def download(name: str, pick_fn, video: dict, query: str, rel: float) -> Path | None:
        link = pick_fn(video)
        if not link:
            return None
        dest = clips_dir / f"{name}_{video['id']}.mp4"
        try:
            _download(link, dest)
        except Exception as e:
            log.warning("download failed for %s video %s: %s", name, video.get("id"), e)
            return None
        used_ids.add(f"{name}_{video['id']}")
        log.info("scene clip <- %s %r (relevance %.0f%%, %s)", name, query, rel * 100, dest.name)
        return dest

    for query in queries[:max_queries]:
        good: list[tuple] = []
        for src_idx, (name, search_fn, pick_fn, api_key) in enumerate(sources):
            try:
                results = search_fn(query, api_key)
            except Exception as e:
                log.warning("%s search for %r failed: %s", name, query, e)
                continue
            for position, video in enumerate(results):
                if f"{name}_{video['id']}" in used_ids or video.get("duration", 0) < 3:
                    continue
                rel = _relevance(query, name, video)
                if name == "pexels" and position < 3:
                    rel += _PEXELS_TOP_RANK_BONUS
                entry = (src_idx, rank(name, video, rel), name, pick_fn, video, query, rel)
                (good if rel >= _GOOD_ENOUGH[name] else fallback).append(entry)
        # among clips that are good enough, this scene's preferred source goes first
        # (sources alternate between scenes, so a video mixes Pexels and Pixabay)
        for _, _, name, pick_fn, video, q, rel in sorted(good, key=lambda e: (e[0], e[1]))[:3]:
            path = download(name, pick_fn, video, q, rel)
            if path:
                return path

    # nothing cleared the bar for any query: the most relevant clip seen overall
    for _, _, name, pick_fn, video, q, rel in sorted(fallback, key=lambda e: (e[1], e[0]))[:3]:
        path = download(name, pick_fn, video, q, rel)
        if path:
            return path
    return None


def _generate_synthetic_fallback(dest: Path, index: int) -> Path:
    """Last-resort fallback if even the local fallback_dir is empty: a plain
    color still generated on the fly with ffmpeg, so fetch_visuals can never
    return an empty list."""
    colors = ["0x1a1a2e", "0x16213e", "0x0f3460", "0x533483", "0x2b2d42"]
    color = colors[index % len(colors)]
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920", "-frames:v", "1", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def fetch_visuals(
    topic: str, script: str, out_dir: Path, n_clips: int, cfg: dict, language: str = "en",
    scenes: list | None = None, visual_keywords: list[str] | None = None,
) -> list[Path]:
    """Return exactly n_clips local file paths (video or image) to use as
    background visuals, in the order they should appear in the final video.

    `scenes` (see src.scenes) gives the text spoken during each clip, which
    drives the per-scene search; without it every scene just gets the topic
    keywords / niche pool. `visual_keywords` are hand-written per-scene search
    phrases for this topic, in narrative order.
    """
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    visuals_cfg = cfg.get("visuals", {})
    pexels_key = os.getenv("PEXELS_API_KEY", "").strip()
    pixabay_key = os.getenv("PIXABAY_API_KEY", "").strip()

    all_sources = []
    if pexels_key:
        all_sources.append(("pexels", _search_pexels, _pick_video_file, pexels_key))
    else:
        log.info("PEXELS_API_KEY not set — skipping Pexels")
    if pixabay_key:
        all_sources.append(("pixabay", _search_pixabay, _pick_pixabay_video_file, pixabay_key))
    else:
        log.info("PIXABAY_API_KEY not set — skipping Pixabay")

    picked: list[Path | None] = [None] * n_clips
    used_ids: set[str] = set()

    if all_sources:
        for i in range(n_clips):
            scene = scenes[i] if scenes and i < len(scenes) else None
            scene_text = scene.text if scene else f"{topic} {script}"
            needed = scene.duration if scene else 7.0
            queries = scene_queries(i, scene_text, visual_keywords)
            # alternate which source gets first pick, so a video mixes both
            k = i % len(all_sources)
            ordered = all_sources[k:] + all_sources[:k]
            picked[i] = _fetch_scene_clip(ordered, queries, needed, clips_dir, used_ids)

    # --- fill any scene that got nothing from data/assets_local ---
    missing = [i for i, p in enumerate(picked) if p is None]
    if missing:
        fallback_dir = Path(visuals_cfg.get("fallback_dir", "data/assets_local"))
        local_files = [
            p for p in fallback_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in (_IMAGE_EXTS | _VIDEO_EXTS)
            and "music" not in p.parts
        ] if fallback_dir.exists() else []
        if local_files:
            random.shuffle(local_files)
            for n, i in enumerate(missing):
                picked[i] = local_files[n % len(local_files)]
            log.info("filled %d clip(s) from local fallback %s", len(missing), fallback_dir)

    # --- absolute last resort: synthesize plain-color stills ---
    still_missing = [i for i, p in enumerate(picked) if p is None]
    if still_missing:
        log.warning("no local fallback assets found — synthesizing %d placeholder still(s)", len(still_missing))
        for i in still_missing:
            picked[i] = _generate_synthetic_fallback(clips_dir / f"synthetic_{i}.png", i)

    return [p for p in picked if p is not None]


if __name__ == "__main__":
    from dotenv import load_dotenv

    from src.utils import load_config

    load_dotenv()
    cfg = load_config()
    test_out = Path("output") / "_selftest_visuals_fetch"
    clips = fetch_visuals(
        topic="Why does silence after a question make people confess more?",
        script="Silence creates social pressure. People rush to fill it, often with the truth.",
        out_dir=test_out,
        n_clips=cfg["visuals"]["clips_per_video"],
        cfg=cfg,
    )
    print(f"{len(clips)} clip(s):")
    for c in clips:
        print(" -", c)
