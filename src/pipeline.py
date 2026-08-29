"""Orchestrator: runs the 6-step chain (script -> voice -> visuals -> subtitle
-> assemble -> log) for a single topic. Each step is wrapped in its own
try/except so a failure is attributed to exactly one step and never crashes
a batch run of many topics (see main.py).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src import music_composer, script_generator, subtitle_burner, video_assembler, visual_fetcher, voice_generator
from src.utils import ManualModeRequired, PipelineStepError, get_logger, new_job_slug

log = get_logger("pipeline")

# Written into a fresh script.txt when manual mode kicks in — used both as
# user-facing instructions and as the marker resume-detection checks for.
PLACEHOLDER_MARKER = "# PASTE YOUR SCRIPT BELOW THIS LINE"


@dataclass
class JobResult:
    status: str  # "done" | "failed" | "awaiting_manual_script"
    out_dir: Path
    final_video: Path | None = None
    failed_step: str | None = None
    error: str | None = None
    title: str | None = None  # set when status == "done" — ready to paste as the YouTube title


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_job_log(out_dir: Path, log_data: dict) -> None:
    (out_dir / "job_log.json").write_text(json.dumps(log_data, indent=2, ensure_ascii=False), encoding="utf-8")


def _has_usable_script(script_path: Path) -> bool:
    if not script_path.exists():
        return False
    text = script_path.read_text(encoding="utf-8").strip()
    return bool(text) and PLACEHOLDER_MARKER not in text


def _write_placeholder_script(script_path: Path, out_dir: Path) -> None:
    script_path.write_text(
        f"{PLACEHOLDER_MARKER}\n"
        f"# ANTHROPIC_API_KEY is not set, so this job needs a hand-written script.\n"
        f"# 1) Delete these comment lines (or the whole file) and paste your script text.\n"
        f'# 2) Re-run:  python main.py single --resume "{out_dir}"\n',
        encoding="utf-8",
    )


def _voice_for_language(language: str, cfg: dict) -> str:
    voice_cfg = cfg["voice"]
    provider = voice_cfg.get("provider", "edge_tts")
    prefix = "elevenlabs_voice" if provider == "elevenlabs" else "edge_voice"
    if language == "vi":
        return voice_cfg[f"{prefix}_vi"]
    if language != "en":
        log.warning("no configured voice for language=%r, defaulting to %s_en", language, prefix)
    return voice_cfg[f"{prefix}_en"]


def _pick_music(topic: str, script: str, cfg: dict) -> Path | None:
    """Mood-matched, fully-synthesized background music (see music_composer) —
    picked per topic, zero copyright risk since nothing is sourced online."""
    music_dir = Path(cfg.get("music", {}).get("dir", "data/assets_local/music"))
    try:
        return music_composer.get_music_for_topic(topic, script, music_dir / "generated")
    except Exception as e:
        log.warning("music synthesis failed (%s) — continuing without background music", e)
        return None


def run_job(topic: str, language: str, cfg: dict, out_dir: Path | None = None) -> JobResult:
    """Run the full pipeline for one topic.

    Pass `out_dir` to resume a job previously stopped at
    "awaiting_manual_script" (after you've edited its script.txt by hand).
    Otherwise a fresh output/<slug>-<timestamp>/ directory is created.
    """
    resuming = out_dir is not None
    if out_dir is None:
        out_dir = Path("output") / new_job_slug(topic)
    out_dir.mkdir(parents=True, exist_ok=True)

    log_data: dict = {
        "topic": topic, "language": language, "resumed": resuming,
        "started_at": _now_iso(), "steps": {},
    }

    def fail(step: str, exc: Exception) -> JobResult:
        err = PipelineStepError(step, exc)
        log.error(str(err))
        log_data["steps"][step] = "failed"
        log_data["error"] = str(err)
        log_data["finished_at"] = _now_iso()
        log_data["status"] = "failed"
        _write_job_log(out_dir, log_data)
        return JobResult(status="failed", out_dir=out_dir, failed_step=step, error=str(err))

    # 1. script
    script_path = out_dir / "script.txt"
    try:
        if _has_usable_script(script_path):
            script = script_path.read_text(encoding="utf-8").strip()
            log.info("using existing script.txt (%d words)", len(script.split()))
        else:
            script = script_generator.generate_script(
                topic, language=language,
                max_words=cfg["script"]["max_words"], model=cfg["script"]["anthropic_model"],
            )
            script_path.write_text(script, encoding="utf-8")
        log_data["steps"]["script"] = "ok"
        log_data["script_word_count"] = len(script.split())
        log_data["script_char_count"] = len(script)
    except ManualModeRequired as e:
        _write_placeholder_script(script_path, out_dir)
        log_data["steps"]["script"] = "awaiting_manual_script"
        log_data["finished_at"] = _now_iso()
        log_data["status"] = "awaiting_manual_script"
        _write_job_log(out_dir, log_data)
        log.warning("%s — edit %s then re-run with --resume", e, script_path)
        return JobResult(status="awaiting_manual_script", out_dir=out_dir)
    except Exception as e:
        return fail("script", e)

    # 2. voice
    try:
        voice_path, srt_path = asyncio.run(
            voice_generator.generate_voice(script, _voice_for_language(language, cfg), out_dir, cfg)
        )
        log_data["steps"]["voice"] = "ok"
    except Exception as e:
        return fail("voice", e)

    # 3. visuals
    try:
        clips = visual_fetcher.fetch_visuals(topic, script, out_dir, cfg["visuals"]["clips_per_video"], cfg, language=language)
        log_data["steps"]["visuals"] = "ok"
        log_data["clip_count"] = len(clips)
    except Exception as e:
        return fail("visuals", e)

    # 4. subtitle
    try:
        ass_path = subtitle_burner.srt_to_ass(
            srt_path, cfg["subtitle"], out_dir / "voice.ass",
            video_width=cfg["video"]["width"], video_height=cfg["video"]["height"],
        )
        log_data["steps"]["subtitle"] = "ok"
    except Exception as e:
        return fail("subtitle", e)

    # 5. assemble
    try:
        music_path = _pick_music(topic, script, cfg)
        final_path = video_assembler.assemble_video(
            clips, voice_path, ass_path, music_path, out_dir / "final.mp4", cfg
        )
        log_data["steps"]["assemble"] = "ok"
        log_data["music_used"] = str(music_path) if music_path else None
    except Exception as e:
        return fail("assemble", e)

    # 6. log
    log_data["finished_at"] = _now_iso()
    log_data["status"] = "done"
    log_data["final_video"] = str(final_path)
    _write_job_log(out_dir, log_data)

    # The topic is already phrased as a hook question, which is exactly what
    # works as a YouTube Shorts/TikTok title — write it out next to the video
    # so it's there to copy-paste when posting, without having to scroll back
    # through terminal output or re-open script.txt.
    title_path = out_dir / "title.txt"
    title_path.write_text(topic, encoding="utf-8")

    return JobResult(status="done", out_dir=out_dir, final_video=final_path, title=topic)


if __name__ == "__main__":
    from dotenv import load_dotenv

    from src.utils import check_ffmpeg, load_config

    load_dotenv()
    check_ffmpeg()
    cfg = load_config()
    result = run_job(
        "Why does silence after a question make people confess more?", "en", cfg
    )
    print(result)
