"""Ghép clips/images + voice audio + burned-in ASS subtitle + background music
into the final vertical mp4, using ffmpeg via subprocess (no moviepy — avoids
a heavy extra dependency for something a single ffmpeg command already does).

Look & feel, all done inside one filter graph:
  - each clip stays on screen for its scene's length (cut at sentence pauses),
  - portrait footage gets a slow pan (Ken Burns-style); landscape footage is
    letterboxed over a blurred copy of itself instead of being centre-cropped,
  - clips crossfade into each other,
  - one shared colour grade + vignette makes footage from different sources
    look like the same channel,
  - the opening seconds are dimmed so the title card reads,
  - music sits under the voice and ducks (sidechain) whenever someone speaks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.utils import ffprobe_dimensions, ffprobe_duration, get_logger

log = get_logger("video_assembler")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Bundled so the caption font renders the same on every machine regardless of
# what's actually installed system-wide.
_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# One look for every clip regardless of where it came from: slightly
# desaturated, a touch darker (better caption contrast), cool/purple shadows
# to match the channel's dark palette. (lutrgb, not colorbalance: same tint at
# ~1/25 of the render cost — colorbalance alone added ~8s per 10s of video.)
_GRADE = (
    "eq=contrast=1.06:saturation=0.78:brightness=-0.06,"
    "lutrgb=r='val*0.97':g='val*0.99':b='min(val*1.05+6,255)',"
    "vignette=PI/5"
)


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for safe use inside an ffmpeg -filter_complex
    string (needed for the `ass=` subtitle filter). Forward slashes avoid
    backslash-escaping headaches on Windows; the drive-letter colon still
    needs escaping because ':' is a filter-argument separator."""
    posix = str(path.resolve()).replace("\\", "/")
    return posix.replace(":", "\\:")


def _clip_filter(i: int, clip: Path, width: int, height: int, fps: int, seg_sec: float, zoom: float) -> str:
    """Filter chain turning input i into a width x height, fps-locked, yuv420p
    stream labelled [v{i}]. seg_sec is how long this clip's segment runs (used
    to pace the pan)."""
    src_w, src_h = ffprobe_dimensions(clip)
    head = f"[{i}:v]setpts=PTS-STARTPTS,"
    tail = f"setsar=1,fps={fps},format=yuv420p[v{i}]"

    progress = f"min(t/{seg_sec:.3f},1)"

    if src_w / src_h > 1.2:
        # Landscape: a square window (full width, sharp) drifting slowly across
        # the frame, over a blurred copy of the same footage. A plain 16:9
        # letterbox filled only a third of the screen; centre-cropping to 9:16
        # threw away 70% of the shot.
        return (
            f"{head}split=2[bg{i}][fg{i}];"
            # blur at 1/8 size then scale back up: same soft look, a fraction of the cost
            f"[bg{i}]scale={width // 8}:{height // 8}:force_original_aspect_ratio=increase,"
            f"crop={width // 8}:{height // 8},boxblur=5:2,scale={width}:{height},eq=brightness=-0.12[bb{i}];"
            f"[fg{i}]scale=-2:{width},"
            f"crop={width}:{width}:x='(iw-ow)*(0.35+0.3*{progress})':y=0[ff{i}];"
            f"[bb{i}][ff{i}]overlay=(W-w)/2:(H-h)/2,{tail}"
        )

    big_w = int(round(width * zoom / 2)) * 2
    big_h = int(round(height * zoom / 2)) * 2
    x = f"(iw-ow)*{progress}" if i % 2 == 0 else f"(iw-ow)*(1-{progress})"
    return (
        f"{head}scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:x='{x}':y='(ih-oh)/2',{tail}"
    )


