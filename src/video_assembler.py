"""Ghép clips/images + voice audio + burned-in ASS subtitle + background music
into the final vertical mp4, using ffmpeg via subprocess (no moviepy — avoids
a heavy extra dependency for something a single ffmpeg command already does).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.utils import ffprobe_duration, get_logger

log = get_logger("video_assembler")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for safe use inside an ffmpeg -filter_complex
    string (needed for the `ass=` subtitle filter). Forward slashes avoid
    backslash-escaping headaches on Windows; the drive-letter colon still
    needs escaping because ':' is a filter-argument separator."""
    posix = str(path.resolve()).replace("\\", "/")
    return posix.replace(":", "\\:")


def assemble_video(
    clips: list[Path],
    audio_path: Path,
    ass_subtitle_path: Path,
    music_path: Path | None,
    out_path: Path,
    cfg: dict,
) -> Path:
    """Concat `clips` (video or still image) to cover the voice track's
    duration, burn in `ass_subtitle_path`, mix in optional background music,
    and export H.264/AAC at cfg['video'] width/height/fps. Returns out_path.
    """
    if not clips:
        raise ValueError("assemble_video requires at least one clip")

    video_cfg = cfg["video"]
    width, height, fps = video_cfg["width"], video_cfg["height"], video_cfg["fps"]

    audio_duration = ffprobe_duration(audio_path)
    per_clip_duration = audio_duration / len(clips)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = ["ffmpeg", "-y"]

    # --- inputs: one per clip, each padded/looped to exactly per_clip_duration ---
    for clip in clips:
        if clip.suffix.lower() in _IMAGE_EXTS:
            args += ["-loop", "1", "-framerate", str(fps), "-t", f"{per_clip_duration:.3f}", "-i", str(clip)]
        else:
            args += ["-stream_loop", "-1", "-t", f"{per_clip_duration:.3f}", "-i", str(clip)]
    voice_idx = len(clips)
    args += ["-i", str(audio_path)]

    music_idx = None
    if music_path is not None:
        music_idx = voice_idx + 1
        args += ["-stream_loop", "-1", "-t", f"{audio_duration:.3f}", "-i", str(music_path)]

    # --- video filter chain: scale+crop each clip to frame, concat, burn subtitle ---
    filter_parts = []
    for i in range(len(clips)):
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}[v{i}]"
        )
    concat_inputs = "".join(f"[v{i}]" for i in range(len(clips)))
    filter_parts.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=0[vconcat]")

    ass_escaped = _escape_ffmpeg_filter_path(ass_subtitle_path)
    filter_parts.append(f"[vconcat]ass='{ass_escaped}'[vout]")

    # --- audio: voice always; mix in music (with fade in/out) if provided ---
    if music_idx is not None:
        music_cfg = cfg.get("music", {})
        volume_db = music_cfg.get("volume_db", -22)
        fade_out_start = max(audio_duration - 1.0, 0.0)
        filter_parts.append(f"[{voice_idx}:a]volume=1.0[voice_a]")
        filter_parts.append(
            f"[{music_idx}:a]volume={volume_db}dB,"
            f"afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start:.3f}:d=1[music_a]"
        )
        filter_parts.append("[voice_a][music_a]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        audio_map = "[aout]"
    else:
        audio_map = f"{voice_idx}:a"

    filter_complex = ";".join(filter_parts)

    args += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", audio_map,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-r", str(fps),
        "-t", f"{audio_duration:.3f}",
        "-movflags", "+faststart",
        str(out_path),
    ]

    log.info("running ffmpeg: %d clip(s), %.1fs target duration -> %s", len(clips), audio_duration, out_path)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}):\n{result.stderr[-4000:]}")

    final_duration = ffprobe_duration(out_path)
    if abs(final_duration - audio_duration) > 0.5:
        log.warning(
            "final video duration (%.2fs) drifts from audio (%.2fs) by > 0.5s",
            final_duration, audio_duration,
        )
    log.info("video assembled: %s (%.1fs)", out_path, final_duration)
    return out_path


if __name__ == "__main__":
    from src.utils import load_config

    voice_dir = Path("output") / "_selftest_voice"
    audio = voice_dir / "voice.mp3"
    ass_path = voice_dir / "voice.ass"
    if not audio.exists() or not ass_path.exists():
        raise SystemExit(
            "Run `python -m src.voice_generator` then `python -m src.subtitle_burner` first."
        )

    fake_dir = Path("output") / "_selftest_visuals"
    fake_dir.mkdir(parents=True, exist_ok=True)
    fake_clips = []
    for i, color in enumerate(["0x1a1a2e", "0x16213e", "0x0f3460"]):
        img = fake_dir / f"fake_{i}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920", "-frames:v", "1", str(img)],
            check=True, capture_output=True,
        )
        fake_clips.append(img)

    cfg = load_config()
    out = assemble_video(fake_clips, audio, ass_path, None, voice_dir / "final.mp4", cfg)
    print(f"final video: {out}")
