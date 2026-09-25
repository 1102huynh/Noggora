"""topic_bank (history / migration), llm (backend choice), utils (helpers)."""

import csv

import pytest

from src import llm, topic_bank
from src import utils

# --- topic_bank -----------------------------------------------------------------


def row(i, topic, used_at="", **extra):
    base = {"id": str(i), "topic": topic, "language": "en", "script": f"Script {i}.", "used_at": used_at,
            "visual_keywords": "", "effect": "", "description": ""}
    base.update(extra)
    return base


def test_pick_next_skips_used_rows_and_respects_language(tmp_path):
    bank = tmp_path / "bank.csv"
    topic_bank._write_rows(bank, [row(1, "Used", used_at="2026-01-01"), row(2, "Second", language="vi"), row(3, "Third")])
    assert topic_bank.pick_next(bank, language=None, used_path=tmp_path / "u.csv")["topic"] == "Second"
    assert topic_bank.pick_next(bank, language="en", used_path=tmp_path / "u.csv")["topic"] == "Third"


def test_mark_used_moves_the_row_to_the_archive(tmp_path):
    bank, used = tmp_path / "bank.csv", tmp_path / "used.csv"
    topic_bank._write_rows(bank, [row(1, "One"), row(2, "Two")])
    topic_bank.mark_used(bank, "1", used_path=used)
    assert [r["topic"] for r in topic_bank._read_rows(bank)] == ["Two"]
    archived = topic_bank._read_rows(used)
    assert [r["topic"] for r in archived] == ["One"] and archived[0]["used_at"]


def test_record_generated_used_stores_everything_and_numbers_after_the_highest_id(tmp_path):
    bank, used = tmp_path / "bank.csv", tmp_path / "used.csv"
    topic_bank._write_rows(bank, [row(7, "In bank")])
    topic_bank.record_generated_used(
        {"topic": "New", "language": "en", "script": "S.", "visual_keywords": "a;b", "effect": "spacing effect",
         "description": "Caption"},
        bank_path=bank, used_path=used,
    )
    saved = topic_bank._read_rows(used)[0]
    assert saved["id"] == "8" and saved["effect"] == "spacing effect" and saved["description"] == "Caption"
    assert saved["visual_keywords"] == "a;b" and saved["used_at"]


def test_all_known_rows_covers_the_archive_and_the_bank(tmp_path):
    bank, used = tmp_path / "bank.csv", tmp_path / "used.csv"
    topic_bank._write_rows(bank, [row(1, "In bank")])
    topic_bank._write_rows(used, [row(2, "Already made")])
    assert [r["topic"] for r in topic_bank.all_known_rows(bank, used)] == ["Already made", "In bank"]


def test_missing_files_are_fine(tmp_path):
    assert topic_bank.all_known_rows(tmp_path / "no.csv", tmp_path / "nope.csv") == []


def test_an_old_archive_header_is_upgraded_without_losing_or_shifting_data(tmp_path):
    used = tmp_path / "used.csv"
    used.write_text(
        "id,topic,language,script,used_at\n"                                 # header from before visual_keywords/effect
        "1,Old topic,en,Old script.,2026-01-01\n"
        "2,Half-new,en,S.,2026-02-01,kw1;kw2,moon illusion,A caption\n",      # already written with the new columns
        encoding="utf-8",
    )
    topic_bank._append_used_rows(used, [row(3, "Newest", effect="pareidolia")])
    rows = topic_bank._read_rows(used)
    assert list(rows[0].keys()) == topic_bank._BANK_FIELDNAMES
    assert rows[0]["topic"] == "Old topic" and rows[0]["effect"] == ""
    assert rows[1]["effect"] == "moon illusion" and rows[1]["description"] == "A caption"
    assert rows[2]["effect"] == "pareidolia"


def test_the_effects_pool_renders_full_length_scripts_with_the_keywords_attached():
    import random

    effect = {"name": "spacing effect", "mechanism": "where you remember more when learning is spread out",
              "example": "Studying twenty minutes a day beats a cram", "hook_subject": "cramming vanishes fast",
              "topic": "Why does cramming vanish?", "visual_keywords": "student studying;calendar planner"}
    out = topic_bank._render_from_effect(effect, random.Random(1))
    assert 60 <= len(out["script"].split()) <= 110
    assert out["visual_keywords"] == "student studying;calendar planner" and out["topic"] == effect["topic"]


