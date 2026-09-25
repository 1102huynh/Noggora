"""Vietnamese captions: sentence grouping, the translation call, re-timing, caching."""

import json
from pathlib import Path

import pytest

from src import llm, subtitle_burner as sb, translate as tr


def phrase(words, start, end):
    """A voice.words.json entry with the words spread evenly over [start, end]."""
    step = (end - start) / len(words)
    return {"start": start, "end": end,
            "words": [{"t": w, "s": start + i * step, "e": start + (i + 1) * step} for i, w in enumerate(words)]}


PHRASES = [
    phrase(["A", "giant", "tree,"], 0.0, 1.5),
    phrase(["yet", "it", "started", "small."], 1.5, 3.5),      # sentence 1: 0.0 - 3.5
    phrase(["Where", "did", "the", "weight", "come", "from?"], 4.2, 6.6),   # sentence 2: 4.2 - 6.6
    phrase(["Not", "the", "soil."], 7.0, 8.0),                  # sentence 3: 7.0 - 8.0
]
SENTENCES = tr.group_sentences(PHRASES)


# --- grouping ------------------------------------------------------------------------------

def test_phrases_are_merged_back_into_sentences_with_their_speaking_span():
    assert [(s["text"], s["start"], s["end"]) for s in SENTENCES] == [
        ("A giant tree, yet it started small.", 0.0, 3.5),
        ("Where did the weight come from?", 4.2, 6.6),
        ("Not the soil.", 7.0, 8.0),
    ]


def test_a_trailing_fragment_without_punctuation_is_kept():
    got = tr.group_sentences(PHRASES + [phrase(["and", "so"], 8.5, 9.0)])
    assert got[-1] == {"text": "and so", "start": 8.5, "end": 9.0}


def test_a_closing_quote_after_the_full_stop_still_ends_the_sentence():
    got = tr.group_sentences([phrase(['He', 'said', '"stop."'], 0, 1), phrase(["Then", "left."], 1.5, 2.5)])
    assert [s["text"] for s in got] == ['He said "stop."', "Then left."]


def test_cache_key_depends_on_the_text_and_the_title_only():
    assert tr.cache_key(SENTENCES, "T?") == tr.cache_key([dict(s, start=99) for s in SENTENCES], "T?")
    assert tr.cache_key(SENTENCES, "T?") != tr.cache_key(SENTENCES, "Other?")
    assert tr.cache_key(SENTENCES, "T?") != tr.cache_key(SENTENCES[:-1], "T?")


# --- the translation call ---------------------------------------------------------------------

@pytest.fixture
def claude(monkeypatch, cfg):
    state = {"reply": None, "calls": []}
    monkeypatch.setattr(llm, "backend", lambda c: "cli")

    def complete(system, user, cfg_, max_tokens=0):
        state["calls"].append((system, user))
        reply = state["reply"]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm, "complete", complete)
    state["cfg"] = cfg
    return state


GOOD = json.dumps({
    "title": "Cái cây nặng từ đâu ra?", "description": "Dòng một\n\n#science #khoahoc",
    "sentences": {"1": "Một cây khổng lồ, vậy mà bắt đầu từ hạt nhỏ xíu.", "2": "Khối lượng đó đến từ đâu?",
                  "3": "Không phải từ đất."},
})


def test_the_prompt_carries_the_sentences_in_order_and_the_rules(claude):
    claude["reply"] = GOOD
    tr.translate_video("A giant tree?", "caption", SENTENCES, claude["cfg"])
    system, user = claude["calls"][0]
    assert "1. A giant tree, yet it started small." in user and "3. Not the soil." in user
    assert "bạn" in system and "exactly THAT sentence" in system and "Điều gì sẽ xảy ra nếu" in system


def test_a_complete_translation_is_returned_in_order(claude):
    claude["reply"] = "Here you go:\n```json\n" + GOOD + "\n```"
    got = tr.translate_video("T?", "d", SENTENCES, claude["cfg"])
    assert got["title"] == "Cái cây nặng từ đâu ra?"
    assert got["sentences"][1] == "Khối lượng đó đến từ đâu?" and len(got["sentences"]) == 3
    assert "#khoahoc" in got["description"]


@pytest.mark.parametrize("reply", [
    json.dumps({"title": "T", "description": "", "sentences": {"1": "a", "3": "c"}}),      # sentence 2 missing
    json.dumps({"title": "", "description": "", "sentences": {"1": "a", "2": "b", "3": "c"}}),  # no title
    json.dumps({"title": "T", "description": "", "sentences": ["a", "b", "c"]}),           # wrong shape
    "sorry, I can't",
    llm.LLMUnavailable("not logged in"),
])
def test_an_incomplete_translation_is_refused_so_english_captions_are_kept(claude, reply):
    claude["reply"] = reply
    assert tr.translate_video("T?", "d", SENTENCES, claude["cfg"]) is None


def test_no_backend_no_translation(monkeypatch, cfg):
    monkeypatch.setattr(llm, "backend", lambda c: None)
    assert tr.translate_video("T?", "d", SENTENCES, cfg) is None


# --- re-timing ---------------------------------------------------------------------------------

def test_a_translated_sentence_is_cut_into_captions_covering_exactly_its_span():
    words = "Một cái cây khổng lồ có thể nặng nhiều tấn, vậy mà nó bắt đầu từ một hạt giống bé xíu.".split()
    chunks = tr._spread(words, 10.0, 15.0, 7)
    assert chunks[0]["start"] == 10.0 and chunks[-1]["end"] == 15.0
    assert all(a["end"] == pytest.approx(b["start"]) for a, b in zip(chunks, chunks[1:]))   # contiguous
    assert [w["t"] for c in chunks for w in c["words"]] == words                            # nothing lost, same order
    assert all(len(c["words"]) <= 8 for c in chunks)                                        # limit + one merged scrap
    assert chunks[0]["words"][0]["s"] == 10.0 and chunks[-1]["words"][-1]["e"] == 15.0


