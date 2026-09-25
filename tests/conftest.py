"""Shared fixtures. Nothing here touches the network or the real data/ folder:
media is generated with ffmpeg into pytest's tmp_path, LLM calls are faked."""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path

import pytest

from src.utils import load_config

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def pytest_collection_modifyitems(config, items):
    if HAVE_FFMPEG:
        return
    skip = pytest.mark.skip(reason="ffmpeg/ffprobe not installed")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def cfg() -> dict:
    """The real config/settings.yaml, as a private copy each test may edit."""
    return copy.deepcopy(load_config())


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture
def make_tone():
    """make_tone(path, seconds=2, freq=440, volume_db=-20, channels=1) -> path"""

    def make(path: Path, seconds: float = 2.0, freq: int = 440, volume_db: float = -20.0, channels: int = 1) -> Path:
        layout = "mono" if channels == 1 else "stereo"
        _ffmpeg(
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=44100",
            "-af", f"volume={volume_db}dB,aformat=channel_layouts={layout}", str(path),
        )
        return path

    return make


@pytest.fixture
def make_image():
    """make_image(path, width, height, color) -> path (a flat-colour PNG)"""

    def make(path: Path, width: int, height: int, color: str = "0x334455") -> Path:
        _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}", "-frames:v", "1", str(path))
        return path

    return make


@pytest.fixture
def make_video():
    """make_video(path, width, height, seconds=2, fps=15) -> path (an mp4 test pattern)"""

    def make(path: Path, width: int, height: int, seconds: float = 2.0, fps: int = 15) -> Path:
        _ffmpeg(
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
            "-pix_fmt", "yuv420p", str(path),
        )
        return path

    return make
