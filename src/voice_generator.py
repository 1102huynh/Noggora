"""Text-to-speech via edge-tts (default) or ElevenLabs: script -> voice.mp3 + voice.srt.

edge-tts talks to Microsoft Edge's public (not officially documented) TTS
endpoint. It needs unrestricted internet egress and will not work inside a
sandboxed environment with blocked egress — see README "Known limitations".

ElevenLabs is opt-in via config/settings.yaml's voice.provider: elevenlabs
(needs ELEVENLABS_API_KEY in .env) — a paid API, used when you want a
specific/nicer voice than edge-tts's free ones.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from datetime import timedelta
from pathlib import Path

import edge_tts
import requests

from src.utils import ffprobe_duration, get_logger, retry_network

log = get_logger("voice_generator")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class WordCue:
    """Provider-agnostic stand-in for edge_tts's Subtitle cue: one spoken
    word plus its start/end offset into the audio. edge-tts's own cue
    objects already duck-type as this (same .start/.end/.content attributes)
    so they're used as-is; ElevenLabs cues are built by hand below from its
    character-level alignment."""
    start: timedelta
    end: timedelta
    content: str


def _format_srt_timestamp(td: timedelta) -> str:
    total_ms = int(td.total_seconds() * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def _split_into_sentences(script: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(script.strip()) if s]


def _group_cues_by_sentence(cues: list, script: str) -> list[tuple[str, list]]:
    """Pair each sentence of the original script with the slice of spoken-word
    cues that covers it, as (sentence_text, cue_group). Sentence boundaries
    come from the script's punctuation; each sentence's word *count* (not its
    text) is used to slice the cues, since cue text is whatever the TTS
    engine actually said (numbers spelled out, punctuation stripped, etc.)
    and won't line up with the written script word-for-word — only the
    written sentence_text is used for what's actually displayed, so the
    caption keeps the script's own commas/punctuation instead of reading as
    one run-on clause.
    """
    sentences = _split_into_sentences(script)
    groups = []
    i = 0
    for sentence in sentences:
        if i >= len(cues):
            break
        count = len(sentence.split())
        group = cues[i : i + count]
        if group:
            groups.append((sentence, group))
        i += count
    if i < len(cues):  # leftover cues from a word-count mismatch — keep them, don't drop
        leftover = cues[i:]
        groups.append((" ".join(c.content for c in leftover), leftover))
    return groups


def _chunk_words(words: list[str], max_words: int) -> list[list[str]]:
    """Split one sentence's words into short caption phrases: break at the
    script's own commas/colons first (so a phrase reads naturally), split any
    phrase still longer than max_words into even pieces, then fold dangling
    1-2 word scraps into a neighbour."""
    phrases: list[list[str]] = []
    cur: list[str] = []
    for w in words:
        cur.append(w)
        if w.endswith((",", ";", ":")):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)

    pieces: list[list[str]] = []
    for p in phrases:
        if len(p) <= max_words:
            pieces.append(p)
            continue
        n = -(-len(p) // max_words)  # ceil
        size = -(-len(p) // n)
        pieces.extend(p[i : i + size] for i in range(0, len(p), size))

    merged: list[list[str]] = []
    for piece in pieces:
        if merged and (len(piece) <= 2 or len(merged[-1]) <= 2) and len(merged[-1]) + len(piece) <= max_words + 1:
            merged[-1] = merged[-1] + piece
        else:
            merged.append(piece)
    return merged


def _cues_to_srt(cues: list, script: str, max_words: int = 6) -> tuple[str, list[dict]]:
    """Render cues as SRT, one caption per short phrase (<= max_words) of each
    sentence of `script`. Text is the script's own wording/punctuation; each
    phrase is timed from when its first word is spoken to when its last word
    finishes (held on screen until the next phrase of the same sentence
    starts, so captions don't flicker between phrases).

    Also returns, per caption, the spoken timing of each of its words
    ([{"start", "end", "words": [{"t", "s", "e"}, ...]}, ...], seconds) —
    the SRT can't carry that, and word-by-word caption highlighting needs it.
    """
    entries: list[tuple[timedelta, timedelta, str, int, list[dict]]] = []  # start, end, text, sentence idx, words
    for s_idx, (sentence, group) in enumerate(_group_cues_by_sentence(cues, script)):
        words = sentence.split()
        offset = 0
        for chunk in _chunk_words(words, max_words):
            first = group[min(offset, len(group) - 1)]
            last = group[min(offset + len(chunk) - 1, len(group) - 1)]
            word_timing = []
            for j, word in enumerate(chunk):
                cue = group[min(offset + j, len(group) - 1)]
                word_timing.append({"t": word, "s": cue.start.total_seconds(), "e": cue.end.total_seconds()})
            entries.append((first.start, last.end, " ".join(chunk), s_idx, word_timing))
            offset += len(chunk)

    lines = []
    timings = []
    for i, (start, end, text, s_idx, word_timing) in enumerate(entries):
        if i + 1 < len(entries) and entries[i + 1][3] == s_idx:
            end = max(end, entries[i + 1][0])
        lines.append(
            f"{i + 1}\n{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n{text}\n"
        )
        timings.append({"start": start.total_seconds(), "end": end.total_seconds(), "words": word_timing})
    return "\n".join(lines), timings


@retry_network(max_attempts=3)
async def _synthesize_edge(script: str, voice: str, rate: str, mp3_path: Path) -> list:
    """edge-tts occasionally raises NoAudioReceived on an otherwise-healthy
    connection (observed under back-to-back batch requests) — retry with a
    fresh Communicate/stream rather than surfacing a transient hiccup as a
    hard job failure."""
    communicate = edge_tts.Communicate(script, voice, rate=rate, boundary="WordBoundary")
    submaker = edge_tts.SubMaker()
    with open(mp3_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)
    if not submaker.cues:
        raise RuntimeError(
            "edge-tts returned no WordBoundary metadata — no audio/timing was produced. "
            "Check internet connectivity (edge-tts needs unrestricted egress)."
        )
    return submaker.cues


@retry_network(max_attempts=3)
def _synthesize_elevenlabs(script: str, voice_id: str, model_id: str, speed: float, mp3_path: Path) -> list:
    """ElevenLabs' /with-timestamps endpoint returns audio plus per-character
    timing, which we collapse into per-word cues (splitting on whitespace) so
    the rest of the pipeline can treat it exactly like edge-tts's word
    boundaries. Runs sync (requests, not httpx/async) — called via
    asyncio.to_thread from generate_voice.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY not set — required when voice.provider: elevenlabs in settings.yaml."
        )
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={"text": script, "model_id": model_id, "voice_settings": {"speed": speed}},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    mp3_path.write_bytes(base64.b64decode(data["audio_base64"]))

    alignment = data.get("alignment") or {}
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not chars:
        raise RuntimeError("ElevenLabs response had no character alignment — cannot build captions.")

    cues: list[WordCue] = []
    word_chars: list[str] = []
    word_start = None
    prev_end = 0.0
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if word_chars:
                cues.append(WordCue(timedelta(seconds=word_start), timedelta(seconds=prev_end), "".join(word_chars)))
                word_chars, word_start = [], None
            continue
        if word_start is None:
            word_start = s
        word_chars.append(ch)
        prev_end = e
    if word_chars:
        cues.append(WordCue(timedelta(seconds=word_start), timedelta(seconds=prev_end), "".join(word_chars)))
    if not cues:
        raise RuntimeError("ElevenLabs alignment produced no words — cannot build captions.")
    return cues


