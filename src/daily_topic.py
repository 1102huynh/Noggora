"""Write today's video from scratch: ask an LLM (the Claude Code CLI or the
Anthropic API — see src/llm.py) for one brand-new psychology topic, its script
and per-scene stock-footage search phrases, instead of drawing from a
pre-generated batch.

Every answer is checked against everything already made (the used archive and
the pre-written bank) before it is accepted: same topic, same underlying
effect, or a topic/script that reads like an existing one is rejected and the
model is asked again, told exactly what it collided with. Returns None when no
acceptable answer comes back (or no backend is available), so the caller can
fall back to the pre-written bank.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import date
from pathlib import Path

from src import llm, script_generator, topic_bank
from src.utils import get_logger

log = get_logger("daily_topic")

# Rotated through by day so consecutive videos don't all come from the same corner of psychology.
_AREAS = [
    "cognitive biases and mental shortcuts",
    "memory and attention",
    "social influence and group behavior",
    "decision making and money psychology",
    "emotions and mood",
    "motivation, habits and procrastination",
    "persuasion and everyday manipulation tactics",
    "self-perception and confidence",
    "relationships and trust",
    "perception and illusions",
]

_SYSTEM_TEMPLATE = """You write scripts for "Noggora", a faceless psychology short-video channel (TikTok / YouTube Shorts / Reels). Videos are 30-40 seconds, read aloud by a text-to-speech voice, with burned-in captions.

Produce ONE new video. Reply with a single JSON object and nothing else (no markdown fences, no commentary):
{{"effect": "...", "topic": "...", "script": "...", "description": "...", "visual_keywords": ["...", "...", "...", "...", "..."]}}

effect: the name of the psychological effect, bias or principle the video is about, as psychologists name it (for example "sunk cost fallacy").

topic: the video's title, phrased as a curiosity question addressed to the viewer, under 75 characters (for example "Why do you keep watching a bad movie to the end?").

script: English, {min_words}-{max_words} words, spoken and conversational, not academic. Structure:
  1. Sentence one is a hook: a curiosity gap or a paradox the viewer recognises from their own life.
  2. The middle names the effect and explains it with one concrete everyday example.
  3. The last sentence gives the viewer something they can do or a new way to see it right away.
Rules: plain sentences of 6-25 words, ending in periods or question marks, with commas where a speaker would pause. Spell numbers out as words. No emojis, hashtags, markdown, stage directions, or headings. Only well-established findings: never invent a study, statistic or researcher; if you are unsure of the details of a study, describe the effect in general terms instead.

description: the caption to paste under the post on TikTok / YouTube Shorts / Instagram Reels, in English. {description_rules}

visual_keywords: exactly 5 short stock-footage search phrases (2-4 words each, plain English) for Pexels/Pixabay, one per part of the script in order (hook, explanation, example, insight, action). Each must describe something concrete a camera can film (people, places, objects), never an abstract concept, and never a person's name or a brand.

Every video must cover a DIFFERENT effect and a different everyday situation from all earlier ones. Never repeat, re-word, or write a second take on an earlier video. Earlier videos (topic, and effect where known):
{exclude}"""

# --- duplicate detection -----------------------------------------------------

_STOPWORDS = {
    "the", "and", "that", "this", "with", "your", "you", "why", "does", "do", "for", "are", "was", "were",
    "have", "has", "had", "not", "but", "from", "they", "their", "them", "what", "when", "how", "can",
    "will", "just", "more", "most", "than", "then", "into", "about", "because", "even", "only", "really",
    "like", "make", "makes", "feel", "feels", "next", "time", "often", "still", "know", "knows", "people",
    "person", "brain", "mind", "called", "known", "effect", "bias", "its", "it's", "you're", "that's",
}

# Reject thresholds. Chosen against the real archive: distinct effects score
# well below these on script/topic overlap; a re-worded take on the same idea
# scores well above (see the check in this module's history / README).
_TOPIC_SEQ_RATIO = 0.72
_TOPIC_WORD_OVERLAP = 0.67  # real, different videos reach 0.5 on shared everyday words ("partner", "know")
_SCRIPT_WORD_OVERLAP = 0.32


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _content_words(text: str) -> set[str]:
    words = set()
    for w in _norm(text).split():
        if len(w) < 4 or w in _STOPWORDS:
            continue
        for suffix in ("ing", "ed", "es", "s"):  # crude stem so "knows"/"know", "expects"/"expect" match
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        words.add(w)
    return words


def _overlap(a: set[str], b: set[str]) -> float:
    """Share of the smaller word set that also appears in the other — high even
    when one text is much longer, which is what a re-written take looks like."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


_EFFECT_FILLER = {"the", "of", "effect", "bias", "fallacy", "illusion", "heuristic", "phenomenon", "principle"}


def _effect_key(effect: str) -> str:
    """"Illusion of transparency" and "transparency effect" -> "transparency"."""
    return " ".join(w for w in _norm(effect).split() if w not in _EFFECT_FILLER)