def assemble_video(
    clips: list[Path],
    audio_path: Path,
    ass_subtitle_path: Path,
    music_path: Path | None,
    out_path: Path,
    cfg: dict,
    durations: list[float] | None = None,
) -> Path:
    """Concat `clips` (video or still image) to cover the voice track's
    duration, burn in `ass_subtitle_path`, mix in optional background music,
    and export H.264/AAC at cfg['video'] width/height/fps. Returns out_path.

    `durations` = seconds each clip stays on screen (must sum to ~ the voice
    length; from src.scenes). Default: equal split.
    """
    if not clips:
        raise ValueError("assemble_video requires at least one clip")

    video_cfg = cfg["video"]
    width, height, fps = video_cfg["width"], video_cfg["height"], video_cfg["fps"]
    visuals_cfg = cfg.get("visuals", {})
    fade = float(visuals_cfg.get("fade_sec", 0.25))
    zoom = float(visuals_cfg.get("pan_zoom", 1.12))
    hook_sec = float(cfg.get("hook", {}).get("title_sec", 0)) if cfg.get("hook", {}).get("title_card") else 0.0

    audio_duration = ffprobe_duration(audio_path)
    n = len(clips)
    if durations is None or len(durations) != n:
        durations = [audio_duration / n] * n
    else:  # rescale so the timeline is exactly the voice length
        scale = audio_duration / sum(durations)
        durations = [d * scale for d in durations]
    if n > 1:
        fade = min(fade, min(durations) / 2)
    else:
        fade = 0.0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = ["ffmpeg", "-y"]

    # --- inputs: one per clip, each looped/held for its segment + the overlap the crossfade eats ---
    for i, clip in enumerate(clips):
        seg = durations[i] + (fade if i < n - 1 else 0.0)
        if clip.suffix.lower() in _IMAGE_EXTS:
            args += ["-loop", "1", "-framerate", str(fps), "-t", f"{seg:.3f}", "-i", str(clip)]
        else:
            args += ["-stream_loop", "-1", "-t", f"{seg:.3f}", "-i", str(clip)]
    voice_idx = n
    args += ["-i", str(audio_path)]

    music_idx = None
    if music_path is not None:
        music_idx = voice_idx + 1
        args += ["-stream_loop", "-1", "-t", f"{audio_duration:.3f}", "-i", str(music_path)]

    # --- video: per-clip framing, crossfade chain, shared grade, hook dim, subtitles ---
    filter_parts = [
        _clip_filter(i, clip, width, height, fps, durations[i] + (fade if i < n - 1 else 0.0), zoom)
        for i, clip in enumerate(clips)
    ]
    if n == 1:
        last = "v0"
    else:
        last = "v0"
        elapsed = 0.0
        for k in range(1, n):
            elapsed += durations[k - 1]
            out_label = f"x{k}"
            filter_parts.append(
                f"[{last}][v{k}]xfade=transition=fade:duration={fade:.3f}:offset={elapsed:.3f}[{out_label}]"
            )
            last = out_label

    chain = _GRADE
    if hook_sec > 0:
        chain += f",drawbox=x=0:y=0:w=iw:h=ih:color=black@0.38:t=fill:enable='lt(t,{hook_sec:.2f})'"
    ass_escaped = _escape_ffmpeg_filter_path(ass_subtitle_path)
    fontsdir_escaped = _escape_ffmpeg_filter_path(_FONTS_DIR)
    chain += f",ass='{ass_escaped}':fontsdir='{fontsdir_escaped}'"
    filter_parts.append(f"[{last}]{chain}[vout]")

    # --- audio: voice always; music (faded, and ducked under the voice) if provided ---
    if music_idx is not None:
        music_cfg = cfg.get("music", {})
        volume_db = music_cfg.get("volume_db", -2)
        fade_out_start = max(audio_duration - 1.5, 0.0)
        filter_parts.append(f"[{voice_idx}:a]aresample=44100,asplit=2[voice_a][voice_sc]")
        filter_parts.append(
            f"[{music_idx}:a]aresample=44100,volume={volume_db}dB,"
            f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out_start:.3f}:d=1.5[music_pre]"
        )
        filter_parts.append(
            "[music_pre][voice_sc]sidechaincompress=threshold=0.03:ratio=4:attack=40:release=600[music_a]"
        )
        # normalize=0: amix otherwise divides every input by the input count,
        # which silently halved (-6 dB) the voice. alimiter guards the sum.
        filter_parts.append(
            "[voice_a][music_a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = f"{voice_idx}:a"

    filter_complex = ";".join(filter_parts)

    args += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", audio_map,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-r", str(fps),
        "-t", f"{audio_duration:.3f}",
        "-movflags", "+faststart",
        str(out_path),
    ]

    log.info("running ffmpeg: %d clip(s) %s, %.1fs target duration -> %s",
             n, [round(d, 1) for d in durations], audio_duration, out_path)
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
