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

# The script rules live here so `single` (a topic you give) and `auto` (a topic Claude invents,
# see daily_topic.py) hold every video to the same standard: ONE question, ONE amazing answer.
SCRIPT_STRUCTURE = (
    "The viewer HEARS the question first — the title is read aloud before your script — so NEVER restate or "
    "re-ask it. Structure: (1) a hook line that builds on the question they have just heard and makes them "
    "need the answer — a vivid image, a stake, or a claim that sounds almost unbelievable, NOT a rephrasing "
    "of the question and NOT a generic opener (ban \"Ever wonder\", \"Have you ever\", \"Did you know\", "
    "\"Turns out\" as openers — vary how every script starts); (2) THE ANSWER, stated plainly and early — "
    "the one surprising thing that is true, and whenever the science genuinely supports it, framed as a "
    "REVERSAL of what the viewer would assume (this channel's best-performing video: viewers expect doing "
    "someone a favor makes THEM like you, but it's the reverse — asking someone for a small favor makes "
    "THEM like YOU more; lead with that kind of twist when the real finding has one, never force a false one); "
    "(3) one vivid, concrete proof or everyday example that makes it land — reach for a striking comparison "
    "or number when the science gives you one cleanly (\"faster than a passenger jet\", \"one in three "
    "people\"), never invent one to sound impressive; (4) one closing line that reframes what the viewer "
    "thought they knew, landing on an image or feeling, not a restatement of the fact, and where it fits "
    "naturally, gives the viewer something real to notice, try or watch for in the next few minutes (this is "
    "what earns comments like \"I tried this and...\"), never a generic \"now you know\" wrap-up. One "
    "idea only: no side-tracks, no second explanation, no list of facts. LENGTH FOLLOWS THE "
    "CONTENT within a hard limit: the whole video stays under one minute, so use only as many words as "
    "this particular answer needs to be complete and satisfying — a striking answer that lands fast can be "
    "short. Never pad, never rush, and cut anything that isn't earning its place; a longer script goes "
    "deeper into the SAME answer, it never adds a second topic. Plain, spoken, "
    "conversational sentences of 6-22 words, ending in periods or question marks, with commas where a "
    "speaker would pause. Spell numbers out as words. No emojis, hashtags, markdown, stage directions or "
    "headings. Only well-established findings — never invent a study, statistic, date or name; if you are "
    "unsure of an exact figure use a safe rounded or comparative one. No hedging like \"researchers still "
    "debate\": the core answer must be something specialists agree on."
)

SYSTEM_PROMPT_TEMPLATE = (
    "You write scripts for {channel}, a faceless short-video channel (TikTok / YouTube Shorts / Reels) "
    "whose promise is: \"{tagline}\" Categories: Psychology, Science, Space, World, Technology, What if? "
    "The viewer sees the question as the title; you write what a voice reads aloud in {language}, "
    "{min_words}-{max_words} words. " + SCRIPT_STRUCTURE + " Return only the words to be read."
)


def description_rules(hashtags: list[str] | None = None) -> str:
    """How the post caption should look. `hashtags` are the channel's broad tags for the video's
    category (from config content.categories); the model adds 2-3 specific ones and #shorts."""
    broad = " ".join(hashtags) if hashtags else "2-3 broad tags for the video's subject"
    return (
        "Format: 2-3 short lines: (1) the question, phrased to stop the scroll, (2) one line that teases or "
        "states the amazing answer, (3) a short question that invites comments. Then a blank line, then 6-8 "
        f"hashtags on ONE line: the broad ones ({broad}), 2-3 specific to this video's subject, and #shorts. "
        "Under 350 characters in total. Plain text, no markdown, no emojis, no claims of curing or "
        "diagnosing anything."
    )


def _description_system(cfg: dict) -> str:
    content = cfg.get("content", {})
    cats = "; ".join(f"{c['label']}: {' '.join(c.get('hashtags', []))}" for c in content.get("categories", []))
    return (
        f"You write the caption for a short video by the channel {cfg.get('branding', {}).get('channel_name', '')} "
        "posted on TikTok, YouTube Shorts and Instagram Reels. "
        + description_rules(None)
        + (f" Broad tags by category (use the one that fits the video): {cats}." if cats else "")
        + " Reply with the caption only."
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

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        channel=cfg.get("branding", {}).get("channel_name", "the channel"),
        tagline=cfg.get("content", {}).get("tagline", "One question. One amazing answer."),
        language=language, min_words=cfg["script"].get("min_words", max_words - 30), max_words=max_words,
    )
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
    elif check["verdict"] == "reject":
        # `auto` would pick another topic; here the topic was chosen by the user, so keep the
        # script but say plainly that its core answer is shaky.
        log.warning("fact-check doubts the core answer of %r (%s) — script kept, review it before posting",
                    topic, "; ".join(check["issues"]) or "no details")

    return script