def find_duplicate(entry: dict, known_rows: list[dict]) -> str | None:
    """Why `entry` must be rejected (a human-readable reason naming the
    existing video it collides with), or None if it is new enough."""
    topic_norm = _norm(entry["topic"])
    effect_norm = _norm(entry.get("effect", ""))
    effect_key = _effect_key(entry.get("effect", ""))
    topic_words = _content_words(entry["topic"])
    script_words = _content_words(entry["script"])

    for row in known_rows:
        old_topic = row["topic"]
        if _norm(old_topic) == topic_norm:
            return f"same topic as an earlier video: {old_topic!r}"

        old_text = _norm(f"{row['topic']} {row.get('script', '')}")
        old_effect = _norm(row.get("effect", ""))
        if effect_norm and (effect_norm == old_effect or (effect_key and effect_key == _effect_key(row.get("effect", "")))):
            return f"effect {entry['effect']!r} was already covered by: {old_topic!r}"
        # pre-written bank rows have no effect column, but their script names the effect
        if effect_norm and f" {effect_norm} " in f" {old_text} ":
            return f"effect {entry['effect']!r} is already named in an earlier video: {old_topic!r}"

        if difflib.SequenceMatcher(None, topic_norm, _norm(old_topic)).ratio() >= _TOPIC_SEQ_RATIO:
            return f"topic is a re-wording of an earlier one: {old_topic!r}"
        if _overlap(topic_words, _content_words(old_topic)) >= _TOPIC_WORD_OVERLAP and len(topic_words) >= 3:
            return f"topic covers the same ground as an earlier one: {old_topic!r}"
        if row.get("script") and _overlap(script_words, _content_words(row["script"])) >= _SCRIPT_WORD_OVERLAP:
            return f"script is too similar to the earlier video: {old_topic!r}"
    return None


# --- generation --------------------------------------------------------------

def _parse_entry(text: str, min_words: int, max_words: int) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the answer")
    data = json.loads(match.group(0))
    effect = str(data.get("effect", "")).strip().strip('"')
    topic = str(data.get("topic", "")).strip().strip('"')
    script = re.sub(r"[*_#`~]", "", str(data.get("script", ""))).strip().strip('"')
    description = str(data.get("description", "")).replace("\r", "").strip()
    keywords = [str(k).strip() for k in (data.get("visual_keywords") or []) if str(k).strip()]

    if not effect or not topic or not script:
        raise ValueError("effect, topic or script missing")
    words = len(script.split())
    if not (min_words <= words <= max_words):
        raise ValueError(f"script is {words} words, wanted {min_words}-{max_words}")
    if not script.rstrip().endswith((".", "!", "?")):
        raise ValueError("script does not end with sentence punctuation")
    if len(keywords) < 3:
        raise ValueError(f"only {len(keywords)} visual_keywords")
    return {
        "effect": effect, "topic": topic, "language": "en", "script": script,
        "description": description, "visual_keywords": ";".join(keywords[:5]),
    }


def _describe_known(rows: list[dict], limit: int = 150) -> str:
    lines = []
    for r in rows[-limit:]:
        effect = f" [{r['effect']}]" if r.get("effect") else ""
        lines.append(f"- {r['topic']}{effect}")
    return "\n".join(lines) or "(none yet)"


def _fact_checked(entry: dict, cfg: dict) -> dict:
    """Run the accuracy pass; on a revision, swap in the corrected script and
    caption. `fact_check_notes` records what was found (written to the job's
    factcheck.txt by main.py)."""
    result = script_generator.fact_check(entry["topic"], entry["script"], entry.get("description", ""), cfg)
    entry["fact_check"] = result["verdict"]
    entry["fact_check_notes"] = result["issues"]
    if result["verdict"] == "revised":
        log.info("fact-check revised the script: %s", "; ".join(result["issues"]) or "(no notes)")
        entry["script"] = result["script"]
        entry["description"] = result["description"]
    else:
        log.info("fact-check: %s%s", result["verdict"], f" — {'; '.join(result['issues'])}" if result["issues"] else "")
    return entry


def generate_daily_entry(
    cfg: dict, bank_path: Path = topic_bank.DEFAULT_BANK_PATH, used_path: Path = topic_bank.DEFAULT_USED_PATH,
    max_attempts: int = 3,
) -> dict | None:
    """One fresh {effect, topic, language, script, visual_keywords} for today
    that is not a repeat or near-repeat of anything already made, or None if the
    LLM is unavailable or never produced an acceptable answer."""
    if llm.backend(cfg) is None:
        log.info("no LLM backend available — skipping daily generation")
        return None

    max_words = int(cfg["script"]["max_words"])
    min_words = max(60, max_words - 35)
    known = topic_bank.all_known_rows(bank_path, used_path)
    area = _AREAS[date.today().toordinal() % len(_AREAS)]
    system = _SYSTEM_TEMPLATE.format(
        min_words=min_words, max_words=max_words, exclude=_describe_known(known),
        description_rules=script_generator.DESCRIPTION_RULES,
    )

    user = f"Today's focus area: {area}. Write today's video."
    for attempt in range(1, max_attempts + 1):
        try:
            entry = _parse_entry(llm.complete(system, user, cfg, max_tokens=1500), min_words, max_words)
            reason = find_duplicate(entry, known)
            if reason is None:
                log.info("generated today's video [%s] (%s): %s", entry["effect"], area, entry["topic"])
                return _fact_checked(entry, cfg)
            raise ValueError(f"REJECTED as a repeat: {reason}")
        except llm.LLMUnavailable as e:
            log.warning("LLM unavailable (%s) — falling back to the pre-written bank", e)
            return None
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("attempt %d/%d: %s", attempt, max_attempts, e)
            user = (
                f"Today's focus area: {area}. Write today's video.\n\n"
                f"Your previous answer was not accepted: {e}. Pick a completely different effect "
                "and a different everyday situation, and follow the format and word count exactly."
            )
    return None


if __name__ == "__main__":
    from dotenv import load_dotenv

    from src.utils import load_config

    load_dotenv()
    result = generate_daily_entry(load_config())
    print(json.dumps(result, indent=2, ensure_ascii=False) if result else "no entry generated")
