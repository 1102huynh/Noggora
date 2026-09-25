"""post.youtube.txt: the title + description laid out for pasting into YouTube."""

import pytest

from src import post_text as pt

TITLE_EN = "Where does a giant tree's enormous weight actually come from?"
DESC_EN = (
    "Where does a giant tree's enormous weight really come from?\n"
    "Most of it is pulled straight out of thin air, not the soil.\n"
    "Did you expect that answer?\n\n"
    "#science #sciencefacts #didyouknow #photosynthesis #shorts"
)
TITLE_VI = "Trọng lượng khổng lồ của một cái cây thật ra đến từ đâu?"
DESC_VI = (
    "Trọng lượng khổng lồ của một cái cây thật ra đến từ đâu?\n"
    "Phần lớn được hút thẳng từ không khí, chứ không phải từ đất.\n"
    "Bạn có đoán ra không?\n\n"
    "#science #sciencefacts #didyouknow #photosynthesis #shorts #khoahoc #thucvat"
)


def build(**over):
    args = dict(title_en=TITLE_EN, description_en=DESC_EN, title_vi=TITLE_VI, description_vi=DESC_VI, settings=None)
    args.update(over)
    return pt.build_youtube_post(args["title_en"], args["description_en"], args["title_vi"], args["description_vi"],
                                 args["settings"])


def section(text, header_start):
    """The lines under a '=== ... ===' header until the next one."""
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("===") and header_start in l) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("===")), len(lines))
    return "\n".join(lines[start:end]).strip("\n")


# --- splitting a caption -------------------------------------------------------------------------

def test_hashtag_lines_are_pulled_out_and_text_lines_kept():
    body, tags = pt.split_description("Line one\nLine two\n\n#a #b #c")
    assert body == ["Line one", "Line two"] and tags == ["#a", "#b", "#c"]


def test_a_hashtag_inside_a_sentence_stays_in_the_text():
    body, tags = pt.split_description("Loved it #wow so much\n#tag1")
    assert body == ["Loved it #wow so much"] and tags == ["#tag1"]


def test_blank_runs_are_collapsed_and_edges_trimmed():
    body, _ = pt.split_description("\n\nA\n\n\n\nB\n\n")
    assert body == ["A", "", "B"]


def test_vietnamese_hashtags_are_recognised():
    _, tags = pt.split_description("#khoahọc #đờisống")
    assert tags == ["#khoahọc", "#đờisống"]


# --- hashtags ---------------------------------------------------------------------------------------

def test_vietnamese_only_tags_come_first_and_shorts_last():
    tags = pt.merge_hashtags(["#science", "#khoahoc", "#thucvat", "#shorts"], ["#science", "#shorts", "#facts"], ["#vietsub"], 12)
    assert tags[:3] == ["#khoahoc", "#thucvat", "#vietsub"]      # the three YouTube shows above the title
    assert tags[-1] == "#shorts" and len(tags) == len({t.casefold() for t in tags})


def test_duplicates_ignore_case():
    tags = pt.merge_hashtags(["#Science"], ["#science", "#SCIENCE"], [], 12)
    assert [t.casefold() for t in tags].count("#science") == 1


def test_the_cap_counts_shorts_and_never_drops_it():
    many = [f"#tag{i}" for i in range(30)]
    tags = pt.merge_hashtags([], many + ["#shorts"], [], 12)
    assert len(tags) == 12 and tags[-1] == "#shorts"


def test_shorts_is_added_when_the_model_forgot_it():
    assert pt.merge_hashtags([], ["#a"], [], 12)[-1] == "#shorts"


# --- the layout ------------------------------------------------------------------------------------------

def test_the_title_is_the_vietnamese_question_with_its_length_shown():
    text = build()
    assert section(text, "TIÊU ĐỀ") == TITLE_VI
    assert f"({len(TITLE_VI)}/100 ký tự)" in text


def test_the_description_leads_in_vietnamese_without_repeating_the_title():
    desc = section(build(), "MÔ TẢ")
    first_lines = desc.split("\n")[:2]
    assert first_lines[0].startswith("Phần lớn được hút thẳng từ không khí")   # the title line was dropped
    assert TITLE_VI not in desc
    assert "Bạn có đoán ra không?" in desc


