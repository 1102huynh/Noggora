"""post.youtube.txt — the title and description laid out to paste straight into YouTube.

The channel narrates in English but burns Vietnamese captions, so the post is Vietnamese first:

  * TITLE      the Vietnamese question (optionally "(Vietsub)" — searched for by Vietnamese viewers).
  * DESCRIPTION  Vietnamese lines first (only ~2 lines show in the feed), then a "Vietsub" line, then the
                 English caption (the English question helps English searches and Vietnamese people
                 practising English), then ONE hashtag line: Vietnamese tags first, because the first
                 three hashtags are the ones YouTube shows above the title.
  * ENGLISH VERSION  the same in English, for YouTube Studio's language translations.

YouTube limits worth respecting: a title is at most 100 characters; more than 15 hashtags makes
YouTube ignore ALL of them; Shorts are recognised by #shorts in the title or description.
"""

from __future__ import annotations

import re

_TAG = re.compile(r"#\w+", re.UNICODE)
MAX_TITLE = 100
_VIETSUB_LINE = "Vietsub — giọng đọc tiếng Anh, phụ đề tiếng Việt."


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold(), flags=re.UNICODE).strip()


def split_description(text: str) -> tuple[list[str], list[str]]:
    """(body lines, hashtags) of a post caption. Pure-hashtag lines are pulled out into the tag list;
    every other line stays, with runs of blank lines collapsed and blank edges trimmed."""
    body: list[str] = []
    tags: list[str] = []
    for line in (text or "").replace("\r", "").split("\n"):
        found = _TAG.findall(line)
        if found and not _TAG.sub("", line).strip():
            tags.extend(found)
            continue
        line = line.rstrip()
        if not line and (not body or not body[-1]):
            continue  # no leading blank, no double blank
        body.append(line)
    while body and not body[-1]:
        body.pop()
    return body, tags


def _dedupe(tags: list[str]) -> list[str]:
    seen, out = set(), []
    for t in tags:
        if t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out


def merge_hashtags(vi_tags: list[str], en_tags: list[str], extra_vi: list[str], limit: int) -> list[str]:
    """Vietnamese-only tags first (YouTube shows the first three above the title), then the shared /
    English ones; #shorts is always kept (it is what marks a Short), even when the list is capped."""
    en_keys = {t.casefold() for t in en_tags}
    vi_only = [t for t in vi_tags if t.casefold() not in en_keys]
    ordered = _dedupe(vi_only + extra_vi + [t for t in vi_tags if t.casefold() in en_keys] + en_tags)
    shorts = next((t for t in ordered if t.casefold() == "#shorts"), "#shorts")
    rest = [t for t in ordered if t.casefold() != "#shorts"]
    return rest[: max(limit - 1, 0)] + [shorts]


def build_youtube_post(
    title_en: str, description_en: str | None, title_vi: str | None, description_vi: str | None,
    settings: dict | None = None,
) -> str:
    """The text of post.youtube.txt. `settings` is config `post.youtube`:
    vietsub: description | title | both | none (where the word "Vietsub" goes), max_hashtags."""
    settings = settings or {}
    vietsub = settings.get("vietsub", "description")
    max_tags = int(settings.get("max_hashtags", 12))
    has_vi = bool(title_vi)

    en_body, en_tags = split_description(description_en or "")
    vi_body, vi_tags = split_description(description_vi or "")

    # ---- title
    title = title_vi if has_vi else title_en
    notes: list[str] = []
    if has_vi and vietsub in ("title", "both"):
        suffixed = f"{title} (Vietsub)"
        if len(suffixed) <= MAX_TITLE:
            title = suffixed
        else:
            notes.append(f'Không thêm "(Vietsub)" vào tiêu đề vì sẽ vượt {MAX_TITLE} ký tự (hãy đặt ở mô tả).')
    title_note = f"{len(title)}/{MAX_TITLE} ký tự"
    if len(title) > MAX_TITLE:
        title_note += f" — QUÁ DÀI, YouTube chỉ cho {MAX_TITLE}: hãy rút gọn trước khi đăng"

    # ---- description
    if has_vi:
        lead = list(vi_body)
        if lead and _norm(lead[0]) == _norm(title_vi):  # the feed shows only the first ~2 lines: don't spend one on the title
            lead = lead[1:]
            while lead and not lead[0]:
                lead = lead[1:]
        if vietsub in ("description", "both"):
            lead = [*lead, _VIETSUB_LINE] if lead else [_VIETSUB_LINE]
        tags = merge_hashtags(vi_tags, en_tags, ["#vietsub"] if vietsub != "none" else [], max_tags)
        blocks = ["\n".join(lead).strip("\n"), "\n".join(en_body).strip("\n")]
    else:
        notes.append("Chưa có bản dịch tiếng Việt (dịch lỗi hoặc subtitle.language không phải vi): đây là bản tiếng Anh.")
        tags = merge_hashtags([], en_tags, [], max_tags)
        blocks = ["\n".join(en_body).strip("\n")]
    if tags:
        blocks.append(" ".join(tags))
    description = "\n\n".join(b for b in blocks if b)

    # ---- English version for YouTube Studio's translations
    en_tags_final = merge_hashtags([], en_tags, [], max_tags)
    en_description = "\n\n".join(b for b in ["\n".join(en_body).strip("\n"), " ".join(en_tags_final)] if b)

    out = [
        f"=== TIÊU ĐỀ — dán vào ô Tiêu đề ({title_note}) ===",
        title,
        '^^^ CHỈ dán đúng dòng này vào ô Tiêu đề. TUYỆT ĐỐI không thêm hashtag hay dòng nào khác vào',
        "    tiêu đề — hashtag dính vào tiêu đề từng làm 1 video chỉ còn 34 lượt xem so với 680+ của",
        "    video có tiêu đề sạch cùng ngày. Hashtag để riêng ở khối MÔ TẢ bên dưới.",
        "",
        "=== MÔ TẢ — dán vào ô Mô tả ===",
        description,
    ]
    if has_vi:
        out += [
            "",
            "=== BẢN TIẾNG ANH — YouTube Studio → Phụ đề → Thêm ngôn ngữ → English → dán tiêu đề và mô tả này ===",
            title_en,
            "",
            en_description,
        ]
    out += [
        "",
        "=== GHI CHÚ (không dán) ===",
        "- Trong Chi tiết nâng cao / Ngôn ngữ video: chọn English (giọng đọc là tiếng Anh).",
        "- Ảnh bìa: cover.png cùng thư mục. Phụ đề rời (nếu muốn): voice.vi.srt.",
        f"- Chỉ nên để tối đa 15 hashtag (hơn thế YouTube bỏ qua TẤT CẢ); 3 hashtag đầu hiện phía trên tiêu đề.",
        *[f"- {n}" for n in notes],
    ]
    return "\n".join(out).rstrip() + "\n"
