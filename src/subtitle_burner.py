"""Convert a .srt (from voice_generator) into a styled .ass subtitle file.

ASS gives us control over font, size, color, outline and screen position that
ffmpeg's plain srt subtitle filter does not — important because burned-in
captions are the single biggest retention lever for this channel.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2

from src.utils import get_logger

log = get_logger("subtitle_burner")

# ASS alignment uses numpad-style positions: 2 = bottom-center, 5 = middle-center.
_ALIGNMENT_MAP = {"bottom_center": 2, "middle_center": 5}


def _parse_ass_color(hex_color: str) -> pysubs2.Color:
    """Parse an ASS-format color string ("&HAABBGGRR") into pysubs2.Color.

    ASS alpha is inverted vs. normal RGBA: 00 = fully opaque, FF = fully transparent.
    """
    digits = hex_color.upper().replace("&H", "").replace("0X", "").zfill(8)
    aa, bb, gg, rr = digits[0:2], digits[2:4], digits[4:6], digits[6:8]
    return pysubs2.Color(int(rr, 16), int(gg, 16), int(bb, 16), int(aa, 16))


def srt_to_ass(
    srt_path: Path, style_cfg: dict, out_path: Path, video_width: int = 1080, video_height: int = 1920
) -> Path:
    """Convert srt_path -> out_path (.ass) styled per style_cfg (subtitle: block of settings.yaml).

    video_width/height set the ASS PlayRes — without it libass assumes a
    384x288 canvas and every coordinate (font size, margins) ends up scaled
    and positioned wrong once rendered onto a real 1080x1920 frame.
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
    style.outline = 3.0
    style.shadow = 0.0
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
    style.marginl = 60
    style.marginr = 60

    subs.styles.clear()
    subs.styles["Default"] = style
    for line in subs:
        line.style = "Default"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out_path))
    log.info("subtitle styled: %s -> %s (%d cues)", srt_path, out_path, len(subs))
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
