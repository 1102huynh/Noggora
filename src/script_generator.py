"""Generate a script from a topic via an LLM API, or signal that the job
needs a hand-written script when no provider is configured ("manual mode").

Two providers are supported:
- anthropic: paid, needs ANTHROPIC_API_KEY.
- groq: free (generous rate limits on their hosted open-weight models,
  e.g. Llama 3.3 70B), needs GROQ_API_KEY from console.groq.com. Same
  OpenAI-compatible chat-completions shape, called via `requests` directly
  so this doesn't need an extra SDK dependency.

`script.provider` in settings.yaml picks between them explicitly, or leave
it "auto" to use whichever key is set (ANTHROPIC_API_KEY takes priority
since it was the original default).
"""

from __future__ import annotations

import os
import re

import requests

from src.utils import ManualModeRequired, get_logger, retry_network

log = get_logger("script_generator")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_NICHE = "tâm lý học / hành vi con người"

SYSTEM_PROMPT_TEMPLATE = (
    "Bạn là copywriter viết lời thoại cho video ngắn chủ đề {niche}. (Kênh đăng video "
    "tên Noggora — đây chỉ là thông tin nội bộ, TUYỆT ĐỐI không nhắc tên kênh hay chữ "
    "\"Noggora\" trong lời thoại.) Viết script {max_words} từ, ngôn ngữ {language}, giọng "
    "gần gũi không hàn lâm, bám sát đúng chủ đề người dùng đưa ra — không lái sang chủ "
    "đề khác. Cấu trúc bắt buộc: câu 1 là hook gây tò mò hoặc nghịch lý; đoạn giữa là 1 "
    "sự thật/insight về đúng chủ đề đó; câu cuối là 1 hành động/góc nhìn người xem áp "
    "dụng hoặc suy ngẫm được ngay.\n\n"
    "QUAN TRỌNG — chỉ dùng sự thật có thật, được biết đến rộng rãi và bạn tin chắc là "
    "đúng. TUYỆT ĐỐI không bịa đặt tên riêng, số liệu, sự kiện, nhân vật hay chi tiết "
    "lịch sử/khoa học không có thật để nghe cho \"hấp dẫn\" hơn — nếu không chắc chắn về "
    "một chi tiết cụ thể, hãy nói khái quát hơn thay vì bịa ra chi tiết giả. "
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
def _call_anthropic(model: str, system_prompt: str, user_message: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())
    response = client.messages.create(
        model=model,
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


@retry_network(max_attempts=2)
def _call_groq(model: str, system_prompt: str, user_message: str) -> str:
    resp = requests.post(
        GROQ_CHAT_URL,
        headers={
            "Authorization": f"Bearer {os.environ['GROQ_API_KEY'].strip()}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            # gpt-oss models on Groq emit hidden "reasoning" tokens before the
            # visible answer and count both against max_tokens. reasoning_effort
            # "low" keeps that budget small, but it's not deterministic — the
            # same prompt can spend anywhere from ~10 to 500+ reasoning tokens,
            # so max_tokens is set generously high (see the empty-content
            # retry in generate_script for the rare case it's not enough).
            "max_tokens": 1500,
            "reasoning_effort": "low",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _resolve_provider(cfg: dict) -> str:
    provider = cfg.get("provider", "auto")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()

    if provider == "auto":
        if anthropic_key:
            return "anthropic"
        if groq_key:
            return "groq"
        raise ManualModeRequired(
            "No script provider configured — set ANTHROPIC_API_KEY (paid) or "
            "GROQ_API_KEY (free, console.groq.com) in .env, or write your "
            "script by hand into output/<job_slug>/script.txt and re-run with --resume."
        )

    if provider == "anthropic" and not anthropic_key:
        raise ManualModeRequired("script.provider is 'anthropic' but ANTHROPIC_API_KEY is not set.")
    if provider == "groq" and not groq_key:
        raise ManualModeRequired("script.provider is 'groq' but GROQ_API_KEY is not set.")
    return provider


def generate_script(topic: str, language: str = "en", cfg: dict | None = None, niche: str | None = None) -> str:
    """Generate a script for `topic` via the configured LLM provider.

    `cfg` is the `script:` block of settings.yaml (provider, anthropic_model,
    groq_model, max_words). `niche` describes the channel/content domain
    (default: Noggora's psychology niche) — pass the topic category's label
    for off-niche one-off videos so the model doesn't default back to
    psychology framing regardless of what topic it's given.

    Raises ManualModeRequired if no provider is usable — the caller
    (pipeline.py) should then stop the job and ask the user to drop a
    hand-written script into output/<job_slug>/script.txt and re-run with
    --resume.

    Retries once with a "shorten it" instruction if the first draft comes
    back over max_words.
    """
    cfg = cfg or {}
    max_words = cfg.get("max_words", 110)
    provider = _resolve_provider(cfg)

    if provider == "anthropic":
        model = cfg.get("anthropic_model", "claude-sonnet-5")
        call = lambda user_message: _call_anthropic(model, system_prompt, user_message)  # noqa: E731
    else:
        model = cfg.get("groq_model", "openai/gpt-oss-120b")
        call = lambda user_message: _call_groq(model, system_prompt, user_message)  # noqa: E731

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        max_words=max_words, language=language, niche=niche or DEFAULT_NICHE,
    )

    raw = call(topic)
    script = _clean_script(raw)

    for attempt in range(2):
        if script:
            break
        log.warning("empty script from provider (attempt %d) — retrying", attempt + 1)
        raw = call(topic)
        script = _clean_script(raw)

    if not script:
        raise RuntimeError(f"{provider} returned an empty script after retries")

    if _word_count(script) > max_words:
        log.info("draft was %d words (limit %d) — asking for a shorter rewrite", _word_count(script), max_words)
        shorten_prompt = (
            f"{topic}\n\n(Bản trước dài {_word_count(script)} từ, vượt giới hạn {max_words} từ. "
            f"Viết lại NGẮN HƠN, tối đa {max_words} từ, giữ đúng cấu trúc.)"
        )
        raw = call(shorten_prompt)
        script = _clean_script(raw)

    if _word_count(script) > max_words:
        log.warning("script still %d words after retry (limit %d) — using as-is", _word_count(script), max_words)

    return script


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    try:
        script = generate_script(
            "Why does silence after a question make people confess more?",
            language="en", cfg={"provider": "auto", "max_words": 110},
        )
        print(f"[{_word_count(script)} words]\n{script}")
    except ManualModeRequired as e:
        print(f"Manual mode required: {e}")
