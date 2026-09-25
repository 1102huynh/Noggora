"""Shared helpers: logging, slugify, ffmpeg/ffprobe checks, config loading.

Nothing in this module talks to a network or an LLM — it is the dependency-free
foundation every other module in `src/` imports.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def retry_network(max_attempts: int = 3):
    """Shared retry policy for flaky network calls (Anthropic, Pexels, edge-tts).

    Exponential backoff (1s, 2s, 4s, ... capped at 8s), re-raises the last
    exception after max_attempts so callers can still log/handle the failure.
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class ManualModeRequired(Exception):
    """Raised by script_generator when no ANTHROPIC_API_KEY is configured.

    Signals to the orchestrator that this job cannot proceed automatically —
    the caller must drop a hand-written script into the job's output folder
    and re-run with --resume.
    """


class PipelineStepError(Exception):
    """Wraps an exception raised inside a named pipeline step.

    Lets pipeline.py log exactly which of the 6 steps failed without losing
    the original traceback context, and without crashing an entire batch run.
    """

    def __init__(self, step: str, original: Exception):
        self.step = step
        self.original = original
        super().__init__(f"[{step}] {original.__class__.__name__}: {original}")


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured to print to console with a consistent format.

    Idempotent — calling this repeatedly for the same name will not add
    duplicate handlers.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def slugify(text: str, max_len: int = 50) -> str:
    """Turn arbitrary (incl. Vietnamese diacritics) text into a filesystem-safe slug."""
    # Đ/đ don't decompose via NFKD (unlike e.g. â -> a + combining circumflex),
    # so without this they'd just vanish instead of becoming "d".
    text = text.replace("Đ", "D").replace("đ", "d")
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    if not slug:
        slug = "topic"
    return slug[:max_len].rstrip("-")


def new_job_slug(topic: str, max_len: int = 50) -> str:
    """slug + timestamp, guarantees a unique, sortable job folder name."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{slugify(topic, max_len)}-{ts}"


def check_ffmpeg() -> None:
    """Raise RuntimeError with an actionable message if ffmpeg/ffprobe are missing."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            f"Missing required tool(s): {', '.join(missing)}. "
            "Install ffmpeg (which bundles ffprobe) and ensure it's on PATH.\n"
            "  Windows: winget install Gyan.FFmpeg  (or choco install ffmpeg)\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   apt install ffmpeg / dnf install ffmpeg"
        )


def ffprobe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def ffprobe_dimensions(path: Path) -> tuple[int, int]:
    """Return (width, height) of the first video stream (or of an image)."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def measure_lufs(path: Path, max_seconds: float | None = None) -> float | None:
    """Integrated loudness (LUFS, EBU R128, gated so pauses don't count) of an
    audio file — of its first `max_seconds` if given — or None if it can't be
    measured (unreadable, or effectively silent)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.3f}"]
    cmd += ["-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    matches = re.findall(r"I:\s+(-?[\d.]+) LUFS", result.stderr)
    if result.returncode != 0 or not matches:
        return None
    value = float(matches[-1])  # the last one is the end-of-file summary
    return value if value > -69.0 else None  # -70 is ebur128's "nothing here"


def ffprobe_audio_channels(path: Path) -> int:
    """Channel count of the first audio stream."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=channels", "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return int(result.stdout.strip().splitlines()[0])


def load_config(config_path: str | Path = "config/settings.yaml") -> dict[str, Any]:
    """Load settings.yaml relative to the project root."""
    path = Path(config_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


if __name__ == "__main__":
    log = get_logger("utils-selftest")
    log.info("slugify: %s", slugify("Tại sao não bộ khiến bạn nhớ rõ những lời chê?"))
    log.info("job slug: %s", new_job_slug("Why does silence make people confess?"))
    try:
        check_ffmpeg()
        log.info("ffmpeg/ffprobe: OK")
    except RuntimeError as e:
        log.warning("ffmpeg check failed: %s", e)
    cfg = load_config()
    log.info("config loaded, channel_name=%s", cfg["branding"]["channel_name"])
