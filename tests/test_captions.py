"""voice_generator's caption chunking + subtitle_burner's highlighting / karaoke."""

import json
from datetime import timedelta
from types import SimpleNamespace

from src import subtitle_burner as sb
from src import voice_generator as vg
from tests.helpers import write_srt


def cues_for(script: str, step: float = 0.4):
    return [
        vg.WordCue(timedelta(seconds=i * step), timedelta(seconds=i * step + step * 0.9), w)
        for i, w in enumerate(script.split())
    ]


# --- chunking -----------------------------------------------------------------

def test_a_short_sentence_is_one_chunk():
    assert vg._chunk_words("Next time, ask.".split(), 6) == [["Next", "time,", "ask."]]


def test_chunks_break_at_the_scripts_own_commas():
    words = "That's the sunk cost fallacy, time and money already spent can't be recovered.".split()
    chunks = [" ".join(c) for c in vg._chunk_words(words, 6)]
    assert chunks[0] == "That's the sunk cost fallacy,"


def test_no_chunk_is_longer_than_the_limit_plus_a_merged_scrap():
    words = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
             "seventeen eighteen.").split()
    assert all(len(c) <= 7 for c in vg._chunk_words(words, 6))
    assert sum(len(c) for c in vg._chunk_words(words, 6)) == len(words)  # nothing dropped


def test_chunking_never_loses_or_reorders_words():
    words = "Ever wonder why the moon looks massive near the horizon, but tiny once it rises overhead?".split()
    flat = [w for chunk in vg._chunk_words(words, 5) for w in chunk]
    assert flat == words


def test_srt_has_one_caption_per_chunk_and_word_timing_lines_up():
    script = "Ever wonder why the moon looks big? This is the moon illusion, and your brain is fooled."
    srt, timing = vg._cues_to_srt(cues_for(script), script, 6)
    assert srt.count("-->") == len(timing)
    assert sum(len(t["words"]) for t in timing) == len(script.split())
    for phrase in timing:
        starts = [w["s"] for w in phrase["words"]]
        assert starts == sorted(starts) and phrase["start"] == starts[0]


def test_captions_keep_the_scripts_own_punctuation():
    script = "This is the moon illusion, and your brain is fooled."
    srt, _ = vg._cues_to_srt(cues_for(script), script, 6)
    assert "illusion," in srt and "fooled." in srt


def test_a_caption_is_held_until_the_next_phrase_of_the_same_sentence():
    script = "One two three four five six seven eight nine ten."
    _, timing = vg._cues_to_srt(cues_for(script), script, 5)
    assert timing[0]["end"] == timing[1]["start"]


# --- highlighting ---------------------------------------------------------------

def test_keyword_span_finds_the_effect_name():
    assert sb._keyword_span(["That's", "the", "Zeigarnik", "effect."]) == (2, 3)
    assert sb._keyword_span(["the", "sunk", "cost", "fallacy,"]) == (1, 3)


def test_keyword_span_needs_a_name_before_the_keyword():
    assert sb._keyword_span(["the", "effect"]) is None
    assert sb._keyword_span(["you", "see", "proof", "they're", "right"]) is None


def test_highlight_wraps_only_the_name_and_keeps_trailing_punctuation():
    out = sb._highlight_keyword("That's the sunk cost fallacy,", "&H00D7FF")
    assert out == "That's the {\\c&H00D7FF&}sunk cost fallacy{\\r},"


def test_color_tag_drops_the_alpha_byte():
    assert sb._color_tag("&H0000D7FF") == "&H00D7FF"


def test_karaoke_makes_one_event_per_word_with_only_that_word_coloured():
    words = [{"t": "That's", "s": 0.0, "e": 0.3}, {"t": "the", "s": 0.4, "e": 0.6},
             {"t": "moon", "s": 0.7, "e": 0.9}, {"t": "illusion.", "s": 1.0, "e": 1.4}]
    line = SimpleNamespace(start=0, end=1500)
    events = sb._karaoke_events(line, {"words": words}, "&HFFC850", "&H00D7FF")
    assert len(events) == 4
    assert events[0].start == 0 and events[-1].end == 1500
    assert events[0].text.startswith("{\\fad(70,0)}") and not events[1].text.startswith("{\\fad")
    assert events[1].text.count("&H00D7FF&") == 1  # exactly one active word
    assert all(a.end == b.start for a, b in zip(events, events[1:]))  # no gaps -> no flicker
    # the named effect keeps its own colour while another word is active
    assert "{\\c&HFFC850&}moon{\\r}" in events[0].text


def test_every_event_shows_the_whole_phrase_so_nothing_builds_up(cfg):
    words = [{"t": t, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, t in enumerate("Ever wonder why the moon".split())]
    events = sb._karaoke_events(SimpleNamespace(start=0, end=2000), {"words": words}, None, "&H00D7FF")
    plain = ["".join(part.split("}")[-1] if "{" in part else part for part in e.text.split("{\\r}")) for e in events]
    for e in events:
        stripped = e.text.replace("{\\fad(70,0)}", "")
        for tag in ("{\\c&H00D7FF&}", "{\\r}"):
            stripped = stripped.replace(tag, "")
        assert stripped == "Ever wonder why the moon"


def test_srt_to_ass_uses_karaoke_when_timings_match(tmp_path, cfg):
    script = "This is the moon illusion, and your brain is fooled."
    srt_text, timing = vg._cues_to_srt(cues_for(script), script, 6)
    (tmp_path / "v.srt").write_text(srt_text, encoding="utf-8")
    (tmp_path / "v.words.json").write_text(json.dumps(timing), encoding="utf-8")
    out = sb.srt_to_ass(tmp_path / "v.srt", cfg["subtitle"], tmp_path / "v.ass", words_path=tmp_path / "v.words.json")
    dialogue = [l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")]
    assert len(dialogue) == len(script.split())


def test_srt_to_ass_falls_back_to_static_captions_when_timings_do_not_match(tmp_path, cfg):
    write_srt(tmp_path / "v.srt", [(0, 1, "One phrase."), (1.5, 2.5, "Two phrases.")])
    (tmp_path / "v.words.json").write_text(json.dumps([{"start": 0, "end": 1, "words": []}]), encoding="utf-8")
    out = sb.srt_to_ass(tmp_path / "v.srt", cfg["subtitle"], tmp_path / "v.ass", words_path=tmp_path / "v.words.json")
    dialogue = [l for l in out.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")]
    assert len(dialogue) == 2


def test_title_card_and_cta_are_added_at_the_right_times(tmp_path, cfg):
    write_srt(tmp_path / "v.srt", [(0, 1, "Hello there."), (1.5, 30, "Goodbye now.")])
    cfg["subtitle"]["karaoke"] = False
    out = sb.srt_to_ass(tmp_path / "v.srt", cfg["subtitle"], tmp_path / "v.ass", title="Why is the sky blue?",
                        hook_cfg=cfg["hook"], duration=30.0)
    lines = out.read_text(encoding="utf-8").splitlines()
    title = next(l for l in lines if ",Title," in l)
    cta = next(l for l in lines if ",CTA," in l)
    assert title.startswith("Dialogue: 0,0:00:00.00,0:00:02.80") and "Why is the sky blue?" in title
    assert cfg["hook"]["cta_text"] in cta and cta.split(",")[2].startswith("0:00:30.00")  # ends with the video
