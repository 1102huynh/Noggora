"""Vietnamese captions for an English-narrated video.

The audio stays English; the burned-in captions (and the title card) can be Vietnamese for
Vietnamese viewers (subtitle.language: vi). The English word-level timing from the voice track
can't be reused word-for-word, so the translation is done per SENTENCE (a phrase-by-phrase
translation reads as fragments because clause boundaries differ between the languages):

  1. group the English caption phrases back into sentences, each with the time span it is spoken;
  2. Claude translates title + caption + every sentence in one call;
  3. each Vietnamese sentence is re-cut into caption-sized chunks and spread over that sentence's
     time span in proportion to text length, in the same {start, end, words: [...]} structure as
     voice.words.json — so the existing caption / karaoke code renders it unchanged.

Timing inside a sentence is therefore approximate (a second or so at worst); across sentences it
is exact. If the translation fails for any reason the caller keeps the English captions.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

from src import llm
from src.utils import get_logger
from src.voice_generator import _chunk_words, _format_srt_timestamp

log = get_logger("translate")

_SENTENCE_END = (".", "!", "?")

_SYSTEM = """You are a professional English-to-Vietnamese subtitle translator for a short educational video channel (psychology, science, space, world, technology, "what if" questions). The video is narrated in English; the Vietnamese text is shown on screen as subtitles, so it must read fast and natural.

Rules:
- Translate meaning, not word for word: natural, conversational Vietnamese a smart teenager would say aloud. Warm and lively, not stiff or bookish. Address the viewer as "bạn".
- Keep every fact, number and proper noun exact. Write numbers and units the Vietnamese way (1.670 km/h; decimals with a comma).
- Keep each sentence's translation a translation of exactly THAT sentence: never merge two sentences, split one, add or drop information (the timing depends on it). Stay about as long as the English; be concise.
- Keep established Vietnamese names for well-known things (Mặt Trăng, Sao Kim, hiệu ứng ...); keep the English term in parentheses only when Vietnamese viewers would know it by the English name.
- The title is a question: keep it a punchy Vietnamese question. If the English title starts with "What if", the Vietnamese one starts with "Điều gì sẽ xảy ra nếu".
- The post caption: translate its lines the same way, keep its structure (short lines, blank line, hashtags on ONE line), keep the English hashtags and add 2-3 Vietnamese hashtags.

