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
from dataclasses import dataclass
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


async def generate_voice(
    script: str, voice: str, out_dir: Path, cfg: dict | None = None
) -> tuple[Path, Path]:
    """Synthesize `script` via the configured provider (voice.provider in
    settings.yaml: edge_tts or elevenlabs), writing out_dir/voice.mp3 and
    voice.srt. `voice` is an edge-tts voice name or an ElevenLabs voice ID,
    matching whichever provider is active.

    Returns (mp3_path, srt_path). Logs a warning (does not raise) if the
    resulting audio exceeds cfg['video']['max_duration_sec'] — the caller is
    expected to shorten the script for next time, not have it silently cut.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / "voice.mp3"
    srt_path = out_dir / "voice.srt"

    voice_cfg = (cfg or {}).get("voice", {})
    provider = voice_cfg.get("provider", "edge_tts")

    if provider == "elevenlabs":
        cues = await asyncio.to_thread(
            _synthesize_elevenlabs,
            script, voice,
            voice_cfg.get("elevenlabs_model", "eleven_multilingual_v2"),
            voice_cfg.get("elevenlabs_speed", 1.0),
            mp3_path,
        )
    else:
        rate = voice_cfg.get("rate", "+0%")
        cues = await _synthesize_edge(script, voice, rate, mp3_path)

    max_caption_words = (cfg or {}).get("subtitle", {}).get("max_words_per_caption", 6)
    srt_text, word_timings = _cues_to_srt(cues, script, max_caption_words)
    srt_path.write_text(srt_text, encoding="utf-8")
    (out_dir / "voice.words.json").write_text(json.dumps(word_timings, ensure_ascii=False), encoding="utf-8")

    duration = ffprobe_duration(mp3_path)
    video_cfg = (cfg or {}).get("video", {})
    max_duration = video_cfg.get("max_duration_sec")
    if max_duration and duration > max_duration:
        log.warning(
            "voice.mp3 is %.1fs, longer than max_duration_sec=%ss configured — "
            "shorten the script for the next run.",
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
