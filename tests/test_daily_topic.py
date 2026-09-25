import json

import pytest

from src import daily_topic as dt
from src import llm, topic_bank

BANK_ROW = {
    "id": "1", "topic": "Why do you keep watching a bad movie to the end?", "language": "en",
    "script": "Halfway through, you already know the movie is bad, yet you keep watching. That's the sunk cost fallacy. "
              "Time already spent can't be recovered.",
    "used_at": "", "visual_keywords": "", "effect": "", "description": "",
}
USED_ROW = {
    "id": "2", "topic": "Why do you expect your partner to just know what's wrong?", "language": "en",
    "script": "Have you ever gone silent, hoping someone would notice? That's the illusion of transparency.",
    "used_at": "2026-09-01T00:00:00", "visual_keywords": "", "effect": "illusion of transparency", "description": "",
}
KNOWN = [BANK_ROW, USED_ROW]

# 90 words, no overlap with the rows above
FRESH_SCRIPT = (
    "You reread the same chapter for hours and still blank on the test. Spread the same hours across a week and the "
    "facts stick. Psychologists call this the spacing effect. Short gaps make your brain work to retrieve, and "
    "retrieval builds memory. Tonight, stop early and quiz yourself tomorrow morning. It feels slower, but it works "
    "far better than one long night of cramming. Try it for one week and compare how much you remember. Your future "
    "self will thank you for it."
)


def entry(**over):
    base = {"topic": "Why does studying a little each day beat one long night?", "effect": "spacing effect",
            "script": FRESH_SCRIPT}
    base.update(over)
    return base


# --- find_duplicate ----------------------------------------------------------

def test_a_genuinely_new_video_is_accepted():
    assert dt.find_duplicate(entry(), KNOWN) is None


def test_same_topic_is_rejected_ignoring_case_and_punctuation():
    reason = dt.find_duplicate(entry(topic="WHY DO YOU KEEP WATCHING A BAD MOVIE TO THE END"), KNOWN)
    assert reason and "same topic" in reason


@pytest.mark.parametrize("effect", ["illusion of transparency", "Illusion of Transparency", "transparency effect",
                                    "the transparency illusion"])
def test_same_effect_is_rejected_however_it_is_worded(effect):
    reason = dt.find_duplicate(entry(effect=effect), KNOWN)
    assert reason and "already" in reason


def test_effect_named_in_a_hand_written_bank_script_counts_as_used():
    # the bank row has no effect column, but its script says "the sunk cost fallacy"
    reason = dt.find_duplicate(entry(effect="sunk cost fallacy"), KNOWN)
    assert reason and "bad movie" in reason


def test_reworded_topic_is_rejected():
    reason = dt.find_duplicate(entry(topic="Why do you keep watching a terrible movie until the end?"), KNOWN)
    assert reason and "topic" in reason


def test_script_that_mostly_repeats_an_old_one_is_rejected():
    repeated = BANK_ROW["script"] + " " + BANK_ROW["script"]
    reason = dt.find_duplicate(entry(script=repeated), KNOWN)
    assert reason


def test_unrelated_videos_do_not_trip_the_similarity_thresholds():
    other = entry(topic="Why do you see faces in wall outlets?", effect="pareidolia",
                  script="Ever looked at a wall socket and felt it stared back? That's pareidolia, the brain's habit of "
                         "finding faces in things that are not faces. Two holes and a slot are enough to set it off.")
    assert dt.find_duplicate(other, KNOWN) is None


# --- _parse_entry -------------------------------------------------------------

def answer(**over):
    data = {"effect": "spacing effect", "topic": "Why does spacing beat cramming?", "mood": "curious",
            "script": FRESH_SCRIPT, "description": "Caption.\n\n#psychology",
            "visual_keywords": [f"shot {i}" for i in range(8)]}
    data.update(over)
    return json.dumps(data)


