"""Write today's video from scratch: ask an LLM (the Claude Code CLI or the
Anthropic API — see src/llm.py) for one brand-new question, its one amazing
answer (the script), the post caption and per-scene stock-footage phrases.

The channel's promise is "One question. One amazing answer." The category
(Psychology, Science, Space, World, Technology, What if?) rotates evenly — see
src/categories.py — and every answer is held to the same bar:

  - it must not repeat or resemble anything already made (same subject, a
    re-worded question, a script that reads alike -> rejected, model told why);
  - a second pass fact-checks it and scores how amazing it is; a contested core
    answer, or a score under script.min_wow, rejects the topic itself.

Returns None when no acceptable answer comes back (or no backend is available),
so the caller can stop or fall back to the pre-written bank.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from src import categories, llm, music_composer, script_generator, topic_bank, voice_generator
from src.utils import get_logger

log = get_logger("daily_topic")

_SYSTEM_TEMPLATE = """You write for {channel}, a faceless short-video channel (TikTok / YouTube Shorts / Reels). Its promise, and the test every video must pass: "{tagline}"

Each video is ONE question the viewer is dying to know the answer to, and ONE amazing answer. A voice reads the question aloud first, then your script, with captions burned in; the question is also shown as the title card.

TODAY'S CATEGORY: {label}
{brief}
Emotional tone for this category: {tone}.

Produce ONE video in this category. Reply with a single JSON object and nothing else (no markdown fences, no commentary):
{{"subject": "...", "topic": "...", "mood": "...", "script": "...", "description": "...", "visual_keywords": ["...", "...", "..."]}}

subject: what the video is about, 2-5 words, as an encyclopaedia would title it (for example "sunk cost fallacy", "neutron stars", "GPS time dilation", "an Earth without the Moon").

topic: THE QUESTION. Addressed to the viewer ("you"), under 75 characters, ending with a question mark, with a real curiosity gap that makes someone stop scrolling. Prefer something tied to a moment the viewer has personally lived or is living right now ("the light hitting your skin right now", "a socket that looks like it's staring at you") over a generic third-person phrasing of the same fact. Reach for a vivid or slightly strange image over a clinical "Why does X happen?" phrasing whenever one fits — picture the exact moment a viewer would be living when this crosses their mind, and ask about THAT. Vary the sentence shape from video to video (not always "Why do/does..."). {topic_rule}

mood: which background music suits the topic. Exactly one of: "mysterious" (hidden forces, secrets, illusions, the unseen), "tense" (danger, pressure, loss, stakes), "curious" (puzzling everyday quirks, discovery, "how is that even possible"), "warm" (connection, kindness, hope, wonder at life), "playful" (light, funny, low-stakes). When unsure, "mysterious".

script: English, between {min_words} and {max_words} words. The voice reads about 2.4 words per second and the whole video MUST stay under ONE MINUTE, so {max_words} words is a hard ceiling — but this channel's data shows 30-45 SECOND videos get far more views than anything near the one-minute ceiling, so treat {typical} as the real target and only go longer when the proof genuinely can't be told faster. {structure}

description: the caption to paste under the post, in English. {description_rules}

visual_keywords: short stock-footage search phrases (2-4 words each, plain English) for Pexels/Pixabay, in the order the footage should appear across the whole script: the opening hook, the answer, the proof or example, the closing line. Write roughly one per 15-20 words of your script (at least 6); the video changes shot about every 8 seconds, so every phrase must work on its own as a different shot. Suitable footage for this category: {footage}. Each phrase must describe something concrete a camera can film, never an abstract concept, and never a person's name or a brand.