_FACTCHECK_SYSTEM = """You are the fact-checker and editor for a short-video channel whose promise is "One question. One amazing answer." Categories: Psychology, Science, Space, World, Technology, What if? You receive a video's category, its title (the question), its spoken script (the answer) and its post caption. Judge two things.

1. TRUTH. Check every claim against well-established knowledge.
- Solid claims: keep them exactly.
- A detail that is oversimplified or slightly off: fix just that sentence, keeping it simple, spoken and confident.
- A study, number, date or name you cannot confirm: remove it or make it general or rounded. Never add new ones.
- If the CORE answer itself (what the video presents as the answer to its question) is contested, still argued over by specialists, or not what the evidence shows: verdict "reject". Do not hide it behind hedging words; the channel needs a topic whose answer is settled. For a "What if" video the core answer must be what established physics, chemistry or biology actually predicts; speculation is a reject.

2. WOW. Rate 1-5 how amazing the answer is to a curious non-expert: 5 = jaw-dropping, they will share it; 3 = interesting but familiar; 1 = obvious or dull. Be a tough judge. This channel's real view data shows topics tied to something almost everyone has personally noticed (a face in a wall socket, the moon near the horizon) get far more views than facts that are merely interesting in the abstract (an effect few people consciously experience) — score an abstract, rarely-noticed topic no higher than 3 even if the fact itself is impressive.

Any fixes keep the structure (hook, answer, proof, closing line), the conversational tone, plain sentences of 6-22 words ending in periods or question marks, numbers spelled out, and a length close to the original and NEVER above MAX_WORDS words. The title stays the same. If the caption repeats a wrong claim, fix it too, keeping its format (short lines, blank line, hashtags on one line).

Reply with ONE JSON object and nothing else:
{"verdict": "ok" or "revised" or "reject", "wow": 1-5, "issues": ["one short note per problem; empty if none"], "script": "...", "description": "..."}
For "ok" and "reject" return the script and description unchanged; put the reasons for a reject in "issues"."""


def fact_check(topic: str, script: str, description: str, cfg: dict, category: str = "") -> dict:
    """Second-opinion pass over a generated script (and caption): truth and "wow".

    Returns {"verdict": "ok" | "revised" | "reject" | "skipped", "wow": int | None, "issues": [...],
    "script", "description"}. "reject" = the core answer is contested/unsettled, or scored under
    script.min_wow, so the topic itself should be replaced. "skipped" (disabled, no backend, call
    failed, or an unusable answer) always carries the ORIGINAL script/description — a broken check
    must never break or corrupt the video.
    """
    original = {"verdict": "skipped", "wow": None, "issues": [], "script": script, "description": description}
    if not cfg["script"].get("fact_check", True) or llm.backend(cfg) is None:
        return original
    max_words = cfg["script"]["max_words"]
    min_wow = int(cfg["script"].get("min_wow", 0))
    system = _FACTCHECK_SYSTEM.replace("MAX_WORDS", str(max_words))
    user = (f"Category: {category or '(not given)'}\nTitle: {topic}\n\nScript:\n{script}\n\n"
            f"Caption:\n{description or '(none)'}")
    try:
        text = _ask(system, user, cfg)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in the answer")
        data = json.loads(match.group(0))
        verdict = str(data.get("verdict", "")).lower()
        verdict = verdict if verdict in ("revised", "reject") else "ok"
        issues = [str(i).strip() for i in (data.get("issues") or []) if str(i).strip()]
        try:
            wow = int(data.get("wow"))
        except (TypeError, ValueError):
            wow = None

        if verdict != "reject" and wow is not None and wow < min_wow:
            verdict = "reject"
            issues.append(f"not amazing enough: wow {wow}/5, minimum is {min_wow}")
        if verdict == "reject":
            return {"verdict": "reject", "wow": wow, "issues": issues, "script": script, "description": description}
        if verdict == "ok":
            return {"verdict": "ok", "wow": wow, "issues": issues, "script": script, "description": description}

        new_script = _clean_script(str(data.get("script", "")))
        new_desc = str(data.get("description", "")).replace("\r", "").strip() or description
        old_words, new_words = _word_count(script), _word_count(new_script)
        if not new_script or not (0.75 * old_words <= new_words <= 1.25 * old_words) or new_words > max_words:
            raise ValueError(
                f"revised script has {new_words} words vs {old_words} (limit {max_words}) — keeping the original"
            )
        if not new_script.rstrip().endswith((".", "!", "?")):
            raise ValueError("revised script does not end with sentence punctuation")
        return {"verdict": "revised", "wow": wow, "issues": issues, "script": new_script, "description": new_desc}
    except (llm.LLMUnavailable, ValueError, json.JSONDecodeError) as e:
        log.warning("fact-check skipped (%s) — keeping the script as written", e)
        return original


def generate_description(topic: str, script: str, cfg: dict) -> str | None:
    """Post caption (text + hashtags) for a finished script, or None if no LLM
    backend is available or the call fails — the video is still fine without it."""
    if llm.backend(cfg) is None:
        return None
    try:
        text = _ask(_description_system(cfg), f"Video title: {topic}\n\nVideo script:\n{script}", cfg).strip()
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