def test_parse_a_valid_answer():
    parsed = dt._parse_entry(answer(), 75, 110)
    assert parsed["mood"] == "curious" and parsed["language"] == "en"
    assert parsed["visual_keywords"].count(";") == 7  # 8 keywords


def test_parse_finds_the_json_inside_a_code_fence_and_chatter():
    parsed = dt._parse_entry("Sure! Here you go:\n```json\n" + answer() + "\n```\nHope that helps.", 75, 110)
    assert parsed["topic"].startswith("Why does spacing")


def test_an_unknown_mood_is_dropped_not_fatal():
    assert dt._parse_entry(answer(mood="joyful"), 75, 110)["mood"] == ""


@pytest.mark.parametrize("field", ["effect", "topic", "script"])
def test_missing_required_fields_are_rejected(field):
    with pytest.raises(ValueError):
        dt._parse_entry(answer(**{field: ""}), 75, 110)


def test_script_outside_the_word_range_is_rejected():
    with pytest.raises(ValueError, match="words"):
        dt._parse_entry(answer(script="Too short."), 75, 110)


def test_script_must_end_with_sentence_punctuation():
    with pytest.raises(ValueError, match="punctuation"):
        dt._parse_entry(answer(script=FRESH_SCRIPT.rstrip(".") + " and then"), 75, 110)


def test_no_json_at_all_is_rejected():
    with pytest.raises(ValueError):
        dt._parse_entry("I cannot do that.", 75, 110)


# --- generate_daily_entry (LLM faked) -----------------------------------------

@pytest.fixture
def files(tmp_path):
    bank, used = tmp_path / "bank.csv", tmp_path / "used.csv"
    topic_bank._write_rows(bank, [BANK_ROW])
    topic_bank._write_rows(used, [USED_ROW])
    return bank, used


@pytest.fixture
def fake_llm(monkeypatch, cfg):
    cfg["script"]["fact_check"] = False  # one LLM call per attempt keeps the scripted replies simple
    calls = []
    replies = []

    def complete(system, user, cfg_, max_tokens=0):
        calls.append(user)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm, "backend", lambda c: "cli")
    monkeypatch.setattr(llm, "complete", complete)
    return calls, replies, cfg


def test_returns_a_good_first_answer(files, fake_llm):
    calls, replies, cfg = fake_llm
    replies.append(answer())
    got = dt.generate_daily_entry(cfg, *files)
    assert got and got["effect"] == "spacing effect" and len(calls) == 1


def test_a_duplicate_is_retried_and_the_reason_is_fed_back(files, fake_llm):
    calls, replies, cfg = fake_llm
    replies += [answer(effect="sunk cost fallacy", topic="Why quit what you paid for?"), answer()]
    got = dt.generate_daily_entry(cfg, *files)
    assert got["effect"] == "spacing effect"
    assert len(calls) == 2
    assert "REJECTED as a repeat" in calls[1] and "sunk cost fallacy" in calls[1]


def test_an_unusable_answer_is_retried_too(files, fake_llm):
    calls, replies, cfg = fake_llm
    replies += ["not json at all", answer()]
    assert dt.generate_daily_entry(cfg, *files)["effect"] == "spacing effect"
    assert len(calls) == 2


def test_gives_up_after_max_attempts_so_the_caller_can_fall_back(files, fake_llm):
    calls, replies, cfg = fake_llm
    replies += [answer(effect="sunk cost fallacy")] * 3
    assert dt.generate_daily_entry(cfg, *files) is None
    assert len(calls) == 3


def test_no_backend_means_no_entry_and_no_calls(files, monkeypatch, cfg):
    monkeypatch.setattr(llm, "backend", lambda c: None)
    assert dt.generate_daily_entry(cfg, *files) is None


def test_a_failing_cli_returns_none_instead_of_raising(files, fake_llm):
    calls, replies, cfg = fake_llm
    replies.append(llm.LLMUnavailable("not logged in"))
    assert dt.generate_daily_entry(cfg, *files) is None
