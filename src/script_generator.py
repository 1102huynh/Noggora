"""Generate a script from a topic (via the Claude Code CLI or the Anthropic API,
see src/llm.py), or signal that the job needs a hand-written script when
neither is available ("manual mode").
"""

from __future__ import annotations

import json
import re

from src import llm
from src.utils import ManualModeRequired, get_logger, retry_network

log = get_logger("script_generator")

SYSTEM_PROMPT_TEMPLATE = (
    "Bạn là copywriter cho kênh short-video tâm lý học tên Noggora. Viết script "
    "{max_words} từ, ngôn ngữ {language}, giọng gần gũi không hàn lâm. Cấu trúc "
    "bắt buộc: câu 1 là hook gây tò mò hoặc nghịch lý; đoạn giữa là 1 sự thật/insight "
    "tâm lý học có căn cứ; câu cuối là 1 hành động/góc nhìn người xem áp dụng được ngay. "
    "Không thêm tiêu đề, không thêm hashtag, không markdown, chỉ trả về đúng phần lời "
    "thoại sẽ được đọc."
)

# Shared with daily_topic (which asks for the description in the same call as the script).
DESCRIPTION_RULES = (
    "Format: 2-3 short lines: (1) a line that restates the hook so it stops the scroll, (2) one line with the "
    "takeaway from the video, (3) a short question that invites comments. Then a blank line, then 6-8 "
    "hashtags on ONE line: broad ones (#psychology #psychologyfacts #mindset) plus 2-3 specific to this video's "
    "effect and situation, and #shorts. Under 350 characters in total. Plain text, no markdown, no emojis, no "
    "claims of curing or diagnosing anything."
)

_DESCRIPTION_SYSTEM = (
    "You write the caption for a short psychology video posted on TikTok, YouTube Shorts and Instagram Reels "
    "by the channel Noggora. " + DESCRIPTION_RULES + " Reply with the caption only."
)

_MARKDOWN_CHARS = re.compile(r"[*_#`~]")


def _clean_script(text: str) -> str:
    """Strip stray markdown/quote wrapping the model sometimes adds anyway."""
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    text = _MARKDOWN_CHARS.sub("", text)
    return text.strip()


def _word_count(text: str) -> int:
    return len(text.split())


@retry_network(max_attempts=2)
def _ask(system_prompt: str, user_message: str, cfg: dict) -> str:
    return llm.complete(system_prompt, user_message, cfg, max_tokens=600)


def generate_script(topic: str, cfg: dict, language: str = "en") -> str:
    """Generate a script for `topic`.

    Raises ManualModeRequired if no LLM backend is available — the caller
    (pipeline.py) should then stop the job and ask the user to drop a
    hand-written script into output/<job_slug>/script.txt and re-run with
    --resume.

    Retries once with a "shorten it" instruction if the first draft comes
    back over script.max_words.
    """
    max_words = cfg["script"]["max_words"]
    if llm.backend(cfg) is None:
        raise ManualModeRequired(
            "no LLM backend (no ANTHROPIC_API_KEY and no `claude` CLI, or script.provider=manual) — "
            "write your script by hand into output/<job_slug>/script.txt and re-run with --resume."
        )

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(max_words=max_words, language=language)
    try:
        script = _clean_script(_ask(system_prompt, topic, cfg))

        if _word_count(script) > max_words:
            log.info("draft was %d words (limit %d) — asking for a shorter rewrite", _word_count(script), max_words)
            shorten_prompt = (
                f"{topic}\n\n(Bản trước dài {_word_count(script)} từ, vượt giới hạn {max_words} từ. "
                f"Viết lại NGẮN HƠN, tối đa {max_words} từ, giữ đúng cấu trúc.)"
            )
            script = _clean_script(_ask(system_prompt, shorten_prompt, cfg))
    except llm.LLMUnavailable as e:
        raise ManualModeRequired(f"LLM call failed ({e}) — write your script by hand instead.") from e

    if _word_count(script) > max_words:
        log.warning("script still %d words after retry (limit %d) — using as-is", _word_count(script), max_words)

    check = fact_check(topic, script, "", cfg)
    if check["verdict"] == "revised":
        log.info("fact-check revised the script: %s", "; ".join(check["issues"]) or "(no notes)")
        script = check["script"]

    return script