Choose an answer that is (a) true and settled among specialists, (b) genuinely surprising to a curious non-expert, and (c) tied to something almost every viewer has personally noticed or experienced (a face in a wall socket, the moon near the horizon, missing something obvious, liking someone more after doing them a favor) rather than a fact that is merely interesting in the abstract (this channel's own data: relatable everyday-experience topics get roughly 10-30x the views of correct-but-abstract ones). If the best-known example of a topic is still argued over, choose another. Every video covers a DIFFERENT subject and angle from all earlier ones; never repeat, re-word, or write a second take on an earlier video. Earlier videos (question [subject, category]):
{exclude}"""

# --- duplicate detection -----------------------------------------------------

_STOPWORDS = {
    "the", "and", "that", "this", "with", "your", "you", "why", "does", "do", "for", "are", "was", "were",
    "have", "has", "had", "not", "but", "from", "they", "their", "them", "what", "when", "how", "can",
    "will", "just", "more", "most", "than", "then", "into", "about", "because", "even", "only", "really",
    "like", "make", "makes", "feel", "feels", "next", "time", "often", "still", "know", "knows", "people",
    "person", "brain", "mind", "called", "known", "effect", "bias", "its", "it's", "you're", "that's",
}

# Reject thresholds. Chosen against the real archive: distinct subjects score
# well below these on script/topic overlap; a re-worded take on the same idea
# scores well above (see tests/test_daily_topic.py).
_TOPIC_SEQ_RATIO = 0.72
_TOPIC_WORD_OVERLAP = 0.67  # real, different videos reach 0.5 on shared everyday words ("partner", "know")
_SCRIPT_WORD_OVERLAP = 0.32
_MIN_SUBJECT_LEN_FOR_TEXT_MATCH = 8  # "Mars" appears in plenty of unrelated scripts; "pareidolia" does not


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
    existing video it collides with), or None if it is new enough.
    `entry["effect"]` is the video's subject (an effect, a planet, a scenario...)."""
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
            return f"subject {entry['effect']!r} was already covered by: {old_topic!r}"
        # pre-written bank rows have no subject column, but their script names the effect
        if len(effect_norm) >= _MIN_SUBJECT_LEN_FOR_TEXT_MATCH and f" {effect_norm} " in f" {old_text} ":
            return f"subject {entry['effect']!r} is already named in an earlier video: {old_topic!r}"

        if difflib.SequenceMatcher(None, topic_norm, _norm(old_topic)).ratio() >= _TOPIC_SEQ_RATIO:
            return f"topic is a re-wording of an earlier one: {old_topic!r}"
        if _overlap(topic_words, _content_words(old_topic)) >= _TOPIC_WORD_OVERLAP and len(topic_words) >= 3:
            return f"topic covers the same ground as an earlier one: {old_topic!r}"
        if row.get("script") and _overlap(script_words, _content_words(row["script"])) >= _SCRIPT_WORD_OVERLAP:
            return f"script is too similar to the earlier video: {old_topic!r}"
    return None


# --- generation --------------------------------------------------------------

def _parse_entry(
    text: str, min_words: int, max_words: int, category: dict | None = None, read_title: bool = True,
) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the answer")
    data = json.loads(match.group(0))
    subject = str(data.get("subject") or data.get("effect") or "").strip().strip('"')
    topic = str(data.get("topic", "")).strip().strip('"')
    script = re.sub(r"[*_#`~]", "", str(data.get("script", ""))).strip().strip('"')
    description = str(data.get("description", "")).replace("\r", "").strip()
    mood = str(data.get("mood", "")).strip().lower()
    if mood not in music_composer.MOODS:
        mood = ""  # unusable -> the music picker falls back to the topic's keywords
    keywords = [str(k).strip() for k in (data.get("visual_keywords") or []) if str(k).strip()]

    if not subject or not topic or not script:
        raise ValueError("subject, topic or script missing")
    if not topic.endswith("?"):
        raise ValueError("the title must be a question ending with '?'")
    if category and category["id"] == "whatif" and not topic.lower().startswith("what if"):
        raise ValueError('a "What if?" video\'s title must start with "What if"')
    if read_title and voice_generator.intro_for(topic, script) is None:
        raise ValueError(
            "the script's first sentence repeats the title, but the title is read aloud before the script: "
            "start with a hook that builds on the question instead"
        )
    words = len(script.split())
    if not (min_words <= words <= max_words):
        raise ValueError(f"script is {words} words, wanted {min_words}-{max_words}")
    if not script.rstrip().endswith((".", "!", "?")):
        raise ValueError("script does not end with sentence punctuation")
    if len(keywords) < 3:
        raise ValueError(f"only {len(keywords)} visual_keywords")
    return {
        "effect": subject, "category": category["id"] if category else "", "topic": topic, "language": "en",
        "script": script, "mood": mood, "description": description, "visual_keywords": ";".join(keywords[:40]),
    }


def _describe_known(rows: list[dict], limit: int = 150) -> str:
    lines = []
    for r in rows[-limit:]:
        tags = ", ".join(t for t in (r.get("effect"), r.get("category")) if t)
        lines.append(f"- {r['topic']}" + (f" [{tags}]" if tags else ""))
    return "\n".join(lines) or "(none yet)"


def _fact_checked(entry: dict, cfg: dict, category: dict | None) -> dict:
    """Run the accuracy + wow pass. A revision swaps in the corrected script and caption; a reject
    raises ValueError so the caller asks for a different topic. `fact_check_notes` records what was
    found (written to the job's factcheck.txt by main.py)."""
    label = category["label"] if category else ""
    result = script_generator.fact_check(entry["topic"], entry["script"], entry.get("description", ""), cfg, label)
    entry["fact_check"] = result["verdict"]
    entry["fact_check_notes"] = result["issues"]
    entry["wow"] = result.get("wow")
    if result["verdict"] == "reject":
        raise ValueError(
            "REJECTED by the fact-checker: " + ("; ".join(result["issues"]) or "core answer not settled")
            + ". Choose a different subject whose answer is settled and truly surprising."
        )
    if result["verdict"] == "revised":
        log.info("fact-check revised the script (wow %s): %s", result.get("wow"), "; ".join(result["issues"]) or "(no notes)")
        entry["script"] = result["script"]
        entry["description"] = result["description"]
    else:
        log.info("fact-check: %s (wow %s)%s", result["verdict"], result.get("wow"),
                 f" — {'; '.join(result['issues'])}" if result["issues"] else "")
    return entry


def generate_daily_entry(
    cfg: dict, bank_path: Path = topic_bank.DEFAULT_BANK_PATH, used_path: Path = topic_bank.DEFAULT_USED_PATH,
    max_attempts: int = 3, category: str | None = None,
) -> dict | None:
    """One fresh {effect (subject), category, topic (the question), language, script (the answer),
    mood, description, visual_keywords} for today that is not a repeat or near-repeat of anything
    already made and survived the fact-check, or None if the LLM is unavailable or never produced an
    acceptable answer. `category` (an id from config content.categories) overrides the rotation."""
    if llm.backend(cfg) is None:
        log.info("no LLM backend available — skipping daily generation")
        return None

    script_cfg = cfg["script"]
    max_words = int(script_cfg["max_words"])
    min_words = int(script_cfg.get("min_words", max(60, max_words - 35)))
    known = topic_bank.all_known_rows(bank_path, used_path)
    cat = categories.pick_next(cfg, known, forced=category)
    content = cfg.get("content", {})
    system = _SYSTEM_TEMPLATE.format(
        channel=cfg.get("branding", {}).get("channel_name", "the channel"),
        tagline=content.get("tagline", "One question. One amazing answer."),
        label=cat["label"], brief=cat.get("brief", ""), tone=cat.get("tone", "curious and clear"),
        topic_rule='It MUST start with "What if".' if cat["id"] == "whatif" else "",
        min_words=min_words, max_words=max_words, typical=f"{min_words}-{min_words + 30} words",
        structure=script_generator.SCRIPT_STRUCTURE,
        description_rules=script_generator.description_rules(cat.get("hashtags")),
        footage=categories.FOOTAGE_HINTS.get(cat["id"], "concrete, filmable scenes"),
        exclude=_describe_known(known),
    )
    log.info("category for this video: %s", cat["label"])

    user = f"Category: {cat['label']}. Write today's video."
    for attempt in range(1, max_attempts + 1):
        try:
            entry = _parse_entry(
                llm.complete(system, user, cfg, max_tokens=4000), min_words, max_words, cat,
                read_title=cfg.get("voice", {}).get("read_title", True),
            )
            reason = find_duplicate(entry, known)
            if reason is None:
                log.info("generated [%s / %s]: %s", cat["label"], entry["effect"], entry["topic"])
                return _fact_checked(entry, cfg, cat)
            raise ValueError(f"REJECTED as a repeat: {reason}")
        except llm.LLMUnavailable as e:
            log.warning("LLM unavailable (%s) — falling back to the pre-written bank", e)
            return None
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("attempt %d/%d: %s", attempt, max_attempts, e)
            user = (
                f"Category: {cat['label']}. Write today's video.\n\n"
                f"Your previous answer was not accepted: {e}. Pick a completely different subject "
                "and angle, and follow the format, question and word count exactly."
            )
    return None


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    from src.utils import load_config

    load_dotenv()
    forced = sys.argv[1] if len(sys.argv) > 1 else None  # e.g. python -m src.daily_topic space
    result = generate_daily_entry(load_config(), category=forced)
    print(json.dumps(result, indent=2, ensure_ascii=False) if result else "no entry generated")
