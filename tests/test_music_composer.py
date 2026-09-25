import json

import pytest

from src import music_composer as mc


@pytest.fixture
def lib(tmp_path):
    library = tmp_path / "music"
    mc.ensure_library_folders(library)
    return library


@pytest.fixture(autouse=True)
def no_real_synthesis(monkeypatch):
    """The synthesized-pad fallback would shell out to ffmpeg; a stub file is enough here."""

    def fake(mood, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"synth")
        return out_path

    monkeypatch.setattr(mc, "_synthesize_mood_track", fake)


def add(lib, mood, *names):
    for name in names:
        (lib / mood / name).write_bytes(b"x")


def pick(lib, tmp_path, mood="mysterious", record=True, state=None):
    state = state or tmp_path / "state.json"
    choice = mc.choose_music("Some topic", "", tmp_path / "gen", lib, state, mood=mood)
    if record:
        mc.record_used(choice, state)
    return choice


def test_folders_are_created_for_every_mood(lib):
    assert sorted(p.name for p in lib.iterdir()) == sorted(mc.MOODS)


def test_tracks_are_ordered_by_name_with_numbers_compared_as_numbers(lib):
    add(lib, "warm", "10 - c.mp3", "2 - b.mp3", "01 - a.mp3", "B.MP3", "notes.txt", "cover.jpg")
    assert [p.name for p in mc.list_tracks(lib / "warm")] == ["01 - a.mp3", "2 - b.mp3", "10 - c.mp3", "B.MP3"]


def test_one_track_per_video_in_order_then_wrap_around(lib, tmp_path):
    add(lib, "mysterious", "01 - a.mp3", "02 - b.mp3", "03 - c.mp3")
    names = [pick(lib, tmp_path).path.name for _ in range(5)]
    assert names == ["01 - a.mp3", "02 - b.mp3", "03 - c.mp3", "01 - a.mp3", "02 - b.mp3"]


def test_state_is_kept_per_mood(lib, tmp_path):
    add(lib, "mysterious", "a.mp3", "b.mp3")
    add(lib, "warm", "x.mp3", "y.mp3")
    assert pick(lib, tmp_path, "mysterious").path.name == "a.mp3"
    assert pick(lib, tmp_path, "warm").path.name == "x.mp3"  # warm has its own turn
    assert pick(lib, tmp_path, "mysterious").path.name == "b.mp3"


def test_nothing_is_recorded_until_told_so_a_failed_render_keeps_its_turn(lib, tmp_path):
    add(lib, "mysterious", "a.mp3", "b.mp3")
    first = pick(lib, tmp_path, record=False).path.name
    again = pick(lib, tmp_path, record=False).path.name
    assert first == again == "a.mp3"


def test_a_deleted_last_used_track_still_continues_sensibly(lib, tmp_path):
    add(lib, "mysterious", "a.mp3", "c.mp3")
    (tmp_path / "state.json").write_text(json.dumps({"mysterious": "b.mp3"}))  # b was removed
    assert pick(lib, tmp_path).path.name == "c.mp3"


def test_new_files_slot_into_the_order(lib, tmp_path):
    add(lib, "mysterious", "a.mp3", "c.mp3")
    assert pick(lib, tmp_path).path.name == "a.mp3"
    add(lib, "mysterious", "b.mp3")  # added after a was used
    assert pick(lib, tmp_path).path.name == "b.mp3"


def test_an_empty_mood_folder_falls_back_to_the_synthesized_pad(lib, tmp_path):
    choice = pick(lib, tmp_path, "curious")
    assert choice.source == "synth" and choice.path.name.startswith("mood_curious")


def test_loose_files_are_used_only_when_the_mood_folder_is_empty(lib, tmp_path):
    (lib / "loose.mp3").write_bytes(b"x")
    add(lib, "warm", "warm.mp3")
    assert pick(lib, tmp_path, "warm").path.name == "warm.mp3"
    assert pick(lib, tmp_path, "tense").path.name == "loose.mp3"


def test_synthesized_music_leaves_no_state(lib, tmp_path):
    pick(lib, tmp_path, "curious")
    assert not (tmp_path / "state.json").exists()


def test_an_invalid_mood_name_falls_back_to_the_topic_keywords(lib, tmp_path):
    choice = mc.choose_music("Why do you fear losing things?", "", tmp_path / "gen", lib, None, mood="bogus")
    assert choice.mood == "tense"


def test_a_missing_library_dir_is_fine(tmp_path):
    choice = mc.choose_music("Why?", "", tmp_path / "gen", tmp_path / "nope", None)
    assert choice.source == "synth"


# --- mood from the topic --------------------------------------------------------

@pytest.mark.parametrize("topic,mood", [
    ("Why do you fear losing more than gaining?", "tense"),
    ("Why do you remember the old days better?", "curious"),
    ("Why do you keep watching a bad movie to the end?", "playful"),
    ("Why does the moon look huge near the horizon?", "mysterious"),
])
def test_keyword_mood_from_the_topic(topic, mood):
    assert mc.pick_mood(topic) == mood


def test_a_topic_with_no_keywords_gets_the_default_mood():
    assert mc.pick_mood("Why?") == mc.DEFAULT_MOOD


def test_the_script_is_only_consulted_when_the_topic_says_nothing():
    assert mc.pick_mood("Why?", "This is about fear and pressure.") == "tense"
    assert mc.pick_mood("Why do you trust friends?", "fear fear fear pressure") == "warm"


def test_every_synthesized_mood_uses_a_tremolo_ffmpeg_accepts():
    # ffmpeg's tremolo rejects f < 0.1; 'warm' once had 0.08 and silently rendered without music
    assert all(params["tremolo"] >= 0.1 for params in mc._MOODS.values())
