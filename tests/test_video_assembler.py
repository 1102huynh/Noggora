"""Real ffmpeg runs at thumbnail size. The filter graph is the most fragile part of
the project (a missing comma or a mono/stereo mix-up only shows up when ffmpeg
parses it), so every audio configuration is rendered for real."""

import json
import re
import subprocess

import pytest

from src import subtitle_burner, video_assembler as va
from src.utils import ffprobe_dimensions, ffprobe_duration, measure_lufs
from tests.helpers import write_srt

pytestmark = pytest.mark.ffmpeg

W, H = 270, 480


@pytest.fixture
def small_cfg(cfg, tmp_path):
    cfg["video"].update(width=W, height=H, fps=15)
    cfg["visuals"]["fade_sec"] = 0.2
    cfg["sfx"]["dir"] = str(tmp_path / "sfx")
    cfg["sfx"]["enabled"] = True
    cfg["sfx"]["transitions"] = False
    return cfg


@pytest.fixture
def job(small_cfg, tmp_path, make_tone, make_image, make_video):
    """Two clips (a portrait video and a landscape still), a mono voice, music, subtitles."""
    # A tone at -4 dB measures ~ -25 LUFS, the level of a real ElevenLabs voice (edge-tts ~ -21).
    # Much quieter than that isn't representative: loudnorm then applies a completely different gain.
    voice = make_tone(tmp_path / "voice_raw.wav", 3.2, freq=300, volume_db=-4, channels=1)  # TTS voices are mono
    # Real speech starts a moment in (~0.1-0.4 s of silence): loudnorm's gain is still high when the
    # title "hit" plays at t=0, which is how a real video once reached +0.1 dBFS true peak.
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(voice_raw := tmp_path / "voice_raw.wav"),
                    "-af", "adelay=400,apad=whole_dur=3.6", str(voice)], check=True)
    music = make_tone(tmp_path / "music.wav", 5.0, freq=110, volume_db=-8, channels=2)
    clips = [make_video(tmp_path / "portrait.mp4", W, H, 2.0), make_image(tmp_path / "wide.png", 640, 360)]
    srt = write_srt(tmp_path / "v.srt", [(0.2, 1.6, "Hello there,"), (1.8, 3.4, "this is a test.")])
    ass = subtitle_burner.srt_to_ass(
        srt, {**small_cfg["subtitle"], "karaoke": False}, tmp_path / "v.ass", video_width=W, video_height=H,
        title="A title?", hook_cfg=small_cfg["hook"], duration=3.6,
    )
    return {"cfg": small_cfg, "voice": voice, "music": music, "clips": clips, "ass": ass, "dir": tmp_path}


def streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,channels,width,height",
         "-of", "json", str(path)], capture_output=True, text=True, check=True,
    ).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def true_peak(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                          "-vn", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)[-1])


COMBOS = [
    pytest.param(False, False, False, id="voice only"),
    pytest.param(True, False, False, id="voice + music"),
    pytest.param(False, True, False, id="voice + title hit (single sfx)"),
    pytest.param(True, True, False, id="voice + music + title hit"),
    pytest.param(True, True, True, id="voice + music + hit + transition whooshes"),
    pytest.param(False, True, True, id="voice + hit + whooshes"),
]


@pytest.mark.parametrize("with_music,with_sfx,transitions", COMBOS)
def test_every_audio_configuration_renders(job, with_music, with_sfx, transitions):
    cfg = job["cfg"]
    cfg["sfx"]["enabled"] = with_sfx
    cfg["sfx"]["transitions"] = transitions
    out = va.assemble_video(
        job["clips"], job["voice"], job["ass"], job["music"] if with_music else None,
        job["dir"] / "final.mp4", cfg, durations=[1.8, 1.8],
    )
    s = streams(out)
    assert (s["video"]["width"], s["video"]["height"]) == (W, H)
    assert s["video"]["codec_name"] == "h264" and s["audio"]["codec_name"] == "aac"
    assert s["audio"]["channels"] == 2  # a mono voice must not make the whole mix mono
    assert ffprobe_duration(out) == pytest.approx(3.6, abs=0.3)


def test_true_peak_stays_under_the_ceiling_with_the_title_hit_and_music(job):
    """Property check on the finished file: true peak <= the -1.5 dBFS design ceiling (+0.2 tolerance).

    Known limit of this test: a real 40 s video once measured +0.1 dBFS because the title "hit" at t=0
    rides loudnorm's early gain over 0 dBFS (sfx off: -1.4; on, no limiter: +0.1). That overshoot did
    NOT reproduce in a 3.6 s thumbnail render, so this test alone would not notice the limiter being
    removed — test_the_limiter_follows_loudnorm_in_the_filter_graph guards that."""
    out = va.assemble_video(job["clips"], job["voice"], job["ass"], job["music"], job["dir"] / "final.mp4",
                            job["cfg"], durations=[1.8, 1.8])
    assert true_peak(out) <= -1.3