def _norm_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split()


def intro_for(title: str, script: str) -> str | None:
    """The line to read aloud before the script: the video's question. None when the script already
    opens with (nearly) that same question, so it isn't said twice — older scripts, and ones you
    write yourself, often restate it."""
    title = title.strip()
    if not title:
        return None
    sentences = _split_into_sentences(script)
    if sentences:
        a, b = _norm_words(title), _norm_words(sentences[0])
        if a and b:
            overlap = len(set(a) & set(b)) / min(len(set(a)), len(set(b)))
            same_order = SequenceMatcher(None, a, b).ratio()
            if same_order >= 0.72 or overlap >= 0.8:
                return None
    return title


def _timings_to_srt(timings: list[dict]) -> str:
    """SRT text for caption timings ([{"start", "end", "words": [{"t", ...}]}, ...])."""
    blocks = []
    for i, ph in enumerate(timings, 1):
        text = " ".join(w["t"] for w in ph["words"])
        blocks.append(
            f"{i}\n{_format_srt_timestamp(timedelta(seconds=ph['start']))} --> "
            f"{_format_srt_timestamp(timedelta(seconds=ph['end']))}\n{text}\n"
        )
    return "\n".join(blocks)


def split_intro(timings: list[dict], intro_words: int) -> tuple[list[dict], dict | None]:
    """Separate the captions of a spoken intro (the question, read aloud before the script) from
    the rest. The intro is whole sentences, so it always ends on a caption boundary. Returns
    (captions after the intro, {"start", "end"} of the intro or None). The intro is left out of the
    captions on purpose: the title card shows that text on screen while it is being spoken."""
    if intro_words <= 0:
        return timings, None
    seen, cut = 0, 0
    for i, ph in enumerate(timings):
        seen += len(ph["words"])
        if seen >= intro_words:
            cut = i + 1
            break
    else:
        return timings, None  # fewer words than the intro claims: nothing sensible to split
    intro = {"start": timings[0]["start"], "end": timings[cut - 1]["words"][-1]["e"]}
    return timings[cut:], intro


