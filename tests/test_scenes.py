from src.scenes import plan_scenes
from tests.helpers import write_srt

# ten one-phrase sentences, 3 s each, with a 1 s pause between them (~39 s)
TEN_SENTENCES = [(i * 4.0, i * 4.0 + 3.0, f"Sentence number {i}.") for i in range(10)]


def test_scenes_are_contiguous_and_cover_the_whole_track(tmp_path):
    srt = write_srt(tmp_path / "v.srt", TEN_SENTENCES)
    scenes = plan_scenes(srt, 39.0, 5)
    assert len(scenes) == 5
    assert scenes[0].start == 0.0 and scenes[-1].end == 39.0
    for a, b in zip(scenes, scenes[1:]):
        assert a.end == b.start
    assert abs(sum(s.duration for s in scenes) - 39.0) < 1e-6


def test_scenes_are_roughly_even(tmp_path):
    scenes = plan_scenes(write_srt(tmp_path / "v.srt", TEN_SENTENCES), 39.0, 5)
    assert all(6.0 <= s.duration <= 10.0 for s in scenes)  # even share is 7.8 s


def test_cuts_fall_in_the_pause_between_phrases(tmp_path):
    scenes = plan_scenes(write_srt(tmp_path / "v.srt", TEN_SENTENCES), 39.0, 5)
    for scene in scenes[1:]:
        # a pause runs from x.0+3 to (x+1)*4: the cut sits between two sentences, not inside one
        assert (scene.start % 4.0) in (3.5,), scene.start


def test_prefers_a_sentence_boundary_over_a_slightly_more_even_mid_sentence_cut(tmp_path):
    cues = [
        (0.0, 3.0, "First,"),                # sentence 1 continues...
        (3.2, 6.0, "and it goes on."),       # ...and ends here
        (7.0, 10.0, "Second one."),
        (11.0, 14.0, "Third one."),
    ]
    scenes = plan_scenes(write_srt(tmp_path / "v.srt", cues), 14.0, 2)
    # the even midpoint (7 s) is a sentence end; cutting after "First," (3.1 s) would not be
    assert scenes[0].text.endswith("on.")
    assert abs(scenes[1].start - 6.5) < 0.6


def test_a_long_sentence_can_still_be_split_between_its_phrases(tmp_path):
    cues = [(0.0, 5.0, "One very long"), (5.2, 10.0, "sentence that"), (10.2, 15.0, "runs for a while"),
            (15.2, 20.0, "and then ends."), (21.0, 23.0, "Short one.")]
    scenes = plan_scenes(write_srt(tmp_path / "v.srt", cues), 23.0, 4)
    assert len(scenes) == 4  # not collapsed into 2 just because sentence boundaries are scarce


def test_never_more_scenes_than_phrases(tmp_path):
    srt = write_srt(tmp_path / "v.srt", [(0.0, 2.0, "Only one."), (3.0, 5.0, "And another.")])
    assert len(plan_scenes(srt, 6.0, 8)) == 2


def test_single_scene(tmp_path):
    scenes = plan_scenes(write_srt(tmp_path / "v.srt", TEN_SENTENCES), 39.0, 1)
    assert len(scenes) == 1 and scenes[0].duration == 39.0


def test_scene_text_is_what_is_spoken_during_it(tmp_path):
    scenes = plan_scenes(write_srt(tmp_path / "v.srt", TEN_SENTENCES), 39.0, 5)
    assert "Sentence number 0." in scenes[0].text
    assert "Sentence number 9." in scenes[-1].text
    assert "Sentence number 9." not in scenes[0].text
