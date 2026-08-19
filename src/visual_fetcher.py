"""Fetch B-roll for a video: Pexels video search by keyword, then Pixabay as a
second online source, then a local fallback that guarantees this function
never returns an empty list — this is the step most likely to fail (no key,
no results, rate limit), so it must degrade gracefully rather than crash the
job.

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

# Niche keywords ensure every search stays anchored to the channel's
# psychology/mind visual identity, even when the topic yields no usable
# English content words (e.g. a Vietnamese topic). Kept deliberately varied
# (not just "psychology") since that single term over-indexes on generic
# therapist/clinic stock footage on Pexels; a shuffled subset each call also
# means re-running the same topic doesn't keep hitting the same clips.
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

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "why", "does", "do", "did",
    "how", "what", "when", "who", "people", "you", "your", "this", "that",
    "of", "to", "in", "on", "for", "and", "or", "not", "it", "its", "be",
    "can", "will", "with", "from", "by", "as", "at", "if", "so", "but",
    "than", "then", "more", "most", "less", "make", "makes", "one", "them",
    "after", "before", "into", "onto", "about", "because", "into", "have",
    "has", "had", "just", "even", "only", "really", "actually", "which",
}

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm"}


def _extract_keywords(topic: str, script: str, n: int = 5, language: str = "en") -> list[str]:
    """Rule-based keyword extraction: a couple of ascii content-words from the
    topic/script plus the fixed niche keyword pool, so results stay on-theme.

    Content-word extraction only runs for language="en". Most Vietnamese
    words carry diacritics and get filtered out by the a-z regex as intended,
    but some common short words (e.g. "nghe", "phim") happen to be pure
    ASCII and would otherwise slip through as if they were English search
    terms — Pexels then matches them almost randomly (observed: "phim"
    returned an unrelated cow-and-bus clip). Simplest reliable fix is to
    gate this on language rather than try to blocklist individual words.
    """
    seen: list[str] = []
    if language == "en":
        text = f"{topic} {script}".lower()
        words = re.findall(r"[a-z]{4,}", text)
        for w in words:
            if w not in _STOPWORDS and w not in seen:
                seen.append(w)
    shuffled_niche = NICHE_KEYWORDS.copy()
    random.shuffle(shuffled_niche)
    keywords = seen[:2] + shuffled_niche
    return keywords[:n]


@retry_network(max_attempts=3)
def _search_pexels(keyword: str, api_key: str, min_duration: int) -> list[dict]:
    resp = requests.get(
        PEXELS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": keyword, "orientation": "portrait", "per_page": 10},
        timeout=15,
    )
    resp.raise_for_status()
    videos = resp.json().get("videos", [])
    return [v for v in videos if v.get("duration", 0) >= min_duration]


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
def _search_pixabay(keyword: str, api_key: str, min_duration: int) -> list[dict]:
    resp = requests.get(
        PIXABAY_SEARCH_URL,
        params={"key": api_key, "q": keyword, "video_type": "film", "safesearch": "true", "per_page": 10},
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    return [v for v in hits if v.get("duration", 0) >= min_duration]


def _pick_pixabay_video_file(video: dict) -> str | None:
    """Pick a rendition around 1080p wide, preferring portrait/square framing
    when Pixabay offers it (unlike Pexels, Pixabay doesn't support an
    orientation search filter, so most hits are landscape)."""
    sizes = video.get("videos", {})
    files = [s for s in sizes.values() if s.get("url")]
    if not files:
        return None
    portrait = [f for f in files if f.get("height", 0) >= f.get("width", 1)]
    candidates = portrait or files
    candidates.sort(key=lambda f: abs((f.get("width") or 0) - 1080))
    return candidates[0]["url"]


def _download(url: str, dest: Path) -> Path:
    with requests.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest


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


def fetch_visuals(topic: str, script: str, out_dir: Path, n_clips: int, cfg: dict, language: str = "en") -> list[Path]:
    """Return exactly n_clips local file paths (video or image) to use as
    background visuals, in the order they should appear in the final video.
    """
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    visuals_cfg = cfg.get("visuals", {})
    min_duration = visuals_cfg.get("min_clip_sec", 6)
    pexels_key = os.getenv("PEXELS_API_KEY", "").strip()
    pixabay_key = os.getenv("PIXABAY_API_KEY", "").strip()

    picked: list[Path] = []
    used_urls: set[str] = set()
    keywords = _extract_keywords(topic, script, n=n_clips + len(NICHE_KEYWORDS), language=language)

    if pexels_key:
        for keyword in keywords:
            if len(picked) >= n_clips:
                break
            try:
                results = _search_pexels(keyword, pexels_key, min_duration)
            except Exception as e:
                log.warning("Pexels search for %r failed: %s", keyword, e)
                continue
            for video in results:
                if len(picked) >= n_clips:
                    break
                link = _pick_video_file(video)
                if not link or link in used_urls:
                    continue
                dest = clips_dir / f"pexels_{len(picked)}_{video['id']}.mp4"
                try:
                    _download(link, dest)
                except Exception as e:
                    log.warning("download failed for video %s: %s", video.get("id"), e)
                    continue
                used_urls.add(link)
                picked.append(dest)
        log.info("Pexels supplied %d/%d clip(s)", len(picked), n_clips)
    else:
        log.info("PEXELS_API_KEY not set — skipping Pexels")

    # --- second online source: Pixabay (same free-commercial-use license as Pexels) ---
    if len(picked) < n_clips and pixabay_key:
        before = len(picked)
        for keyword in keywords:
            if len(picked) >= n_clips:
                break
            try:
                results = _search_pixabay(keyword, pixabay_key, min_duration)
            except Exception as e:
                log.warning("Pixabay search for %r failed: %s", keyword, e)
                continue
            for video in results:
                if len(picked) >= n_clips:
                    break
                link = _pick_pixabay_video_file(video)
                if not link or link in used_urls:
                    continue
                dest = clips_dir / f"pixabay_{len(picked)}_{video['id']}.mp4"
                try:
                    _download(link, dest)
                except Exception as e:
                    log.warning("download failed for video %s: %s", video.get("id"), e)
                    continue
                used_urls.add(link)
                picked.append(dest)
        log.info("Pixabay supplied %d additional clip(s)", len(picked) - before)
    elif len(picked) < n_clips:
        log.info("PIXABAY_API_KEY not set — skipping Pixabay")

    # --- fill any shortfall from data/assets_local ---
    if len(picked) < n_clips:
        fallback_dir = Path(visuals_cfg.get("fallback_dir", "data/assets_local"))
        local_files = [
            p for p in fallback_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in (_IMAGE_EXTS | _VIDEO_EXTS)
            and "music" not in p.parts
        ]
        if local_files:
            before = len(picked)
            random.shuffle(local_files)
            i = 0
            while len(picked) < n_clips:
                picked.append(local_files[i % len(local_files)])
                i += 1
            log.info("filled %d clip(s) from local fallback %s", len(picked) - before, fallback_dir)

    # --- absolute last resort: synthesize plain-color stills ---
    if len(picked) < n_clips:
        synth_dir = clips_dir
        missing = n_clips - len(picked)
        log.warning("no local fallback assets found — synthesizing %d placeholder still(s)", missing)
        for i in range(missing):
            picked.append(_generate_synthetic_fallback(synth_dir / f"synthetic_{i}.png", i))

    return picked[:n_clips]


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