def test_the_english_caption_follows_the_vietnamese_one_and_hashtags_come_last():
    desc = section(build(), "MÔ TẢ")
    assert desc.index("Vietsub") < desc.index("Where does a giant tree's enormous weight really") < desc.index("#")
    last = desc.split("\n")[-1]
    assert last.startswith("#") and last.split()[0] in ("#khoahoc", "#thucvat") and last.endswith("#shorts")


def test_an_english_version_is_included_for_youtube_studio_translations():
    text = build()
    block = section(text, "BẢN TIẾNG ANH")
    assert block.split("\n")[0] == TITLE_EN and "Did you expect that answer?" in block
    assert "#khoahoc" not in block and block.endswith("#shorts")   # Vietnamese tags don't belong in the English version


def test_notes_explain_the_studio_settings_and_are_not_part_of_the_pasted_text():
    text = build()
    assert "GHI CHÚ (không dán)" in text and "English" in section(text, "GHI CHÚ")
    assert "GHI CHÚ" not in section(text, "MÔ TẢ")


# --- the word "Vietsub" ----------------------------------------------------------------------------------------

def test_vietsub_in_the_description_by_default_and_as_a_tag():
    text = build()
    assert "Vietsub — giọng đọc tiếng Anh, phụ đề tiếng Việt." in section(text, "MÔ TẢ")
    assert "#vietsub" in section(text, "MÔ TẢ") and "Vietsub" not in section(text, "TIÊU ĐỀ")


def test_vietsub_in_the_title_instead():
    text = build(settings={"vietsub": "title"})
    assert section(text, "TIÊU ĐỀ") == TITLE_VI + " (Vietsub)"
    assert "Vietsub —" not in section(text, "MÔ TẢ")


def test_vietsub_in_both_places():
    text = build(settings={"vietsub": "both"})
    assert section(text, "TIÊU ĐỀ").endswith("(Vietsub)") and "Vietsub —" in section(text, "MÔ TẢ")


def test_vietsub_off():
    text = build(settings={"vietsub": "none"})
    assert "Vietsub" not in section(text, "TIÊU ĐỀ")
    assert "Vietsub —" not in section(text, "MÔ TẢ") and "#vietsub" not in section(text, "MÔ TẢ")


def test_the_title_suffix_is_skipped_when_it_would_break_the_100_character_limit():
    long_title = ("Điều gì sẽ xảy ra nếu " + "Mặt Trăng biến mất đột ngột khỏi bầu trời đêm của chúng ta " * 2)[:95] + "?"
    assert 95 < len(long_title) <= 100 and len(long_title) + len(" (Vietsub)") > 100
    text = build(title_vi=long_title, settings={"vietsub": "title"})
    assert section(text, "TIÊU ĐỀ") == long_title and 'Không thêm "(Vietsub)"' in text


def test_a_title_over_the_limit_is_flagged():
    text = build(title_vi="x" * 120)
    assert "120/100 ký tự — QUÁ DÀI" in text


# --- when there is no Vietnamese ----------------------------------------------------------------------------------

def test_without_a_translation_the_post_is_english_and_says_so():
    text = build(title_vi=None, description_vi=None)
    assert section(text, "TIÊU ĐỀ") == TITLE_EN
    assert "BẢN TIẾNG ANH" not in text and "Vietsub" not in section(text, "MÔ TẢ")
    assert "Chưa có bản dịch tiếng Việt" in text
    assert section(text, "MÔ TẢ").endswith("#shorts")


def test_a_missing_english_caption_does_not_crash():
    text = build(description_en=None)
    assert section(text, "TIÊU ĐỀ") == TITLE_VI and "#shorts" in text


def test_a_caption_that_is_only_the_title_line_still_produces_a_description():
    text = build(description_vi=TITLE_VI + "\n\n#khoahoc")
    assert "Vietsub —" in section(text, "MÔ TẢ")


def test_the_output_is_plain_utf8_text_ending_in_a_newline():
    text = build()
    assert text.endswith("\n") and not text.endswith("\n\n")
    text.encode("utf-8")
    assert "\r" not in text
