#!/usr/bin/env python
"""Simple local webapp for the Noggora pipeline: pick a topic category, type
a specific topic + script by hand, pick duration bucket + aspect ratio, hit
Generate, watch the resulting video right in the browser.

Wraps src.pipeline.run_job — same 6-step pipeline the CLI (main.py) drives,
just fed from an HTML form instead of argv/CSV. Runs synchronously in the
request (a video takes well under a minute to build), which is fine for a
single local user; it isn't meant to serve concurrent public traffic.
"""

from __future__ import annotations

import copy
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory

from src import script_generator
from src.pipeline import run_job
from src.utils import ManualModeRequired, check_ffmpeg, get_logger, load_config, new_job_slug

load_dotenv()
log = get_logger("webapp")

OUTPUT_DIR = Path("output").resolve()

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# --- topic category -> English B-roll search keywords -----------------------
# The pipeline's automatic keyword extraction only works for English topics
# (see src/visual_fetcher.py); everything else falls back to a psychology
# niche keyword pool. The category picked in the UI drives keywords_override
# so B-roll actually matches off-niche / non-English topics.
CATEGORIES: dict[str, dict] = {
    "psychology": {
        "label": "Tâm lý học / Hành vi con người",
        "keywords": None,  # use the pipeline's built-in niche pool
    },
    "space": {
        "label": "Khoa học vũ trụ / Thiên văn",
        "keywords": [
            "solar system", "planets space", "galaxy stars", "outer space nebula",
            "milky way night sky", "earth from space", "astronaut space", "sun surface space",
        ],
    },
    "history": {
        "label": "Lịch sử",
        "keywords": [
            "ancient ruins", "old manuscripts", "vintage photographs",
            "historical architecture", "archive footage black and white", "old map",
        ],
    },
    "technology": {
        "label": "Khoa học / Công nghệ",
        "keywords": [
            "technology abstract", "futuristic digital", "science laboratory",
            "computer code screen", "artificial intelligence concept", "innovation technology",
        ],
    },
    "health": {
        "label": "Sức khỏe / Đời sống",
        "keywords": [
            "healthy lifestyle", "fitness exercise", "nature wellness",
            "meditation calm", "healthy food", "morning routine",
        ],
    },
    "other": {
        "label": "Khác (tự nhập từ khóa hình ảnh)",
        "keywords": None,  # relies entirely on the custom keywords field
    },
}

# --- duration target -> min/max_duration_sec (tolerance band) + clip count --
DURATIONS: dict[str, dict] = {
    "30": {"label": "30 giây", "min": 26, "max": 34, "clips": 6},
    "45": {"label": "45 giây", "min": 40, "max": 50, "clips": 8},
    "60": {"label": "60 giây", "min": 54, "max": 66, "clips": 10},
}

# --- aspect ratio -> width/height + subtitle safe margin --------------------
ASPECTS: dict[str, dict] = {
    "tiktok": {
        "label": "TikTok / Shorts / Reels (dọc 9:16)",
        "width": 1080, "height": 1920, "safe_margin_percent": 28,
    },
    "youtube": {
        "label": "YouTube (ngang 16:9)",
        "width": 1920, "height": 1080, "safe_margin_percent": 8,
    },
}

# --- rough spoken pace per language, for sizing AI-generated scripts to a
# target duration. Vietnamese word-splitting counts far more (mostly
# monosyllabic) tokens per second of speech than English — measured from
# real generated jobs: ~3.2-4.1 words/sec for vi, ~2.3 words/sec for en.
WORDS_PER_SEC: dict[str, float] = {"vi": 3.6, "en": 2.3}

# --- voice option -> language + edge-tts voice id + rate/pitch -------------
VOICES: dict[str, dict] = {
    "vi_male": {
        "label": "Nam (Tiếng Việt)", "language": "vi",
        "edge_voice": "vi-VN-NamMinhNeural", "rate": "+8%", "pitch": "-12Hz",
    },
    "vi_female": {
        "label": "Nữ (Tiếng Việt)", "language": "vi",
        "edge_voice": "vi-VN-HoaiMyNeural", "rate": "+0%", "pitch": "+0Hz",
    },
    "en_male": {
        "label": "Nam (Tiếng Anh)", "language": "en",
        "edge_voice": "en-US-GuyNeural", "rate": "+0%", "pitch": "+0Hz",
    },
    "en_female": {
        "label": "Nữ (Tiếng Anh)", "language": "en",
        "edge_voice": "en-US-AriaNeural", "rate": "+0%", "pitch": "+0Hz",
    },
}


