"""Generate a script from a topic via the Anthropic API, or signal that the
job needs a hand-written script when no API key is configured ("manual mode").
"""

from __future__ import annotations

import os
import re

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
def _call_anthropic(client, model: str, system_prompt: str, user_message: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def generate_script(
    topic: str, language: str = "en", max_words: int = 110, model: str = "claude-sonnet-5"
) -> str:
    """Generate a script for `topic` via the Anthropic API.

    Raises ManualModeRequired if ANTHROPIC_API_KEY is not set — the caller
    (pipeline.py) should then stop the job and ask the user to drop a
    hand-written script into output/<job_slug>/script.txt and re-run with
    --resume.

    Retries once with a "shorten it" instruction if the first draft comes
    back over max_words.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ManualModeRequired(
            "ANTHROPIC_API_KEY not set — write your script by hand into "
            "output/<job_slug>/script.txt and re-run with --resume."
        )

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(max_words=max_words, language=language)

    raw = _call_anthropic(client, model, system_prompt, topic)
    script = _clean_script(raw)

    if _word_count(script) > max_words:
        log.info("draft was %d words (limit %d) — asking for a shorter rewrite", _word_count(script), max_words)
        shorten_prompt = (
            f"{topic}\n\n(Bản trước dài {_word_count(script)} từ, vượt giới hạn {max_words} từ. "
            f"Viết lại NGẮN HƠN, tối đa {max_words} từ, giữ đúng cấu trúc.)"
        )
        raw = _call_anthropic(client, model, system_prompt, shorten_prompt)
        script = _clean_script(raw)

    if _word_count(script) > max_words:
        log.warning("script still %d words after retry (limit %d) — using as-is", _word_count(script), max_words)

    return script


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    try:
        script = generate_script(
            "Why does silence after a question make people confess more?", language="en", max_words=110
        )
        print(f"[{_word_count(script)} words]\n{script}")
    except ManualModeRequired as e:
        print(f"Manual mode required: {e}")
