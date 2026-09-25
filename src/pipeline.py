"""Orchestrator: runs the 6-step chain (script -> voice -> visuals -> subtitle
-> assemble -> log) for a single topic. Each step is wrapped in its own
try/except so a failure is attributed to exactly one step and never crashes
a batch run of many topics (see main.py).
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src import music_composer, script_generator, subtitle_burner, video_assembler, visual_fetcher, voice_generator
from src import categories, post_text, translate
from src import scenes as scenes_mod
from src.utils import ManualModeRequired, PipelineStepError, ffprobe_duration, get_logger, new_job_slug

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
    description: str | None = None  # post caption + hashtags, ready to paste (None if it couldn't be generated)
    cover: Path | None = None  # cover/thumbnail image (None if it couldn't be made)
    title_vi: str | None = None  # Vietnamese title / caption, when the captions were translated
    description_vi: str | None = None
    youtube_post: Path | None = None  # post.youtube.txt: title + description laid out to paste into YouTube


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
        f"# No script generator is available (no `claude` CLI / ANTHROPIC_API_KEY), so this job needs a hand-written script.\n"
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


def _music_state_path(cfg: dict) -> Path:
    return Path(cfg.get("music", {}).get("state_file", "data/music_state.json"))


def _pick_music(topic: str, script: str, cfg: dict, mood: str | None) -> music_composer.MusicChoice | None:
    """Music for the topic's mood: your next track from data/assets_local/music/<mood>/
    (in file-name order), else a synthesized pad (see music_composer)."""
    music_dir = Path(cfg.get("music", {}).get("dir", "data/assets_local/music"))
    try:
        return music_composer.choose_music(
            topic, script, music_dir / "generated", library_dir=music_dir,
            state_path=_music_state_path(cfg), mood=mood,
        )
    except Exception as e:
        log.warning("music selection failed (%s) — continuing without background music", e)
        return None


def run_job(
    topic: str, language: str, cfg: dict, out_dir: Path | None = None,
    visual_keywords: list[str] | None = None, description: str | None = None, mood: str | None = None,
    category: str | None = None,
) -> JobResult:
    """Run the full pipeline for one topic.

    Pass `out_dir` to resume a job previously stopped at
    "awaiting_manual_script" (after you've edited its script.txt by hand).
    Otherwise a fresh output/<slug>-<timestamp>/ directory is created.
    `visual_keywords` are per-scene B-roll search phrases in narrative order
    (from the topic bank); without them scenes are matched from their own text.
    `description` is the post caption if one was already written alongside the
    script; otherwise one is generated after the video is done.
    `mood` (mysterious|tense|curious|warm|playful) picks the music folder; when
    absent it is derived from the topic's keywords.
    `category` (an id from config content.categories: psychology, science, space,
    world, technology, whatif) shows as the tag on the title card and cover, and
    picks the fallback footage themes.
    """
    resuming = out_dir is not None
    if out_dir is None:
        out_dir = Path("output") / new_job_slug(topic)
    out_dir.mkdir(parents=True, exist_ok=True)
    category_label = categories.label_for(cfg, category)

    log_data: dict = {
        "topic": topic, "language": language, "category": category, "resumed": resuming,
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
            script = script_generator.generate_script(topic, cfg, language=language)
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

    # 2. voice — the question (the title) is read aloud first, then the script
    run_cfg = cfg  # cfg as used for the rest of this run (the title card follows the spoken question)
    try:
        intro = voice_generator.intro_for(topic, script) if cfg.get("voice", {}).get("read_title", True) else None
        voice_path, srt_path = asyncio.run(
            voice_generator.generate_voice(script, _voice_for_language(language, cfg), out_dir, cfg, intro=intro)
        )
        log_data["steps"]["voice"] = "ok"
        intro_path = out_dir / "voice.intro.json"
        if intro_path.exists():
            intro_meta = json.loads(intro_path.read_text(encoding="utf-8"))
            # the title card stays up exactly while the question is spoken (plus a beat), not a fixed 2.8 s
            hook_cfg = {**cfg.get("hook", {}), "title_sec": round(max(intro_meta["end"] + 0.3, 1.5), 2)}
            run_cfg = {**cfg, "hook": hook_cfg}
            log_data["intro"] = {"text": intro_meta["text"], "end": round(intro_meta["end"], 2)}
    except Exception as e:
        return fail("voice", e)

    # 3. visuals — one clip per scene, scenes cut at sentence pauses
    try:
        audio_duration = ffprobe_duration(voice_path)
        visuals_cfg = cfg["visuals"]
        n_scenes = min(
            max(visuals_cfg["clips_per_video"], math.ceil(audio_duration / visuals_cfg.get("max_scene_sec", 8))),
            int(visuals_cfg.get("max_clips", 40)),
        )
        scenes = scenes_mod.plan_scenes(srt_path, audio_duration, n_scenes)
        scene_keywords = None
        if visuals_cfg.get("plan_keywords", True):
            scene_keywords = visual_fetcher.plan_scene_keywords(scenes, topic, category_label, cfg)
        clips = visual_fetcher.fetch_visuals(
            topic, script, out_dir, len(scenes), cfg, language=language,
            scenes=scenes, visual_keywords=visual_keywords, category=category, scene_keywords=scene_keywords,
        )
        log_data["steps"]["visuals"] = "ok"
        log_data["clip_count"] = len(clips)
        log_data["scenes"] = [
            {"start": round(s.start, 2), "end": round(s.end, 2), "clip": c.name,
             "phrase": scene_keywords[i] if scene_keywords else None}
            for i, (s, c) in enumerate(zip(scenes, clips))
        ]
    except Exception as e:
        return fail("visuals", e)

    # 4. subtitle (+ hook title card and closing CTA). subtitle.language: vi burns Vietnamese
    #    captions and a Vietnamese title card over the English narration; English is the fallback
    #    whenever the translation isn't available, so the video always comes out.
    vi = None
    try:
        if cfg["subtitle"].get("language", "en") == "vi":
            if not description:  # the translation covers the post caption too
                description = script_generator.generate_description(topic, script, cfg)
            try:
                vi = translate.build_for_job(
                    out_dir, topic, description, cfg, int(cfg["subtitle"].get("max_words_per_caption_vi", 7)),
                )
            except Exception as e:
                log.warning("Vietnamese captions failed (%s) — using English captions", e)
        sub_cfg = dict(cfg["subtitle"])
        if vi and sub_cfg.get("font_size_vi"):
            sub_cfg["font_size"] = sub_cfg["font_size_vi"]
        ass_path = subtitle_burner.srt_to_ass(
            vi["srt"] if vi else srt_path, sub_cfg, out_dir / "voice.ass",
            video_width=cfg["video"]["width"], video_height=cfg["video"]["height"],
            title=vi["title"] if vi else topic, hook_cfg=run_cfg.get("hook"), duration=audio_duration,
            words_path=vi["words"] if vi else out_dir / "voice.words.json", category_label=category_label,
        )
        log_data["steps"]["subtitle"] = "ok"
        log_data["caption_language"] = "vi" if vi else "en"
    except Exception as e:
        return fail("subtitle", e)

    # 5. assemble
    try:
        music = _pick_music(topic, script, cfg, mood)
        final_path = video_assembler.assemble_video(
            clips, voice_path, ass_path, music.path if music else None, out_dir / "final.mp4", run_cfg,
            durations=[s.duration for s in scenes],
        )
        log_data["steps"]["assemble"] = "ok"
        log_data["music_used"] = str(music.path) if music else None
        log_data["music_mood"] = music.mood if music else None
        log_data["music_source"] = music.source if music else None
        if music:
            music_composer.record_used(music, _music_state_path(cfg))  # only now: a failed render keeps its turn
    except Exception as e:
        return fail("assemble", e)

    # 6. cover image + post text — best effort, the video is already done either way
    cover_path = None
    if cfg.get("cover", {}).get("enabled", True):
        try:
            cover_path = video_assembler.make_cover(
                clips[0], vi["title"] if vi else topic, out_dir / "cover.png", cfg, category_label=category_label,
            )
        except Exception as e:
            log.warning("cover image failed (%s) — continuing without it", e)
    if not description:
        description = script_generator.generate_description(topic, script, cfg)

    # 7. log
    log_data["finished_at"] = _now_iso()
    log_data["status"] = "done"
    log_data["final_video"] = str(final_path)
    log_data["has_description"] = bool(description)
    _write_job_log(out_dir, log_data)

    # The topic is already phrased as a hook question, which is exactly what
    # works as a YouTube Shorts/TikTok title — write it out next to the video,
    # with the caption, so both are there to copy-paste when posting without
    # scrolling back through terminal output or re-opening script.txt.
    (out_dir / "title.txt").write_text(topic, encoding="utf-8")
    post = f"TITLE\n{topic}\n"
    if description:
        (out_dir / "description.txt").write_text(description, encoding="utf-8")
        post += f"\nDESCRIPTION\n{description}\n"
    title_vi = description_vi = None
    if vi:
        title_vi, description_vi = vi["title"], vi["description"] or None
        (out_dir / "title.vi.txt").write_text(title_vi, encoding="utf-8")
        post += f"\nTIÊU ĐỀ (tiếng Việt)\n{title_vi}\n"
        if description_vi:
            (out_dir / "description.vi.txt").write_text(description_vi, encoding="utf-8")
            post += f"\nMÔ TẢ (tiếng Việt)\n{description_vi}\n"
    (out_dir / "post.txt").write_text(post, encoding="utf-8")
    youtube_path = None
    try:  # the paste-ready YouTube layout; decoration — never fail the job over it
        youtube_path = out_dir / "post.youtube.txt"
        youtube_path.write_text(
            post_text.build_youtube_post(topic, description, title_vi, description_vi, cfg.get("post", {}).get("youtube")),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("post.youtube.txt failed (%s) — post.txt still has the text", e)
        youtube_path = None

    return JobResult(
        status="done", out_dir=out_dir, final_video=final_path, title=topic, description=description,
        cover=cover_path, title_vi=title_vi, description_vi=description_vi, youtube_post=youtube_path,
    )


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