# --- llm backend choice -----------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    def setup(api_key=None, cli=None):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        if api_key:
            monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
        monkeypatch.setattr(llm, "_cli_path", lambda: cli)

    return setup


@pytest.mark.parametrize("provider,api_key,cli,expected", [
    ("auto", "sk-x", "/bin/claude", "api"),
    ("auto", None, "/bin/claude", "cli"),
    ("auto", None, None, None),
    ("claude_cli", "sk-x", "/bin/claude", "cli"),
    ("claude_cli", "sk-x", None, None),
    ("anthropic_api", "sk-x", "/bin/claude", "api"),
    ("anthropic_api", None, "/bin/claude", None),
    ("manual", "sk-x", "/bin/claude", None),
])
def test_backend_choice(env, provider, api_key, cli, expected):
    env(api_key, cli)
    assert llm.backend({"script": {"provider": provider}}) == expected


def test_complete_without_a_backend_says_so(env):
    env(None, None)
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("sys", "user", {"script": {"provider": "auto"}})


def test_the_cli_call_strips_the_api_key_so_it_bills_the_subscription(env, monkeypatch, tmp_path):
    env("sk-secret", "claude")
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "hello\n", ""

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return Done()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    out = llm.complete("SYSTEM PROMPT", "USER PROMPT", {"script": {"provider": "claude_cli", "cli_model": "sonnet"}})
    assert out == "hello"
    assert "ANTHROPIC_API_KEY" not in seen["kwargs"]["env"]
    assert seen["kwargs"]["input"] == "USER PROMPT"        # via stdin, not argv (Windows .cmd quoting)
    assert "USER PROMPT" not in " ".join(seen["cmd"]) and "SYSTEM PROMPT" not in " ".join(seen["cmd"])
    assert "--tools" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--tools") + 1] == ""


def test_a_failing_cli_raises_llmunavailable(env, monkeypatch):
    env(None, "claude")

    class Bad:
        returncode, stdout, stderr = 1, "", "not logged in"

    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: Bad())
    with pytest.raises(llm.LLMUnavailable, match="not logged in"):
        llm.complete("s", "u", {"script": {"provider": "claude_cli"}})


# --- utils ------------------------------------------------------------------------


def test_slugify_handles_vietnamese_and_punctuation():
    assert utils.slugify("Tại sao não bộ khiến bạn nhớ rõ những lời chê?") == "tai-sao-nao-bo-khien-ban-nho-ro-nhung-loi-che"
    assert utils.slugify("Đường đi!") == "duong-di"
    assert utils.slugify("???") == "topic"
    assert len(utils.slugify("a" * 200)) <= 50


@pytest.mark.ffmpeg
def test_measure_lufs_tracks_the_real_loudness(tmp_path, make_tone):
    quiet = utils.measure_lufs(make_tone(tmp_path / "q.wav", 3, volume_db=-30))
    loud = utils.measure_lufs(make_tone(tmp_path / "l.wav", 3, volume_db=-10))
    assert loud - quiet == pytest.approx(20, abs=0.5)


@pytest.mark.ffmpeg
def test_measure_lufs_of_the_first_seconds_only(tmp_path, make_tone):
    tone = make_tone(tmp_path / "t.wav", 4, volume_db=-20)
    assert utils.measure_lufs(tone, max_seconds=1.5) == pytest.approx(utils.measure_lufs(tone), abs=0.6)


@pytest.mark.ffmpeg
def test_measure_lufs_of_silence_is_none_not_minus_70(tmp_path, make_tone):
    assert utils.measure_lufs(make_tone(tmp_path / "s.wav", 2, volume_db=-120)) is None


@pytest.mark.ffmpeg
def test_probe_helpers(tmp_path, make_image, make_tone):
    assert utils.ffprobe_dimensions(make_image(tmp_path / "i.png", 320, 180)) == (320, 180)
    assert utils.ffprobe_audio_channels(make_tone(tmp_path / "m.wav", 1, channels=1)) == 1
    assert utils.ffprobe_audio_channels(make_tone(tmp_path / "s.wav", 1, channels=2)) == 2
    assert utils.ffprobe_duration(make_tone(tmp_path / "d.wav", 2.5)) == pytest.approx(2.5, abs=0.1)