MAX_SPEEDUP = 1.25   # beyond this a voice sounds hurried, so a longer read is left alone (and warned about)
_FIT_MARGIN_SEC = 0.5  # aim a little under the cap: the video is exactly as long as the voice track


def _fit_to_max_duration(mp3_path: Path, cues: list, max_duration: float) -> list:
    """If the voice track is longer than `max_duration`, speed it up just enough to fit (ffmpeg
    atempo: tempo changes, pitch doesn't) and rescale every word timing to match.

    This is the backstop for the hard length cap: the prompt and script.max_words should already keep
    scripts short enough, but pacing varies (2.25-2.5 words/second measured) and a script at the word
    limit can land a few seconds over. Re-synthesizing at a higher speed would cost ElevenLabs
    characters again; stretching the file costs nothing. Speedups past MAX_SPEEDUP are refused.
    Returns the (possibly rescaled) cues."""
    duration = ffprobe_duration(mp3_path)
    if duration <= max_duration:
        return cues
    factor = duration / (max_duration - _FIT_MARGIN_SEC)
    if factor > MAX_SPEEDUP:
        log.warning(
            "voice is %.1fs, over the %ss cap, and fitting it would need a %.2fx speed-up (limit %.2fx) — "
            "leaving it as is; shorten the script.", duration, max_duration, factor, MAX_SPEEDUP,
        )
        return cues
    fitted = mp3_path.with_name(mp3_path.stem + "_fitted.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(mp3_path), "-filter:a", f"atempo={factor:.5f}",
         "-q:a", "2", str(fitted)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning("could not speed the voice up to fit the cap (%s) — leaving it as is", result.stderr[-300:])
        return cues
    fitted.replace(mp3_path)
    log.info("voice was %.1fs, sped up %.2fx to fit the %ss cap (now %.1fs)",
             duration, factor, max_duration, ffprobe_duration(mp3_path))
    return [WordCue(c.start / factor, c.end / factor, c.content) for c in cues]


async def generate_voice(
    script: str, voice: str, out_dir: Path, cfg: dict | None = None, intro: str | None = None,
) -> tuple[Path, Path]:
    """Synthesize `script` via the configured provider (voice.provider in
    settings.yaml: edge_tts or elevenlabs), writing out_dir/voice.mp3 and
    voice.srt. `voice` is an edge-tts voice name or an ElevenLabs voice ID,
    matching whichever provider is active.

    `intro` (the video's question) is read aloud BEFORE the script, as part of the same take. Its
    captions are left out of voice.srt / voice.words.json (the title card shows that text while it
    is spoken) and its time span goes to voice.intro.json as {"text", "start", "end"}; without an
    intro that file is removed so a stale one from an earlier run can't leak in.

    Returns (mp3_path, srt_path). Logs a warning (does not raise) if the
    resulting audio exceeds cfg['video']['max_duration_sec'] — the caller is
    expected to shorten the script for next time, not have it silently cut.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / "voice.mp3"
    srt_path = out_dir / "voice.srt"
    intro_path = out_dir / "voice.intro.json"
    narration = f"{intro.strip()} {script}" if intro and intro.strip() else script

    voice_cfg = (cfg or {}).get("voice", {})
    provider = voice_cfg.get("provider", "edge_tts")

    if provider == "elevenlabs":
        cues = await asyncio.to_thread(
            _synthesize_elevenlabs,
            narration, voice,
            voice_cfg.get("elevenlabs_model", "eleven_multilingual_v2"),
            voice_cfg.get("elevenlabs_speed", 1.0),
            mp3_path,
        )
    else:
        rate = voice_cfg.get("rate", "+0%")
        cues = await _synthesize_edge(narration, voice, rate, mp3_path)

    video_cfg = (cfg or {}).get("video", {})
    if video_cfg.get("max_duration_sec") and video_cfg.get("enforce_max_duration", True):
        cues = _fit_to_max_duration(mp3_path, cues, float(video_cfg["max_duration_sec"]))

    max_caption_words = (cfg or {}).get("subtitle", {}).get("max_words_per_caption", 6)
    srt_text, word_timings = _cues_to_srt(cues, narration, max_caption_words)
    intro_info = None
    if narration != script:
        word_timings, intro_info = split_intro(word_timings, len(intro.split()))
        srt_text = _timings_to_srt(word_timings)
    srt_path.write_text(srt_text, encoding="utf-8")
    (out_dir / "voice.words.json").write_text(json.dumps(word_timings, ensure_ascii=False), encoding="utf-8")
    if intro_info:
        intro_path.write_text(json.dumps({"text": intro.strip(), **intro_info}, ensure_ascii=False), encoding="utf-8")
    else:
        intro_path.unlink(missing_ok=True)

    duration = ffprobe_duration(mp3_path)
    max_duration = video_cfg.get("max_duration_sec")
    if max_duration and duration > max_duration:
        log.warning(
            "voice.mp3 is %.1fs, longer than max_duration_sec=%ss configured and too long to fit by "
            "speeding it up — shorten the script.",
            duration, max_duration,
        )
    min_duration = video_cfg.get("min_duration_sec")
    if min_duration and duration < min_duration:
        log.warning(
            "voice.mp3 is %.1fs, shorter than min_duration_sec=%ss configured — "
            "the script is probably too short.",
            duration, min_duration,
        )

    last_cue_end = cues[-1].end.total_seconds()
    # edge-tts always leaves ~1s of trailing silence, so only flag a cue that
    # runs past the audio or a much larger gap (words missing from the end).
    if last_cue_end > duration + 0.5 or duration - last_cue_end > 2.5:
        log.warning(
            "srt last cue end (%.2fs) drifts from audio duration (%.2fs)",
            last_cue_end, duration,
        )

    log.info("voice generated (%s): %s (%.1fs), %s (%d words)", provider, mp3_path, duration, srt_path, len(cues))
    return mp3_path, srt_path


if __name__ == "__main__":
    sample_text = (
        "Your brain remembers one harsh criticism longer than ten compliments. "
        "This isn't weakness, it's a survival bias called negativity bias. "
        "Next time a comment stings more than praise felt good, notice it, name it, and let it pass."
    )
    test_out = Path("output") / "_selftest_voice"
    mp3, srt = asyncio.run(
        generate_voice(sample_text, "en-US-AriaNeural", test_out, cfg={"video": {"max_duration_sec": 45}})
    )
    print(f"mp3={mp3}\nsrt={srt}")
    print(srt.read_text(encoding="utf-8")[:400])