_FACTCHECK_SYSTEM = """You are a careful fact-checker for popular-psychology short videos. You receive a video's title, its spoken script and its post caption. Check every claim against well-established findings.

- Solid, well-supported claims: keep them exactly.
- Claims that are contested, oversimplified, or where the mechanism is still debated (competing explanations, failed replications, effects that shrink under scrutiny): rewrite just that sentence so it is accurate but still simple and spoken, for example "one explanation is", "researchers still debate why", "in some studies".
- Any study, number or researcher you cannot confirm: remove it or make it general. Never add new studies or statistics.
- Keep the structure (hook, effect plus example, something to do), the conversational tone, plain sentences of 6-25 words ending in periods or question marks, numbers spelled out, and the length close to the original and NEVER above MAX_WORDS words (the video has a hard time limit). The title and the effect stay the same.
- If the caption repeats a contested claim, fix it too, keeping its format (short lines, blank line, hashtags on one line).

Reply with ONE JSON object and nothing else:
{"verdict": "ok" or "revised", "issues": ["one short note per problem you found; empty if none"], "script": "...", "description": "..."}
If the verdict is "ok", return the script and description unchanged."""


def fact_check(topic: str, script: str, description: str, cfg: dict) -> dict:
    """Second-opinion pass over a generated script (and caption).

    Returns {"verdict": "ok" | "revised" | "skipped", "issues": [...], "script", "description"}.
    "skipped" (disabled, no backend, call failed, or an unusable answer) always
    carries the ORIGINAL script/description — a broken check must never break or
    corrupt the video.
    """
    original = {"verdict": "skipped", "issues": [], "script": script, "description": description}
    if not cfg["script"].get("fact_check", True) or llm.backend(cfg) is None:
        return original
    max_words = cfg["script"]["max_words"]
    system = _FACTCHECK_SYSTEM.replace("MAX_WORDS", str(max_words))
    user = f"Title: {topic}\n\nScript:\n{script}\n\nCaption:\n{description or '(none)'}"
    try:
        text = _ask(system, user, cfg)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in the answer")
        data = json.loads(match.group(0))
        verdict = "revised" if str(data.get("verdict", "")).lower() == "revised" else "ok"
        issues = [str(i).strip() for i in (data.get("issues") or []) if str(i).strip()]
        if verdict == "ok":
            return {"verdict": "ok", "issues": issues, "script": script, "description": description}

        new_script = _clean_script(str(data.get("script", "")))
        new_desc = str(data.get("description", "")).replace("\r", "").strip() or description
        old_words, new_words = _word_count(script), _word_count(new_script)
        if not new_script or not (0.75 * old_words <= new_words <= 1.25 * old_words) or new_words > max_words:
            raise ValueError(
                f"revised script has {new_words} words vs {old_words} (limit {max_words}) — keeping the original"
            )
        if not new_script.rstrip().endswith((".", "!", "?")):
            raise ValueError("revised script does not end with sentence punctuation")
        return {"verdict": "revised", "issues": issues, "script": new_script, "description": new_desc}
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as e:
        log.warning("fact-check skipped (%s) — keeping the script as written", e)
        return original


def generate_description(topic: str, script: str, cfg: dict) -> str | None:
    """Post caption (text + hashtags) for a finished script, or None if no LLM
    backend is available or the call fails — the video is still fine without it."""
    if llm.backend(cfg) is None:
        return None
    try:
        text = _ask(_DESCRIPTION_SYSTEM, f"Video title: {topic}\n\nVideo script:\n{script}", cfg).strip()
    except llm.LLMUnavailable as e:
        log.warning("could not generate a post description (%s)", e)
        return None
    return text.strip('"').strip() or None


if __name__ == "__main__":
    from dotenv import load_dotenv

    from src.utils import load_config

    load_dotenv()
    try:
        script = generate_script("Why does silence after a question make people confess more?", load_config())
        print(f"[{_word_count(script)} words]\n{script}")
    except ManualModeRequired as e:
        print(f"Manual mode required: {e}")
