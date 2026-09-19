"""Convert a .srt (from voice_generator) into a styled .ass subtitle file.

ASS gives us control over font, size, color, outline and screen position that
ffmpeg's plain srt subtitle filter does not — important because burned-in
captions are the single biggest retention lever for this channel. Besides the
spoken-word captions this also adds the hook title card (the topic, shown big
over the first seconds) and a closing call-to-action line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pysubs2

from src.utils import get_logger

log = get_logger("subtitle_burner")

# ASS alignment uses numpad-style positions: 2 = bottom-center, 5 = middle-center.
_ALIGNMENT_MAP = {"bottom_center": 2, "middle_center": 5}

# The named psychology effect ("sunk cost fallacy", "IKEA effect", ...) is the
# one phrase per video worth making pop, so it is highlighted in the caption.
_KEYWORD_RE = re.compile(
    r"\b(effect|bias|fallacy|rule|illusion|phenomenon|technique|heuristic|hypothesis|"
    r"aversion|gradient|blindness|adaptation|retrospection|error|paradox|"
    r"contagion|loafing|licensing|reactance|prophecy)\b",
    re.IGNORECASE,
)
# Words that can't be part of the effect's name when walking back from the
# keyword ("That's the Zeigarnik effect" -> "Zeigarnik effect").
_NAME_STOPWORDS = {
    "the", "a", "an", "of", "this", "that", "that's", "it's", "is", "called", "as", "known",
    "and", "or", "but", "so", "to", "call", "psychologists", "it", "its", "your", "our", "in",
}


def _parse_ass_color(hex_color: str) -> pysubs2.Color:
    """Parse an ASS-format color string ("&HAABBGGRR") into pysubs2.Color.

    ASS alpha is inverted vs. normal RGBA: 00 = fully opaque, FF = fully transparent.
    """
    digits = hex_color.upper().replace("&H", "").replace("0X", "").zfill(8)
    aa, bb, gg, rr = digits[0:2], digits[2:4], digits[4:6], digits[6:8]
    return pysubs2.Color(int(rr, 16), int(gg, 16), int(bb, 16), int(aa, 16))


def _highlight_keyword(text: str, color_tag: str) -> str:
    """Wrap the first "<name> effect"-style phrase in `text` in a colour override."""
    words = text.split(" ")
    for idx, word in enumerate(words):
        if not _KEYWORD_RE.fullmatch(word.strip(".,;:!?\"'").lower()):
            continue
        start = idx
        while start > 0 and idx - start < 2:
            prev = words[start - 1].strip(".,;:!?\"'").lower()
            if prev in _NAME_STOPWORDS or words[start - 1].endswith((",", ";", ":", ".", "!", "?")):
                break
            start -= 1
        if start == idx:  # bare "effect" with no name before it in this phrase
            return text
        trailing = ""
        while words[idx] and words[idx][-1] in ".,;:!?":
            trailing = words[idx][-1] + trailing
            words[idx] = words[idx][:-1]
        words[start] = "{\\c" + color_tag + "&}" + words[start]
        words[idx] = words[idx] + "{\\r}" + trailing
        return " ".join(words)
    return text


def srt_to_ass(
    srt_path: Path, style_cfg: dict, out_path: Path, video_width: int = 1080, video_height: int = 1920,
    title: str | None = None, hook_cfg: dict | None = None, duration: float | None = None,
) -> Path:
    """Convert srt_path -> out_path (.ass) styled per style_cfg (subtitle: block of settings.yaml).

    video_width/height set the ASS PlayRes — without it libass assumes a
    384x288 canvas and every coordinate (font size, margins) ends up scaled
    and positioned wrong once rendered onto a real 1080x1920 frame.

    `title` + `hook_cfg` add the opening title card and closing CTA line
    (`duration` = total video length, needed to place the CTA at the end).
    """
    subs = pysubs2.load(str(srt_path), encoding="utf-8")
    subs.info["PlayResX"] = str(video_width)
    subs.info["PlayResY"] = str(video_height)

    style = pysubs2.SSAStyle()
    style.fontname = style_cfg.get("font", "Arial")
    style.fontsize = style_cfg.get("font_size", 64)
    style.primarycolor = _parse_ass_color(style_cfg.get("color", "&H00FFFFFF"))
    style.outlinecolor = _parse_ass_color(style_cfg.get("outline_color", "&H00000000"))
    style.borderstyle = 1
    style.outline = 4.0
    style.shadow = 1.0
    style.bold = True

    position = style_cfg.get("position", "bottom_center")
    style.alignment = _ALIGNMENT_MAP.get(position, 2)
    if position == "bottom_center":
        # Platform UI (channel name, title, description, like/comment/share
        # buttons) is drawn by YouTube/TikTok/Instagram *on top of* the video
        # itself in roughly the bottom quarter — captions must clear that
        # zone or they render half-hidden behind it (verified against a real
        # YouTube Shorts screenshot: 300px/1920 = 15.6% was not enough).
        safe_margin_percent = style_cfg.get("safe_margin_percent", 28)
        style.marginv = round(video_height * safe_margin_percent / 100)
    else:
        style.marginv = 0
    style.marginl = 70
    style.marginr = 70

    subs.styles.clear()
    subs.styles["Default"] = style

    highlight = style_cfg.get("highlight_color")
    color_tag = None
    if highlight:
        digits = highlight.upper().replace("&H", "").rstrip("&").zfill(8)
        color_tag = "&H" + digits[2:]  # \c takes &HBBGGRR& (no alpha byte)
    for line in subs:
        line.style = "Default"
        text = _highlight_keyword(line.text, color_tag) if color_tag else line.text
        line.text = "{\\fad(70,0)}" + text

    hook_cfg = hook_cfg or {}
    font = style.fontname
    if title and hook_cfg.get("title_card", False):
        title_style = pysubs2.SSAStyle()
        title_style.fontname = font
        title_style.fontsize = hook_cfg.get("title_font_size", 92)
        title_style.primarycolor = _parse_ass_color("&H00FFFFFF")
        title_style.outlinecolor = _parse_ass_color("&H00000000")
        title_style.borderstyle = 1
        title_style.outline = 6.0
        title_style.shadow = 2.0
        title_style.bold = True
        title_style.alignment = 5
        title_style.marginl = 90
        title_style.marginr = 90
        subs.styles["Title"] = title_style
        sec = float(hook_cfg.get("title_sec", 2.8))
        subs.append(pysubs2.SSAEvent(
            start=0, end=int(sec * 1000), style="Title",
            text="{\\fad(350,350)}" + title.replace("\n", " "),
        ))

    cta = hook_cfg.get("cta_text")
    if cta and duration:
        cta_style = pysubs2.SSAStyle()
        cta_style.fontname = font
        cta_style.fontsize = hook_cfg.get("cta_font_size", 56)
        cta_style.primarycolor = _parse_ass_color(style_cfg.get("highlight_color", "&H00FFFFFF"))
        cta_style.outlinecolor = _parse_ass_color("&H00000000")
        cta_style.borderstyle = 1
        cta_style.outline = 4.0
        cta_style.shadow = 1.0
        cta_style.bold = True
        cta_style.alignment = 8  # top-center: the bottom is taken by captions + platform UI
        cta_style.marginv = 300
        cta_style.marginl = 70
        cta_style.marginr = 70
        subs.styles["CTA"] = cta_style
        cta_sec = float(hook_cfg.get("cta_sec", 2.5))
        subs.append(pysubs2.SSAEvent(
            start=int(max(duration - cta_sec, 0) * 1000), end=int(duration * 1000), style="CTA",
            text="{\\fad(300,0)}" + cta,
        ))

    subs.sort()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out_path))
    log.info("subtitle styled: %s -> %s (%d events)", srt_path, out_path, len(subs))
    return out_path


if __name__ == "__main__":
    from src.utils import load_config

    test_srt = Path("output") / "_selftest_voice" / "voice.srt"
    if not test_srt.exists():
        raise SystemExit(
            f"{test_srt} not found — run `python -m src.voice_generator` first to generate it."
        )
    cfg = load_config()
    out = srt_to_ass(
        test_srt, cfg["subtitle"], test_srt.with_suffix(".ass"),
        video_width=cfg["video"]["width"], video_height=cfg["video"]["height"],
    )
    print(out.read_text(encoding="utf-8")[:600])