Reply with ONE JSON object and nothing else:
{"title": "...", "description": "...", "sentences": {"1": "...", "2": "..."}}"""


def group_sentences(phrases: list[dict]) -> list[dict]:
    """Merge caption phrases (voice.words.json entries) into sentences:
    [{"text": "...", "start": s, "end": e}, ...] with the time the sentence is spoken."""
    sentences: list[dict] = []
    words: list[str] = []
    start = None
    end = 0.0
    for phrase in phrases:
        if start is None:
            start = phrase["start"]
        end = phrase["end"]
        words.extend(w["t"] for w in phrase["words"])
        if words and words[-1].rstrip("\"')”’").endswith(_SENTENCE_END):
            sentences.append({"text": " ".join(words), "start": start, "end": end})
            words, start = [], None
    if words:  # trailing text without closing punctuation
        sentences.append({"text": " ".join(words), "start": start if start is not None else 0.0, "end": end})
    return sentences


def cache_key(sentences: list[dict], title: str) -> str:
    """Identifies what was translated, so a re-render of the same script reuses the translation."""
    blob = json.dumps([title, [s["text"] for s in sentences]], ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def translate_video(title: str, description: str, sentences: list[dict], cfg: dict) -> dict | None:
    """One Claude call: {"title", "description", "sentences": [vi, ...]} in the order given, or None
    if there is no backend, the call fails, or any sentence / the title comes back missing."""
    if llm.backend(cfg) is None or not sentences:
        return None
    numbered = "\n".join(f"{i + 1}. {s['text']}" for i, s in enumerate(sentences))
    user = f"Title: {title}\n\nPost caption:\n{description or '(none)'}\n\nSentences:\n{numbered}"
    try:
        text = llm.complete(_SYSTEM, user, cfg, max_tokens=6000)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group(0)) if match else None
        if not isinstance(data, dict) or not isinstance(data.get("sentences"), dict):
            raise ValueError("no JSON object with sentences in the answer")
        translated = [str(data["sentences"].get(str(i + 1), "")).strip() for i in range(len(sentences))]
        missing = [i + 1 for i, t in enumerate(translated) if not t]
        if missing:
            raise ValueError(f"sentences missing from the translation: {missing}")
        vi_title = str(data.get("title", "")).strip()
        if not vi_title:
            raise ValueError("title missing from the translation")
        return {
            "title": vi_title,
            "description": str(data.get("description", "")).replace("\r", "").strip(),
            "sentences": translated,
        }
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as e:
        log.warning("Vietnamese translation skipped (%s) — keeping the English captions", e)
        return None


def _spread(words: list[str], start: float, end: float, max_words: int) -> list[dict]:
    """Cut one translated sentence into caption chunks and spread them (and their words) over
    [start, end] in proportion to text length. Chunks of one sentence are contiguous."""
    chunks = _chunk_words(words, max_words)
    weights = [sum(len(w) + 1 for w in c) for c in chunks]
    total = sum(weights) or 1
    span = max(end - start, 0.05)
    phrases, t = [], start
    for chunk, weight in zip(chunks, weights):
        duration = span * weight / total
        word_weights = [len(w) + 1 for w in chunk]
        word_total = sum(word_weights)
        wt, timed = t, []
        for word, ww in zip(chunk, word_weights):
            d = duration * ww / word_total
            timed.append({"t": word, "s": wt, "e": wt + d})
            wt += d
        phrases.append({"start": t, "end": t + duration, "words": timed})
        t += duration
    if phrases:
        phrases[-1]["end"] = end  # absorb float drift so the sentence ends exactly with its audio
        phrases[-1]["words"][-1]["e"] = end
    return phrases


def vietnamese_captions(sentences: list[dict], translated: list[str], max_words: int = 7) -> tuple[str, list[dict]]:
    """(srt_text, phrases) for the translated sentences, timed to the English sentences they
    replace. `phrases` has the same shape as voice.words.json."""
    phrases: list[dict] = []
    for sentence, vi in zip(sentences, translated):
        phrases.extend(_spread(vi.split(), sentence["start"], sentence["end"], max_words))
    blocks = []
    for i, ph in enumerate(phrases, 1):
        text = " ".join(w["t"] for w in ph["words"])
        blocks.append(
            f"{i}\n{_format_srt_timestamp(timedelta(seconds=ph['start']))} --> "
            f"{_format_srt_timestamp(timedelta(seconds=ph['end']))}\n{text}\n"
        )
    return "\n".join(blocks), phrases


def build_for_job(
    out_dir: Path, title: str, description: str | None, cfg: dict, max_words: int = 7,
) -> dict | None:
    """Translate a finished voice track's captions and write voice.vi.srt / voice.vi.words.json.

    Reads voice.words.json (English phrase timing) from `out_dir`. The translation itself is cached
    in translation.vi.json keyed on the sentences + title, so re-rendering the same script (resume,
    a new music/footage pass) doesn't ask Claude again. Returns {"title", "description", "srt",
    "words"} (paths) or None to keep the English captions."""
    words_path = out_dir / "voice.words.json"
    if not words_path.exists():
        return None
    sentences = group_sentences(json.loads(words_path.read_text(encoding="utf-8")))
    key = cache_key(sentences, title)
    cache_path = out_dir / "translation.vi.json"

    translation = None
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("key") == key and len(cached.get("sentences", [])) == len(sentences):
            translation = cached
            log.info("reusing the saved Vietnamese translation")
    if translation is None:
        translation = translate_video(title, description or "", sentences, cfg)
        if translation is None:
            return None
        cache_path.write_text(json.dumps({"key": key, **translation}, ensure_ascii=False, indent=2), encoding="utf-8")

    srt_text, phrases = vietnamese_captions(sentences, translation["sentences"], max_words)
    srt_path = out_dir / "voice.vi.srt"
    vi_words_path = out_dir / "voice.vi.words.json"
    srt_path.write_text(srt_text, encoding="utf-8")
    vi_words_path.write_text(json.dumps(phrases, ensure_ascii=False), encoding="utf-8")
    log.info("Vietnamese captions: %d sentence(s) -> %d caption(s)", len(sentences), len(phrases))
    return {
        "title": translation["title"], "description": translation["description"],
        "srt": srt_path, "words": vi_words_path,
    }