def test_the_limiter_follows_loudnorm_in_the_filter_graph(job, monkeypatch):
    seen = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        seen.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(va.subprocess, "run", spy)
    for with_music in (False, True):
        seen.clear()
        va.assemble_video(job["clips"], job["voice"], job["ass"], job["music"] if with_music else None,
                          job["dir"] / "final.mp4", job["cfg"], durations=[1.8, 1.8])
        graph = next(c[c.index("-filter_complex") + 1] for c in seen if "-filter_complex" in c)
        audio_out = graph[graph.index("loudnorm"): graph.index("[aout]")]
        assert "alimiter" in audio_out and audio_out.index("loudnorm") < audio_out.index("alimiter"), graph


def test_final_loudness_lands_near_the_target_whatever_the_voice_level(job, make_tone):
    # levels of real TTS voices: edge-tts ~ -21 LUFS, ElevenLabs ~ -24 LUFS (a tone at -4 / +2 dB
    # here). Far quieter input than that is beyond what a single-pass loudnorm can lift.
    levels = []
    for db in (-4, 2):
        voice = make_tone(job["dir"] / f"v{db}.wav", 3.6, freq=300, volume_db=db)
        out = va.assemble_video(job["clips"], voice, job["ass"], job["music"], job["dir"] / f"f{db}.mp4",
                                job["cfg"], durations=[1.8, 1.8])
        levels.append(measure_lufs(out))
    assert levels[0] == pytest.approx(levels[1], abs=2.0)  # ~6 dB apart in, within ~2 dB out
    assert levels[1] == pytest.approx(job["cfg"]["audio"]["target_lufs"], abs=3.0)


def test_music_sits_the_configured_amount_under_the_voice(job, make_tone):
    music_cfg = {"below_voice_db": 12}
    gain = va._music_gain_db(job["voice"], job["music"], 3.6, music_cfg)
    boosted = job["dir"] / "boosted.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(job["music"]), "-af", f"volume={gain}dB", str(boosted)],
                   check=True)
    assert measure_lufs(job["voice"]) - measure_lufs(boosted) == pytest.approx(12, abs=0.5)


def test_a_loud_and_a_quiet_track_end_up_at_the_same_level(job, make_tone):
    # 27 dB apart. (Beyond +20 dB of lift the gain is deliberately capped, so a track quieter than
    # ~-45 LUFS is not brought fully up — a sane limit, not something to assert here.)
    quiet = make_tone(job["dir"] / "quiet.wav", 5, freq=200, volume_db=-30, channels=2)
    loud = make_tone(job["dir"] / "loud2.wav", 5, freq=200, volume_db=-3, channels=2)
    cfg = {"below_voice_db": 12}
    levels = []
    for track in (quiet, loud):
        gain = va._music_gain_db(job["voice"], track, 3.6, cfg)
        levels.append(measure_lufs(track, 3.6) + gain)
    assert levels[0] == pytest.approx(levels[1], abs=0.3)


def test_unmeasurable_music_falls_back_to_a_fixed_gain(job, make_tone):
    silent = make_tone(job["dir"] / "silent.wav", 3, volume_db=-120, channels=2)
    assert va._music_gain_db(job["voice"], silent, 3.0, {"fallback_volume_db": -21}) == -21


def test_landscape_clips_get_the_blurred_backdrop_and_portrait_ones_the_pan(job):
    portrait = va._clip_filter(0, job["clips"][0], W, H, 15, 3.0, 1.12)
    landscape = va._clip_filter(1, job["clips"][1], W, H, 15, 3.0, 1.12)
    assert "boxblur" in landscape and "overlay" in landscape
    assert "boxblur" not in portrait and "crop=" in portrait and "min(t/" in portrait


def test_the_cover_image_is_made_at_full_frame_size(job):
    out = va.make_cover(job["clips"][0], "Why is the sky blue?", job["dir"] / "cover.png", job["cfg"])
    assert out.exists() and ffprobe_dimensions(out) == (W, H)
    assert not (job["dir"] / "cover.ass").exists()  # the temporary subtitle file is cleaned up


def test_the_cover_works_from_a_still_image_too(job):
    out = va.make_cover(job["clips"][1], "Why is the sky blue?", job["dir"] / "cover2.png", job["cfg"])
    assert ffprobe_dimensions(out) == (W, H)


def test_at_least_one_clip_is_required(job):
    with pytest.raises(ValueError):
        va.assemble_video([], job["voice"], job["ass"], None, job["dir"] / "x.mp4", job["cfg"])