def test_word_timings_inside_a_caption_are_monotonic_and_longer_words_get_longer():
    chunk = tr._spread(["Ai", "khoảng", "khoảng"], 0.0, 3.0, 7)[0]
    starts = [w["s"] for w in chunk["words"]]
    assert starts == sorted(starts)
    durations = [w["e"] - w["s"] for w in chunk["words"]]
    assert durations[1] > durations[0]


def test_captions_follow_the_english_sentences_and_stay_silent_in_the_pauses():
    srt, phrases = tr.vietnamese_captions(SENTENCES, list(json.loads(GOOD)["sentences"].values()), 7)
    spans = [(s["start"], s["end"]) for s in SENTENCES]
    for ph in phrases:
        assert any(a - 1e-6 <= ph["start"] and ph["end"] <= b + 1e-6 for a, b in spans)
    assert srt.count("-->") == len(phrases)
    # nothing is shown between 3.5 and 4.2 (the pause between sentences 1 and 2)
    assert not any(3.5 < ph["start"] < 4.2 or 3.5 < ph["end"] < 4.2 for ph in phrases)


def test_srt_is_valid_utf8_with_the_diacritics_intact():
    srt, _ = tr.vietnamese_captions(SENTENCES, ["Đường đi xa lắm.", "Ừ, đúng rồi!", "Không phải."], 7)
    assert "Đường đi xa lắm." in srt and "Ừ, đúng rồi!" in srt
    assert srt.startswith("1\n00:00:00,000 --> 00:00:03,500")


# --- building the files for a job ---------------------------------------------------------------

@pytest.fixture
def job(tmp_path):
    (tmp_path / "voice.words.json").write_text(json.dumps(PHRASES), encoding="utf-8")
    return tmp_path


def test_a_job_gets_a_vietnamese_srt_and_word_timings(job, claude):
    claude["reply"] = GOOD
    vi = tr.build_for_job(job, "T?", "d", claude["cfg"])
    assert vi["title"] == "Cái cây nặng từ đâu ra?" and "#khoahoc" in vi["description"]
    assert vi["srt"].read_text(encoding="utf-8").count("-->") == len(json.loads(vi["words"].read_text(encoding="utf-8")))
    assert "khổng lồ" in vi["srt"].read_text(encoding="utf-8")


def test_the_translation_is_saved_and_reused_without_asking_claude_again(job, claude):
    claude["reply"] = GOOD
    tr.build_for_job(job, "T?", "d", claude["cfg"])
    assert len(claude["calls"]) == 1 and (job / "translation.vi.json").exists()
    claude["reply"] = llm.LLMUnavailable("would fail if asked again")
    again = tr.build_for_job(job, "T?", "d", claude["cfg"])
    assert again and len(claude["calls"]) == 1


def test_a_changed_script_invalidates_the_saved_translation(job, claude):
    claude["reply"] = GOOD
    tr.build_for_job(job, "T?", "d", claude["cfg"])
    changed = [dict(p) for p in PHRASES]
    changed[0] = phrase(["A", "huge", "tree,"], 0.0, 1.5)
    (job / "voice.words.json").write_text(json.dumps(changed), encoding="utf-8")
    tr.build_for_job(job, "T?", "d", claude["cfg"])
    assert len(claude["calls"]) == 2


def test_a_failed_translation_writes_nothing_and_returns_none(job, claude):
    claude["reply"] = "no"
    assert tr.build_for_job(job, "T?", "d", claude["cfg"]) is None
    assert not (job / "voice.vi.srt").exists() and not (job / "translation.vi.json").exists()


def test_no_word_timings_means_nothing_to_translate(tmp_path, claude):
    claude["reply"] = GOOD
    assert tr.build_for_job(tmp_path, "T?", "d", claude["cfg"]) is None


# --- on screen -------------------------------------------------------------------------------------

def test_the_ass_uses_the_vietnamese_captions_and_title(job, claude, tmp_path):
    claude["reply"] = GOOD
    cfg = claude["cfg"]
    vi = tr.build_for_job(job, "T?", "d", cfg)
    out = sb.srt_to_ass(vi["srt"], cfg["subtitle"], job / "v.ass", 1080, 1920, title=vi["title"],
                        hook_cfg=cfg["hook"], duration=8.0, words_path=vi["words"], category_label="SCIENCE")
    text = out.read_text(encoding="utf-8")
    dialogue = [l for l in text.splitlines() if l.startswith("Dialogue")]
    n_words = sum(len(p["words"]) for p in json.loads(vi["words"].read_text(encoding="utf-8")))
    karaoke = [l for l in dialogue if ",Default," in l]
    assert len(karaoke) == n_words                                  # one event per Vietnamese word
    assert "Cái cây nặng từ đâu ra?" in text and "khổng" in text     # title card + captions, diacritics intact
    assert "A giant tree" not in text                                # no English caption text left


def test_vietnamese_is_the_default_caption_language(cfg):
    assert cfg["subtitle"]["language"] == "vi"
    assert cfg["subtitle"]["max_words_per_caption_vi"] >= 6
    assert cfg["subtitle"]["font_size_vi"] <= cfg["subtitle"]["font_size"]
