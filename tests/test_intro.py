"""The question (title) is read aloud before the script."""

import asyncio
import json
from datetime import timedelta

import pytest

from src import daily_topic as dt
from src import script_generator as sg
from src import voice_generator as vg
from tests.test_daily_topic import FRESH_SCRIPT, answer

TITLE = "Why does the moon look huge near the horizon?"


# --- when the question is read --------------------------------------------------------------

def test_the_title_is_read_before_a_script_that_starts_with_its_own_hook():
    assert vg.intro_for(TITLE, "Same moon, same distance, completely different size. Here is why.") == TITLE


@pytest.mark.parametrize("script", [
    TITLE + " Because your brain judges size by context.",                       # verbatim
    "Why does the moon look so huge near the horizon? Your brain is fooled.",     # lightly re-worded
    "why does the moon look huge near the horizon. It is an illusion.",           # no capital / mark
])
def test_it_is_not_read_twice_when_the_script_already_opens_with_that_question(script):
    assert vg.intro_for(TITLE, script) is None


def test_a_script_that_merely_mentions_the_same_subject_still_gets_the_intro():
    assert vg.intro_for(TITLE, "The moon is the same size all night. Yet it fools you every evening.") == TITLE


def test_empty_title_or_script():
    assert vg.intro_for("", "Some script.") is None
    assert vg.intro_for(TITLE, "") == TITLE


# --- splitting the spoken question off the captions --------------------------------------------

def phrase(words, start, end):
    step = (end - start) / len(words)
    return {"start": start, "end": end,
            "words": [{"t": w, "s": start + i * step, "e": start + (i + 1) * step} for i, w in enumerate(words)]}


TIMINGS = [
    phrase(["Why", "is", "the", "sky", "blue?"], 0.0, 2.0),          # the intro (5 words)
    phrase(["Look", "up,"], 2.6, 3.4),
    phrase(["and", "you", "see", "it."], 3.4, 5.0),
]


def test_the_intro_captions_are_removed_and_its_span_reported():
    rest, intro = vg.split_intro(TIMINGS, 5)
    assert [" ".join(w["t"] for w in p["words"]) for p in rest] == ["Look up,", "and you see it."]
    assert intro == {"start": 0.0, "end": pytest.approx(2.0)}


def test_an_intro_spanning_two_caption_phrases():
    timings = [phrase(["Why", "does", "it,"], 0, 1), phrase(["really", "happen?"], 1, 2), phrase(["It", "does."], 2.5, 3.5)]
    rest, intro = vg.split_intro(timings, 5)
    assert len(rest) == 1 and intro["end"] == pytest.approx(2.0)


def test_no_intro_leaves_everything_alone():
    assert vg.split_intro(TIMINGS, 0) == (TIMINGS, None)


def test_an_intro_longer_than_the_captions_is_not_split_at_all():
    assert vg.split_intro(TIMINGS, 99) == (TIMINGS, None)


def test_the_remaining_captions_render_as_a_valid_srt():
    rest, _ = vg.split_intro(TIMINGS, 5)
    srt = vg._timings_to_srt(rest)
    assert srt.startswith("1\n00:00:02,600 --> 00:00:03,400\nLook up,")
    assert srt.count("-->") == 2


# --- generate_voice with a spoken intro (TTS faked) -------------------------------------------------

@pytest.fixture
def fake_tts(monkeypatch, make_tone):
    seen = {}

    async def synth(narration, voice, rate, mp3_path):
        seen["narration"] = narration
        make_tone(mp3_path, seconds=12)
        words = narration.split()
        return [vg.WordCue(timedelta(seconds=i * 0.4), timedelta(seconds=i * 0.4 + 0.35), w) for i, w in enumerate(words)]

    monkeypatch.setattr(vg, "_synthesize_edge", synth)
    return seen


@pytest.mark.ffmpeg
def test_the_question_is_spoken_first_and_kept_out_of_the_captions(tmp_path, cfg, fake_tts):
    cfg["voice"]["provider"] = "edge_tts"
    cfg["video"]["max_duration_sec"] = 0
    script = "Your brain compares it to trees. So the horizon moon looks bigger."
    asyncio.run(vg.generate_voice(script, "v", tmp_path, cfg, intro="Why is the moon so big?"))

    assert fake_tts["narration"] == "Why is the moon so big? " + script      # one take: question, then script
    words = json.loads((tmp_path / "voice.words.json").read_text(encoding="utf-8"))
    spoken = [w["t"] for p in words for w in p["words"]]
    assert spoken == script.split()                                          # captions: the script only
    srt = (tmp_path / "voice.srt").read_text(encoding="utf-8")
    assert "Why is the moon so big?" not in srt and srt.count("-->") == len(words)   # srt and json stay aligned

    intro = json.loads((tmp_path / "voice.intro.json").read_text(encoding="utf-8"))
    assert intro["text"] == "Why is the moon so big?"
    # the question is 6 spoken words: the 6th starts at 5 * 0.4 s and lasts 0.35 s
    assert intro["start"] == 0.0 and intro["end"] == pytest.approx(5 * 0.4 + 0.35)
    assert words[0]["start"] >= intro["end"]                                 # captions start after the question


@pytest.mark.ffmpeg
def test_without_an_intro_the_script_is_read_as_is_and_a_stale_intro_file_is_removed(tmp_path, cfg, fake_tts):
    cfg["voice"]["provider"] = "edge_tts"
    cfg["video"]["max_duration_sec"] = 0
    (tmp_path / "voice.intro.json").write_text('{"text": "stale", "start": 0, "end": 9}', encoding="utf-8")
    script = "Only the script. Nothing else."
    asyncio.run(vg.generate_voice(script, "v", tmp_path, cfg))
    assert fake_tts["narration"] == script
    assert not (tmp_path / "voice.intro.json").exists()
    words = json.loads((tmp_path / "voice.words.json").read_text(encoding="utf-8"))
    assert [w["t"] for p in words for w in p["words"]] == script.split()


# --- what Claude is asked for -------------------------------------------------------------------------

def test_the_script_rules_say_the_question_is_already_spoken():
    assert "NEVER restate or re-ask it" in sg.SCRIPT_STRUCTURE and "read aloud before your script" in sg.SCRIPT_STRUCTURE


def test_a_script_that_restates_the_title_is_rejected_so_claude_writes_another():
    repeated = answer(topic=TITLE, script=TITLE + " " + FRESH_SCRIPT)
    with pytest.raises(ValueError, match="repeats the title"):
        dt._parse_entry(repeated, 60, 120)


def test_the_repeat_check_can_be_switched_off_with_the_title_reading(cfg):
    repeated = answer(topic=TITLE, script=TITLE + " " + FRESH_SCRIPT)
    assert dt._parse_entry(repeated, 20, 120, read_title=False)["topic"] == TITLE


def test_the_word_limit_leaves_room_for_the_spoken_question(cfg):
    assert cfg["voice"]["read_title"] is True
    question_allowance = 12  # a typical title is 8-12 words
    assert (cfg["script"]["max_words"] + question_allowance) / 2.25 <= cfg["video"]["max_duration_sec"]
