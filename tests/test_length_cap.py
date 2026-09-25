"""Videos are never longer than one minute: the word cap, and the speed-up backstop."""

from datetime import timedelta

import pytest

from src import voice_generator as vg
from src.utils import ffprobe_duration


def cue(word, start, end):
    return vg.WordCue(timedelta(seconds=start), timedelta(seconds=end), word)


def test_the_configured_cap_is_one_minute_and_the_word_limit_fits_under_it(cfg):
    assert cfg["video"]["max_duration_sec"] == 60
    # measured pace with the ElevenLabs voice at 0.85: 2.25-2.5 words per second
    assert cfg["script"]["max_words"] / 2.25 <= cfg["video"]["max_duration_sec"]
    assert cfg["script"]["min_words"] < cfg["script"]["max_words"]


def test_the_speedup_limit_is_gentle():
    assert 1.0 < vg.MAX_SPEEDUP <= 1.3  # past this a voice sounds hurried


@pytest.mark.ffmpeg
def test_a_voice_under_the_cap_is_left_untouched(tmp_path, make_tone):
    mp3 = make_tone(tmp_path / "voice.mp3", seconds=5)
    cues = [cue("a", 0.0, 0.4), cue("b", 0.5, 0.9)]
    before = mp3.read_bytes()
    assert vg._fit_to_max_duration(mp3, cues, 60.0) is cues
    assert mp3.read_bytes() == before


@pytest.mark.ffmpeg
def test_a_voice_a_little_over_the_cap_is_sped_up_to_fit_and_every_timing_follows(tmp_path, make_tone):
    mp3 = make_tone(tmp_path / "voice.mp3", seconds=9)  # cap 8 -> aim 7.5 -> 1.2x
    cues = [cue("first", 0.0, 1.2), cue("second", 4.8, 6.0), cue("last", 7.8, 9.0)]
    fitted = vg._fit_to_max_duration(mp3, cues, 8.0)
    assert ffprobe_duration(mp3) == pytest.approx(7.5, abs=0.2)
    assert ffprobe_duration(mp3) <= 8.0
    assert [c.content for c in fitted] == ["first", "second", "last"]
    assert fitted[1].start.total_seconds() == pytest.approx(4.8 / 1.2)
    assert fitted[-1].end.total_seconds() == pytest.approx(9.0 / 1.2)
    assert fitted[-1].end.total_seconds() <= ffprobe_duration(mp3) + 0.3  # captions still end with the audio
    assert not (tmp_path / "voice_fitted.mp3").exists()  # the temporary file is swapped in, not left behind


@pytest.mark.ffmpeg
def test_a_voice_far_over_the_cap_is_not_squeezed_into_a_chipmunk(tmp_path, make_tone):
    mp3 = make_tone(tmp_path / "voice.mp3", seconds=12)  # cap 8 would need 1.6x
    cues = [cue("a", 0.0, 1.0)]
    before = mp3.read_bytes()
    assert vg._fit_to_max_duration(mp3, cues, 8.0) is cues
    assert mp3.read_bytes() == before and ffprobe_duration(mp3) == pytest.approx(12, abs=0.2)


@pytest.mark.ffmpeg
def test_exactly_at_the_cap_is_fine(tmp_path, make_tone):
    mp3 = make_tone(tmp_path / "voice.mp3", seconds=5)
    duration = ffprobe_duration(mp3)
    cues = [cue("a", 0.0, 1.0)]
    assert vg._fit_to_max_duration(mp3, cues, duration) is cues
