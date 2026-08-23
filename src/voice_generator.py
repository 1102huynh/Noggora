"""Text-to-speech via edge-tts: script -> voice.mp3 + voice.srt.

edge-tts talks to Microsoft Edge's public (not officially documented) TTS
endpoint. It needs unrestricted internet egress and will not work inside a
sandboxed environment with blocked egress — see README "Known limitations".
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import edge_tts

from src.utils import ffprobe_duration, get_logger, retry_network

log = get_logger("voice_generator")

# How many words to group into a single burned-in caption line. Word-level
# boundaries are precise, but a caption per word is too choppy to read — a
# short rolling phrase reads better and still tracks speech tightly.
WORDS_PER_CAPTION = 4


def _format_srt_timestamp(td: timedelta) -> str:
    total_ms = int(td.total_seconds() * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def _group_word_boundaries_to_srt(cues: list, words_per_caption: int = WORDS_PER_CAPTION) -> str:
    """Group per-word (offset, duration, text) cues into short caption lines and
    render as SRT text. `cues` items are edge_tts.SubMaker.cues (Subtitle objects
    with .start, .end (timedelta) and .content (str))."""
    lines = []
    index = 1
    for i in range(0, len(cues), words_per_caption):
        group = cues[i : i + words_per_caption]
        start = group[0].start
        end = group[-1].end
        text = " ".join(c.content for c in group)
        lines.append(
            f"{index}\n{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n{text}\n"
        )
        index += 1
    return "\n".join(lines)


@retry_network(max_attempts=3)
async def _synthesize(script: str, voice: str, rate: str, pitch: str, mp3_path: Path) -> "edge_tts.SubMaker":
    """edge-tts occasionally raises NoAudioReceived on an otherwise-healthy
    connection (observed under back-to-back batch requests) — retry with a
    fresh Communicate/stream rather than surfacing a transient hiccup as a
    hard job failure."""
    communicate = edge_tts.Communicate(script, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
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
    return submaker


async def generate_voice(
    script: str, voice: str, out_dir: Path, cfg: dict | None = None, language: str = "en"
) -> tuple[Path, Path]:
    """Synthesize `script` with edge-tts, writing out_dir/voice.mp3 and voice.srt.

    Returns (mp3_path, srt_path). Logs a warning (does not raise) if the
    resulting audio exceeds cfg['video']['max_duration_sec'] — the caller is
    expected to shorten the script for next time, not have it silently cut.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / "voice.mp3"
    srt_path = out_dir / "voice.srt"

    voice_cfg = (cfg or {}).get("voice", {})
    rate = voice_cfg.get(f"rate_{language}", voice_cfg.get("rate", "+0%"))
    pitch = voice_cfg.get(f"pitch_{language}", voice_cfg.get("pitch", "+0Hz"))
    submaker = await _synthesize(script, voice, rate, pitch, mp3_path)

    srt_text = _group_word_boundaries_to_srt(submaker.cues)
    srt_path.write_text(srt_text, encoding="utf-8")

    duration = ffprobe_duration(mp3_path)
    max_duration = (cfg or {}).get("video", {}).get("max_duration_sec")
    if max_duration and duration > max_duration:
        log.warning(
            "voice.mp3 is %.1fs, longer than max_duration_sec=%ss configured — "
            "shorten the script for the next run.",
            duration, max_duration,
        )

    last_cue_end = submaker.cues[-1].end.total_seconds()
    if abs(duration - last_cue_end) > 1.0:
        log.warning(
            "srt last cue end (%.2fs) drifts from audio duration (%.2fs) by > 1s",
            last_cue_end, duration,
        )

    log.info("voice generated: %s (%.1fs), %s (%d captions)", mp3_path, duration, srt_path, len(submaker.cues) // WORDS_PER_CAPTION + 1)
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