def _build_cfg(duration_key: str, aspect_key: str, voice_key: str) -> dict:
    cfg = copy.deepcopy(load_config())
    duration = DURATIONS[duration_key]
    aspect = ASPECTS[aspect_key]
    voice = VOICES[voice_key]
    cfg["video"]["min_duration_sec"] = duration["min"]
    cfg["video"]["max_duration_sec"] = duration["max"]
    cfg["video"]["width"] = aspect["width"]
    cfg["video"]["height"] = aspect["height"]
    cfg["visuals"]["clips_per_video"] = duration["clips"]
    cfg["subtitle"]["safe_margin_percent"] = aspect["safe_margin_percent"]
    lang = voice["language"]
    cfg["voice"][f"edge_voice_{lang}"] = voice["edge_voice"]
    cfg["voice"][f"rate_{lang}"] = voice["rate"]
    cfg["voice"][f"pitch_{lang}"] = voice["pitch"]
    return cfg


@app.route("/")
def index():
    return render_template(
        "index.html",
        categories=CATEGORIES,
        durations=DURATIONS,
        aspects=ASPECTS,
        voices=VOICES,
    )


@app.route("/generate-script", methods=["POST"])
def generate_script_route():
    data = request.get_json(force=True) or {}
    specific_topic = (data.get("topic") or "").strip()
    voice_key = data.get("voice", "")
    category = data.get("category", "")
    duration_key = data.get("duration", "")

    if not specific_topic:
        return jsonify(ok=False, error="Vui lòng điền chủ đề cụ thể trước."), 400
    if voice_key not in VOICES:
        return jsonify(ok=False, error="Vui lòng chọn giọng đọc trước."), 400
    if duration_key not in DURATIONS:
        return jsonify(ok=False, error="Vui lòng chọn thời lượng video trước."), 400

    language = VOICES[voice_key]["language"]
    niche = CATEGORIES.get(category, {}).get("label", "").split(" (")[0] or None
    target_sec = int(duration_key)
    script_cfg = dict(load_config()["script"])
    script_cfg["max_words"] = round(WORDS_PER_SEC[language] * target_sec)

    try:
        script = script_generator.generate_script(
            specific_topic, language=language, cfg=script_cfg, niche=niche,
        )
    except ManualModeRequired as e:
        return jsonify(ok=False, error=str(e)), 400
    except Exception as e:
        return jsonify(ok=False, error=f"Sinh script thất bại: {e}"), 500

    return jsonify(ok=True, script=script)


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True) or {}

    category = data.get("category", "")
    specific_topic = (data.get("topic") or "").strip()
    script = (data.get("script") or "").strip()
    voice_key = data.get("voice", "")
    duration_key = data.get("duration", "")
    aspect_key = data.get("aspect", "")
    custom_keywords = (data.get("keywords") or "").strip()

    if category not in CATEGORIES:
        return jsonify(ok=False, error="Vui lòng chọn chủ đề (topic)."), 400
    if not specific_topic:
        return jsonify(ok=False, error="Vui lòng điền chủ đề cụ thể."), 400
    if not script:
        return jsonify(ok=False, error="Vui lòng điền script mô tả chủ đề."), 400
    if voice_key not in VOICES:
        return jsonify(ok=False, error="Vui lòng chọn giọng đọc."), 400
    if duration_key not in DURATIONS:
        return jsonify(ok=False, error="Vui lòng chọn thời lượng video."), 400
    if aspect_key not in ASPECTS:
        return jsonify(ok=False, error="Vui lòng chọn tỉ lệ khung hình."), 400

    if custom_keywords:
        visual_keywords = [k.strip() for k in custom_keywords.split(",") if k.strip()]
    else:
        visual_keywords = CATEGORIES[category]["keywords"]

    language = VOICES[voice_key]["language"]
    cfg = _build_cfg(duration_key, aspect_key, voice_key)

    out_dir = OUTPUT_DIR / new_job_slug(specific_topic)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script.txt").write_text(script, encoding="utf-8")

    log.info("web job: topic=%r category=%s voice=%s duration=%s aspect=%s -> %s",
              specific_topic, category, voice_key, duration_key, aspect_key, out_dir)

    result = run_job(specific_topic, language, cfg, out_dir=out_dir, visual_keywords=visual_keywords)

    if result.status != "done":
        return jsonify(
            ok=False,
            error=f'Lỗi ở bước "{result.failed_step}": {result.error}' if result.failed_step else str(result.error),
            job_dir=str(result.out_dir),
        ), 500

    video_url = f"/videos/{result.out_dir.name}/final.mp4"
    return jsonify(ok=True, video_url=video_url, job_dir=str(result.out_dir))


@app.route("/videos/<path:filename>")
def videos(filename: str):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    check_ffmpeg()
    app.run(host="127.0.0.1", port=5050, debug=False)
