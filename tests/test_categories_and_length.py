"""Category rotation + content rules, and everything that has to cope with videos of 30 s .. 3 min."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import categories, daily_topic as dt, llm, script_generator as sg, subtitle_burner as sb
from src import topic_bank, video_assembler as va
from src import visual_fetcher as vf
from tests.test_daily_topic import FRESH_SCRIPT, answer

IDS = ["psychology", "science", "space", "world", "technology", "whatif"]


# --- config sanity ----------------------------------------------------------------

def test_the_channel_has_the_six_banner_categories_in_order(cfg):
    assert [c["id"] for c in categories.get_categories(cfg)] == IDS
    assert [c["label"] for c in categories.get_categories(cfg)] == [
        "PSYCHOLOGY", "SCIENCE", "SPACE", "WORLD", "TECHNOLOGY", "WHAT IF?"]


def test_every_category_is_fully_configured(cfg):
    for c in categories.get_categories(cfg):
        assert c["brief"].strip() and c["hashtags"] and all(h.startswith("#") for h in c["hashtags"])
        assert c["id"] in categories.FOOTAGE_HINTS, f"no footage hint for {c['id']}"
        assert vf.NICHE_BY_CATEGORY[c["id"]], f"no fallback footage themes for {c['id']}"


def test_brand_color_is_the_banner_gold(cfg):
    # #FAB032 written the way ASS wants it (&HAABBGGRR)
    assert cfg["content"]["brand_color"] == "&H0032B0FA" == cfg["subtitle"]["active_word_color"]
    assert cfg["content"]["tagline"] == "One question. One amazing answer." == cfg["hook"]["cta_text"]


def test_missing_content_config_falls_back_to_psychology():
    assert [c["id"] for c in categories.get_categories({})] == ["psychology"]


# --- rotation ---------------------------------------------------------------------

def rows(*ids):
    return [{"topic": f"t{i}", "category": c} for i, c in enumerate(ids)]


def test_with_no_history_the_first_configured_category_goes_first(cfg):
    assert categories.pick_next(cfg, [])["id"] == "psychology"


def test_the_rotation_visits_every_category_before_repeating_any(cfg):
    history, order = [], []
    for _ in range(12):
        pick = categories.pick_next(cfg, history)["id"]
        order.append(pick)
        history.append({"topic": pick, "category": pick})
    assert order[:6] == IDS and order[6:] == IDS


def test_the_least_recently_used_category_wins_whatever_the_history(cfg):
    history = rows("space", "world", "psychology", "space", "technology", "whatif", "psychology")
    # last seen: space@3, world@1, psychology@6, technology@4, whatif@5, science never
    assert categories.pick_next(cfg, history)["id"] == "science"
    history += rows("science")
    assert categories.pick_next(cfg, history)["id"] == "world"  # oldest last-use is now world@1


def test_skipping_days_does_not_skew_the_rotation(cfg):
    # nothing depends on dates: the history is the only state
    history = rows("psychology", "science")
    assert categories.pick_next(cfg, history)["id"] == "space"


def test_rows_without_a_category_are_ignored(cfg):
    history = [{"topic": "pre-written bank row", "category": ""}, {"topic": "x"}] + rows("psychology")
    assert categories.pick_next(cfg, history)["id"] == "science"


def test_a_category_can_be_forced_and_an_unknown_one_is_an_error(cfg):
    assert categories.pick_next(cfg, rows("space"), forced="space")["id"] == "space"
    with pytest.raises(ValueError, match="unknown category"):
        categories.pick_next(cfg, [], forced="cooking")


def test_a_category_removed_from_config_is_never_picked(cfg):
    cfg["content"]["categories"] = [c for c in cfg["content"]["categories"] if c["id"] != "world"]
    history, seen = [], set()
    for _ in range(10):
        pick = categories.pick_next(cfg, history)["id"]
        seen.add(pick)
        history.append({"topic": pick, "category": pick})
    assert "world" not in seen and len(seen) == 5


# --- what Claude is asked for and what is accepted -----------------------------------

def test_the_title_must_be_a_question():
    with pytest.raises(ValueError, match="question"):
        dt._parse_entry(answer(topic="The moon is big near the horizon"), 60, 420)


def test_a_what_if_title_must_start_with_what_if(cfg):
    whatif = categories.by_id(cfg, "whatif")
    with pytest.raises(ValueError, match="What if"):
        dt._parse_entry(answer(topic="Why would the Moon vanish?"), 60, 420, whatif)
    ok = dt._parse_entry(answer(topic="What if the Moon vanished tonight?"), 60, 420, whatif)
    assert ok["category"] == "whatif"


def test_subject_is_stored_in_the_effect_field_for_the_history_and_duplicate_check():
    data = json.loads(answer())
    data.pop("effect")
    data["subject"] = "neutron stars"
    assert dt._parse_entry(json.dumps(data), 60, 420)["effect"] == "neutron stars"


def test_short_subjects_are_not_matched_against_old_script_text():
    # "Mars" turns up in plenty of unrelated scripts; only distinctive subjects use that rule
    known = [{"topic": "Why is the sky red at sunset?", "script": "On Mars the sunset is blue.", "effect": ""}]
    new = {"topic": "Why is Mars red?", "effect": "Mars", "script": "Iron oxide dust covers the planet surface. " * 8}
    assert dt.find_duplicate(new, known) is None


@pytest.fixture
def fake_llm(monkeypatch, cfg):
    cfg["script"]["fact_check"] = False
    calls, replies = [], []

    def complete(system, user, cfg_, max_tokens=0):
        calls.append((system, user))
        return replies.pop(0)

    monkeypatch.setattr(llm, "backend", lambda c: "cli")
    monkeypatch.setattr(llm, "complete", complete)
    return calls, replies, cfg


@pytest.fixture
def empty_files(tmp_path):
    return tmp_path / "bank.csv", tmp_path / "used.csv"


def test_the_prompt_carries_the_category_the_tagline_and_the_length_rule(empty_files, fake_llm):
    calls, replies, cfg = fake_llm
    replies.append(answer(topic="What if the Moon vanished tonight?"))
    got = dt.generate_daily_entry(cfg, *empty_files, category="whatif")
    system = calls[0][0]
    assert got["category"] == "whatif"
    assert "WHAT IF?" in system and 'starts with "What if"' in system or 'MUST start with "What if"' in system
    assert "One question. One amazing answer." in system
    lo, hi = cfg["script"]["min_words"], cfg["script"]["max_words"]
    assert "LENGTH FOLLOWS THE CONTENT" in system and f"between {lo} and {hi} words" in system
    assert "ONE MINUTE" in system and f"{hi} words is a hard ceiling" in system
    assert "#whatif" in system                      # the category's broad hashtags go into the caption rules
    assert categories.FOOTAGE_HINTS["whatif"] in system


def test_the_rotation_decides_the_category_when_none_is_forced(tmp_path, fake_llm):
    calls, replies, cfg = fake_llm
    bank, used = tmp_path / "bank.csv", tmp_path / "used.csv"
    topic_bank._write_rows(used, [
        {"id": "1", "topic": "A?", "language": "en", "script": "S.", "category": c} for c in
        ["psychology", "science", "space", "world", "technology"]])
    replies.append(answer(topic="What if all ice melted tomorrow?"))
    assert dt.generate_daily_entry(cfg, bank, used)["category"] == "whatif"


def test_earlier_videos_are_listed_with_their_category_in_the_prompt(tmp_path, fake_llm):
    calls, replies, cfg = fake_llm
    bank, used = tmp_path / "bank.csv", tmp_path / "used.csv"
    topic_bank._write_rows(used, [{"id": "1", "topic": "How do neutron stars form?", "language": "en", "script": "S.",
                                   "effect": "neutron stars", "category": "space"}])
    replies.append(answer())
    dt.generate_daily_entry(cfg, bank, used)
    assert "How do neutron stars form? [neutron stars, space]" in calls[0][0]


def test_a_fact_checker_reject_sends_the_topic_back_with_the_reason(empty_files, fake_llm, monkeypatch):
    calls, replies, cfg = fake_llm
    replies += [answer(), answer(effect="spacing effect 2", topic="Why does sleep lock in what you learn?")]
    verdicts = [
        {"verdict": "reject", "wow": 4, "issues": ["mechanism still debated"], "script": "", "description": ""},
        {"verdict": "ok", "wow": 5, "issues": [], "script": FRESH_SCRIPT, "description": "d"},
    ]
    monkeypatch.setattr(dt.script_generator, "fact_check", lambda *a, **k: verdicts.pop(0))
    got = dt.generate_daily_entry(cfg, *empty_files)
    assert got["topic"].startswith("Why does sleep") and got["wow"] == 5
    assert "REJECTED by the fact-checker: mechanism still debated" in calls[1][1]


# --- the fact-check pass itself ------------------------------------------------------

@pytest.fixture
def checker(monkeypatch, cfg):
    reply = {}
    monkeypatch.setattr(llm, "backend", lambda c: "cli")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps(reply["json"]) if "json" in reply else reply["raw"])
    cfg["script"]["min_wow"] = 3
    return reply, cfg


def check(cfg, script=FRESH_SCRIPT):
    return sg.fact_check("Why does spacing beat cramming?", script, "caption", cfg, "PSYCHOLOGY")


def test_a_settled_and_surprising_answer_passes(checker):
    reply, cfg = checker
    reply["json"] = {"verdict": "ok", "wow": 4, "issues": []}
    got = check(cfg)
    assert got["verdict"] == "ok" and got["wow"] == 4 and got["script"] == FRESH_SCRIPT


def test_a_contested_core_answer_is_rejected_not_hedged(checker):
    reply, cfg = checker
    reply["json"] = {"verdict": "reject", "wow": 5, "issues": ["core mechanism is debated"]}
    got = check(cfg)
    assert got["verdict"] == "reject" and got["script"] == FRESH_SCRIPT  # original untouched


def test_a_dull_answer_is_rejected_even_if_true(checker):
    reply, cfg = checker
    reply["json"] = {"verdict": "ok", "wow": 2, "issues": []}
    got = check(cfg)
    assert got["verdict"] == "reject" and any("wow 2/5" in i for i in got["issues"])


def test_a_dull_revision_is_rejected_too(checker):
    reply, cfg = checker
    reply["json"] = {"verdict": "revised", "wow": 1, "issues": [], "script": FRESH_SCRIPT + " Extra.", "description": "d"}
    assert check(cfg)["verdict"] == "reject"


def test_a_revision_within_limits_replaces_the_script(checker):
    reply, cfg = checker
    revised = FRESH_SCRIPT.replace("Psychologists call this", "Scientists call this")
    reply["json"] = {"verdict": "revised", "wow": 4, "issues": ["wording"], "script": revised, "description": "new caption"}
    got = check(cfg)
    assert got["verdict"] == "revised" and got["script"] == revised and got["description"] == "new caption"


def test_a_revision_over_the_word_limit_is_discarded(checker):
    reply, cfg = checker
    cfg["script"]["max_words"] = 95
    reply["json"] = {"verdict": "revised", "wow": 4, "issues": [], "script": FRESH_SCRIPT + " " + FRESH_SCRIPT[:200],
                     "description": "d"}
    assert check(cfg)["verdict"] == "skipped"


def test_garbage_from_the_checker_never_breaks_the_video(checker):
    reply, cfg = checker
    reply["raw"] = "I think it is fine!"
    got = check(cfg)
    assert got["verdict"] == "skipped" and got["script"] == FRESH_SCRIPT


def test_the_checker_can_be_switched_off(checker):
    reply, cfg = checker
    cfg["script"]["fact_check"] = False
    assert check(cfg)["verdict"] == "skipped"


def test_description_rules_use_the_categorys_hashtags():
    text = sg.description_rules(["#space", "#astronomy", "#universe"])
    assert "#space #astronomy #universe" in text and "#shorts" in text and "350 characters" in text


# --- the category tag on screen --------------------------------------------------------

def dialogue(path):
    return [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")]


def test_the_tag_is_a_gold_label_with_a_gold_bar_above_the_title(tmp_path, cfg):
    from tests.helpers import write_srt

    write_srt(tmp_path / "v.srt", [(0, 1, "Hello there."), (1.5, 30, "Goodbye now.")])
    cfg["subtitle"]["karaoke"] = False
    out = sb.srt_to_ass(tmp_path / "v.srt", cfg["subtitle"], tmp_path / "v.ass", title="Why is the sky blue?",
                        hook_cfg=cfg["hook"], duration=30.0, category_label="SPACE")
    tag = [l for l in dialogue(out) if ",Tag," in l]
    # #FAB032 in ASS colour order (blue, green, red) is 32B0FA
    assert len(tag) == 2 and "SPACE" in tag[0] and "\\p1" in tag[1] and "\\c&H32B0FA&" in tag[1]
    assert all(l.split(",")[1] == "0:00:00.00" and l.split(",")[2].startswith("0:00:02.80") for l in tag)  # title-card time


def test_no_tag_without_a_category_or_when_switched_off(tmp_path, cfg):
    from tests.helpers import write_srt

    write_srt(tmp_path / "v.srt", [(0, 1, "Hello there.")])
    cfg["subtitle"]["karaoke"] = False
    without = sb.srt_to_ass(tmp_path / "v.srt", cfg["subtitle"], tmp_path / "a.ass", title="Q?",
                            hook_cfg=cfg["hook"], duration=5.0)
    assert not [l for l in dialogue(without) if ",Tag," in l]
    cfg["hook"]["category_tag"] = False
    off = sb.srt_to_ass(tmp_path / "v.srt", cfg["subtitle"], tmp_path / "b.ass", title="Q?",
                        hook_cfg=cfg["hook"], duration=5.0, category_label="SPACE")
    assert not [l for l in dialogue(off) if ",Tag," in l]


# --- scenes for long videos ----------------------------------------------------------------

def scene(text, start=0.0, end=8.0):
    return SimpleNamespace(text=text, start=start, end=end, duration=end - start)


def test_scene_keywords_come_back_one_per_scene_keyed_by_number(monkeypatch, cfg):
    monkeypatch.setattr(llm, "backend", lambda c: "cli")
    seen = {}
    reply = '```json\n{"1": "night sky", "2": "  telescope lens ", "3": "rocket launch"}\n```'
    monkeypatch.setattr(llm, "complete", lambda s, u, c, max_tokens=0: (seen.update(user=u, system=s), reply)[1])
    got = vf.plan_scene_keywords([scene("The sky."), scene("A lens."), scene("Liftoff.")], "How big is space?", "SPACE", cfg)
    assert got == ["night sky", "telescope lens", "rocket launch"]
    assert "1. The sky." in seen["user"] and "exactly 3 excerpts" in seen["system"]


def test_an_extra_or_missing_key_no_longer_throws_the_whole_plan_away(monkeypatch, cfg):
    # a bare list once came back with 12 items for 11 scenes and had to be discarded entirely
    monkeypatch.setattr(llm, "backend", lambda c: "cli")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"1": "a", "2": "b", "4": "d", "9": "extra"}')
    got = vf.plan_scene_keywords([scene("x"), scene("y"), scene("z"), scene("w")], "T?", "", cfg)
    assert got == ["a", "b", "", "d"]  # the skipped scene 3 just has no phrase; the stray key 9 is ignored


@pytest.mark.parametrize("reply", ['{"1": "only one"}', "no json here", '["a", "b", "c"]', '{"1": "", "2": "", "3": "c"}'])
def test_scene_keywords_that_are_mostly_missing_are_ignored(monkeypatch, cfg, reply):
    monkeypatch.setattr(llm, "backend", lambda c: "cli")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: reply)
    assert vf.plan_scene_keywords([scene("x"), scene("y"), scene("z")], "T?", "", cfg) is None


def test_no_backend_no_scene_keywords(monkeypatch, cfg):
    monkeypatch.setattr(llm, "backend", lambda c: None)
    assert vf.plan_scene_keywords([scene("x")], "T?", "", cfg) is None


def test_the_scenes_own_phrase_is_searched_first():
    queries = vf.scene_queries(1, "Text about a rocket.", ["video-wide"], n_scenes=3, scene_phrase="rocket launch pad")
    assert queries[0] == "rocket launch pad"


def test_fetching_a_long_video_gives_every_scene_its_own_distinct_clip(tmp_path, cfg, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "x")
    monkeypatch.setenv("PIXABAY_API_KEY", "y")
    used_queries = []

    def fake_search(name):
        def search(query, key):
            used_queries.append(query)
            base = abs(hash(query)) % 100000
            if name == "pexels":
                return [{"id": base + k, "url": f"https://www.pexels.com/video/{query.replace(' ', '-')}-{base + k}/",
                         "duration": 10, "width": 1080, "height": 1920,
                         "video_files": [{"link": "l", "width": 1080, "height": 1920}]} for k in range(3)]
            return [{"id": base + k, "tags": query.replace(" ", ", "), "duration": 10,
                     "videos": {"l": {"url": "u", "width": 1080, "height": 1920}}} for k in range(3)]
        return search

    monkeypatch.setattr(vf, "_search_pexels", fake_search("pexels"))
    monkeypatch.setattr(vf, "_search_pixabay", fake_search("pixabay"))
    monkeypatch.setattr(vf, "_download", lambda url, dest: (Path(dest).write_bytes(b"x"), Path(dest))[1])
    n = 23  # ~ a 3-minute video
    scenes = [scene(f"scene {i} narration", i * 8.0, (i + 1) * 8.0) for i in range(n)]
    phrases = [f"phrase number {i}" for i in range(n)]
    cfg["visuals"]["fetch_workers"] = 6
    clips = vf.fetch_visuals("T?", "script", tmp_path, n, cfg, scenes=scenes, scene_keywords=phrases, category="space")
    assert len(clips) == n and len({c.name for c in clips}) == n  # 23 scenes, 23 different clips, in order
    assert all(f"phrase number {i}" in used_queries for i in range(n))
    assert {c.name.split("_")[0] for c in clips} == {"pexels", "pixabay"}  # both sources still alternate


# --- rendering a long video ---------------------------------------------------------------------

@pytest.fixture
def small(cfg, tmp_path):
    cfg["video"].update(width=270, height=480, fps=15)
    cfg["visuals"]["fade_sec"] = 0.2
    cfg["sfx"]["enabled"] = False
    return cfg


def ass_for(tmp_path, cfg, seconds):
    from tests.helpers import write_srt

    srt = write_srt(tmp_path / "v.srt", [(0.2, seconds - 0.5, "A caption.")])
    return sb.srt_to_ass(srt, {**cfg["subtitle"], "karaoke": False}, tmp_path / "v.ass", video_width=270, video_height=480)


@pytest.mark.ffmpeg
def test_music_shorter_than_the_video_is_extended_by_crossfaded_copies(small, tmp_path, make_tone, make_video):
    voice = make_tone(tmp_path / "voice.wav", 20, freq=300, volume_db=-4, channels=1)
    music = make_tone(tmp_path / "music.wav", 7, freq=110, volume_db=-8, channels=2)  # 20 s video needs 5 copies
    clip = make_video(tmp_path / "c.mp4", 270, 480, 2.0)
    seen = []
    real_run = subprocess.run
    import src.video_assembler as module

    def spy(cmd, *a, **k):
        seen.append(cmd)
        return real_run(cmd, *a, **k)

    module.subprocess.run = spy
    try:
        out = va.assemble_video([clip, clip], voice, ass_for(tmp_path, small, 20), music, tmp_path / "f.mp4", small,
                                durations=[10, 10])
    finally:
        module.subprocess.run = real_run
    ffmpeg_cmd = next(c for c in seen if c[0] == "ffmpeg" and "-map" in c)
    graph = ffmpeg_cmd[ffmpeg_cmd.index("-filter_complex") + 1]
    assert ffmpeg_cmd.count(str(music)) == 5 and graph.count("acrossfade") == 4
    assert "-stream_loop" not in ffmpeg_cmd[ffmpeg_cmd.index(str(music)) - 2: ffmpeg_cmd.index(str(music))]
    from src.utils import ffprobe_duration
    assert ffprobe_duration(out) == pytest.approx(20, abs=0.3)


@pytest.mark.ffmpeg
def test_a_track_longer_than_the_video_is_simply_cut(small, tmp_path, make_tone, make_video):
    voice = make_tone(tmp_path / "voice.wav", 4, freq=300, volume_db=-4, channels=1)
    music = make_tone(tmp_path / "music.wav", 30, freq=110, volume_db=-8, channels=2)
    clip = make_video(tmp_path / "c.mp4", 270, 480, 2.0)
    out = va.assemble_video([clip], voice, ass_for(tmp_path, small, 4), music, tmp_path / "f.mp4", small)
    from src.utils import ffprobe_duration
    assert ffprobe_duration(out) == pytest.approx(4, abs=0.3)


@pytest.mark.ffmpeg
def test_a_thirty_clip_filter_graph_goes_through_a_file_and_still_renders(small, tmp_path, make_tone, make_video, make_image):
    voice = make_tone(tmp_path / "voice.wav", 15, freq=300, volume_db=-4, channels=1)
    music = make_tone(tmp_path / "music.wav", 20, freq=110, volume_db=-8, channels=2)
    portrait = make_video(tmp_path / "p.mp4", 270, 480, 2.0)
    wide = make_image(tmp_path / "w.png", 640, 360)
    clips = [portrait if i % 3 else wide for i in range(30)]
    out = tmp_path / "f.mp4"
    result = va.assemble_video(clips, voice, ass_for(tmp_path, small, 15), music, out, small, durations=[0.5] * 30)
    from src.utils import ffprobe_duration
    assert ffprobe_duration(result) == pytest.approx(15, abs=0.4)
    assert not out.with_suffix(".filtergraph.txt").exists()  # cleaned up after a successful render
