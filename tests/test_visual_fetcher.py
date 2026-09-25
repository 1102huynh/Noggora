from pathlib import Path

import pytest

from src import visual_fetcher as vf


# --- keywords -> scenes ----------------------------------------------------------

KW8 = [f"k{i}" for i in range(8)]
KW5 = KW8[:5]


@pytest.mark.parametrize("n_scenes", [5, 6, 7, 8])
@pytest.mark.parametrize("kws", [KW8, KW5])
def test_every_keyword_is_used_exactly_once(n_scenes, kws):
    used = [k for i in range(n_scenes) for k in vf.keywords_for_scene(i, n_scenes, kws)]
    assert sorted(used) == sorted(kws)


@pytest.mark.parametrize("n_scenes", [5, 6, 7, 8])
def test_first_scene_gets_the_hook_and_last_scene_the_closing_action(n_scenes):
    assert vf.keywords_for_scene(0, n_scenes, KW8)[0] == "k0"
    assert vf.keywords_for_scene(n_scenes - 1, n_scenes, KW8)[-1] == "k7"


def test_with_fewer_keywords_than_scenes_the_gaps_are_left_empty_not_duplicated():
    per_scene = [vf.keywords_for_scene(i, 6, KW5) for i in range(6)]
    assert [len(p) for p in per_scene].count(0) == 1
    assert per_scene[0] == ["k0"] and per_scene[-1] == ["k4"]


def test_no_keywords_no_queries_from_them():
    assert vf.keywords_for_scene(0, 5, None) == [] and vf.keywords_for_scene(0, 5, []) == []


def test_queries_put_the_written_keyword_first_then_concepts_then_niche():
    text = "You keep watching the movie for another hour."
    queries = vf.scene_queries(0, text, ["my keyword"], n_scenes=5)
    assert queries[0] == "my keyword"
    assert "cinema audience watching" in queries[:3]
    assert queries[-1] in vf.NICHE_KEYWORDS
    assert len(queries) == len(set(queries))


def test_a_scene_with_no_keyword_of_its_own_starts_from_what_its_text_mentions():
    queries = vf.scene_queries(2, "You keep watching the movie for another hour.", ["my keyword"], n_scenes=5)
    assert "my keyword" not in queries
    assert queries[0] == "cinema audience watching"


def test_concept_words_map_to_footage_queries():
    assert vf._concept_queries("You spilled your coffee on the table") == ["coffee cup table", "person spilling coffee"]
    assert vf._concept_queries("Nothing concrete is said here") == []


# --- relevance -------------------------------------------------------------------

def test_pexels_relevance_reads_the_url_slug():
    video = {"url": "https://www.pexels.com/video/woman-typing-on-laptop-123456/"}
    assert vf._relevance("typing laptop", "pexels", video) == 1.0
    assert vf._relevance("dog park", "pexels", video) == 0.0


def test_pixabay_relevance_reads_the_tags():
    video = {"tags": "cafe, restaurant, talking, menu, coffee, dinner"}
    assert vf._relevance("coffee menu choosing", "pixabay", video) == pytest.approx(2 / 3)


def test_generic_words_do_not_count_as_evidence():
    video = {"tags": "person, people, man, woman"}
    assert vf._relevance("person people", "pixabay", video) == 0.0


def test_a_cigarette_clip_does_not_match_hand_holding_wristwatch():
    # a real miss: these tags scored 67% on "hand" + "holding" alone and a smoking clip ended up in a video
    tags = {"tags": "cigarette, tobacco, smoke, smoking, hand, addiction, burning, holding"}
    assert vf._relevance("hand holding wristwatch", "pixabay", tags) == 0.0


@pytest.mark.parametrize("source,video", [
    ("pixabay", {"tags": "cigarette, tobacco, smoke, smoking, hand, addiction"}),
    ("pixabay", {"tags": "lizard, close up, iguana, reptile, wildlife, eye"}),
    ("pexels", {"url": "https://www.pexels.com/video/man-with-a-gun-on-a-table-123/"}),
    ("pixabay", {"tags": "Smoking, ashtray"}),
])
def test_off_limits_subjects_are_recognised(source, video):
    assert vf._is_blocked(source, video)


@pytest.mark.parametrize("source,video", [
    ("pixabay", {"tags": "clock, time, wall clock, office"}),
    ("pexels", {"url": "https://www.pexels.com/video/woman-checking-her-wristwatch-456/"}),
    ("pixabay", {"tags": "eye, close up, blinking, human"}),
])
def test_ordinary_clips_are_not_blocked(source, video):
    assert not vf._is_blocked(source, video)


def test_a_blocked_clip_is_never_downloaded_however_well_it_matches(tmp_path, monkeypatch):
    downloaded = []
    monkeypatch.setattr(vf, "_download", lambda url, dest: (downloaded.append(dest.name), Path(dest).write_bytes(b"x"))[0])
    smoking = {"id": 1, "tags": "eye, close up, moving, cigarette", "duration": 10,
               "videos": {"l": {"url": "u", "width": 1080, "height": 1920}}}
    fine = {"id": 2, "tags": "eye, blinking", "duration": 10, "videos": {"l": {"url": "u", "width": 1080, "height": 1920}}}
    src = [("pixabay", lambda q, k: [smoking, fine], lambda v: "link", "key")]
    got = vf._fetch_scene_clip(src, ["eye close up moving"], 6, tmp_path, set())
    assert got.name == "pixabay_2.mp4" and downloaded == ["pixabay_2.mp4"]


def test_plurals_match():
    assert vf._relevance("old photographs", "pixabay", {"tags": "old photo, photographs"}) == 1.0


# --- file selection ----------------------------------------------------------------

def test_pixabay_never_picks_4k():
    video = {"videos": {
        "small": {"url": "S", "width": 640, "height": 360},
        "large": {"url": "L", "width": 1920, "height": 1080},
        "huge": {"url": "H", "width": 3840, "height": 2160},
    }}
    assert vf._pick_pixabay_video_file(video) == "L"


def test_pixabay_takes_the_smallest_if_everything_is_huge():
    video = {"videos": {"a": {"url": "A", "width": 3840, "height": 2160}, "b": {"url": "B", "width": 5120, "height": 2880}}}
    assert vf._pick_pixabay_video_file(video) == "A"


def test_pexels_prefers_a_portrait_file_near_1080_wide():
    video = {"video_files": [
        {"link": "land", "width": 1920, "height": 1080},
        {"link": "small", "width": 540, "height": 960},
        {"link": "good", "width": 1080, "height": 1920},
    ]}
    assert vf._pick_video_file(video) == "good"


# --- _fetch_scene_clip with fake sources ---------------------------------------------

def pexels_video(i, slug, duration=10, top=True):
    return {"id": i, "url": f"https://www.pexels.com/video/{slug}-{i}/", "duration": duration,
            "width": 1080, "height": 1920}


def pixabay_video(i, tags, duration=10):
    return {"id": i, "tags": tags, "duration": duration, "videos": {"l": {"url": "u", "width": 1080, "height": 1920}}}


@pytest.fixture
def fake_download(monkeypatch):
    monkeypatch.setattr(vf, "_download", lambda url, dest: (Path(dest).write_bytes(b"x"), Path(dest))[1])


def sources(pexels_results=(), pixabay_results=(), order=("pexels", "pixabay")):
    table = {
        "pexels": ("pexels", lambda q, k: list(pexels_results), lambda v: "link", "key"),
        "pixabay": ("pixabay", lambda q, k: list(pixabay_results), lambda v: "link", "key"),
    }
    return [table[name] for name in order]


def test_the_scenes_preferred_source_wins_when_both_have_a_good_clip(tmp_path, fake_download):
    src = sources([pexels_video(1, "coffee-menu-choosing")], [pixabay_video(2, "coffee, menu, choosing")])
    got = vf._fetch_scene_clip(src, ["coffee menu choosing"], 6, tmp_path, set())
    assert got.name == "pexels_1.mp4"
    got = vf._fetch_scene_clip(list(reversed(src)), ["coffee menu choosing"], 6, tmp_path, set())
    assert got.name == "pixabay_2.mp4"


def test_the_other_source_covers_when_the_preferred_one_has_nothing_good(tmp_path, fake_download):
    src = sources([pexels_video(1, "unrelated-thing")], [pixabay_video(2, "coffee, menu, choosing")])
    got = vf._fetch_scene_clip(src, ["coffee menu choosing"], 6, tmp_path, set())
    assert got.name == "pixabay_2.mp4"


def test_a_later_query_is_tried_before_settling_for_a_poor_match(tmp_path, fake_download):
    calls = []

    def search(q, k):
        calls.append(q)
        return [pixabay_video(9, "clock, ticking, time")] if q == "clock ticking" else [pixabay_video(3, "banana")]

    src = [("pixabay", search, lambda v: "link", "key")]
    got = vf._fetch_scene_clip(src, ["wrong words", "clock ticking"], 6, tmp_path, set())
    assert got.name == "pixabay_9.mp4" and calls == ["wrong words", "clock ticking"]


def test_if_nothing_matches_the_best_scoring_clip_seen_is_used(tmp_path, fake_download):
    src = sources(pixabay_results=[pixabay_video(1, "banana"), pixabay_video(2, "coffee, mug")], order=("pixabay",))
    got = vf._fetch_scene_clip(src, ["coffee menu choosing"], 6, tmp_path, set())
    assert got.name == "pixabay_2.mp4"  # 1/3 words beats 0/3


def test_a_clip_already_used_in_this_video_is_skipped(tmp_path, fake_download):
    src = sources(pixabay_results=[pixabay_video(1, "coffee, menu"), pixabay_video(2, "coffee, menu")], order=("pixabay",))
    got = vf._fetch_scene_clip(src, ["coffee menu"], 6, tmp_path, {"pixabay_1"})
    assert got.name == "pixabay_2.mp4"


def test_very_short_clips_are_ignored(tmp_path, fake_download):
    src = sources(pixabay_results=[pixabay_video(1, "coffee, menu", duration=2)], order=("pixabay",))
    assert vf._fetch_scene_clip(src, ["coffee menu"], 6, tmp_path, set()) is None


def test_a_failing_search_is_survived(tmp_path, fake_download):
    def boom(q, k):
        raise RuntimeError("rate limited")

    good = ("pixabay", lambda q, k: [pixabay_video(4, "coffee, menu")], lambda v: "link", "key")
    got = vf._fetch_scene_clip([("pexels", boom, lambda v: "l", "key"), good], ["coffee menu"], 6, tmp_path, set())
    assert got.name == "pixabay_4.mp4"


def test_longer_clips_are_preferred_when_relevance_ties(tmp_path, fake_download):
    src = sources(pixabay_results=[pixabay_video(1, "coffee, menu", duration=4), pixabay_video(2, "coffee, menu", duration=12)],
                  order=("pixabay",))
    got = vf._fetch_scene_clip(src, ["coffee menu"], 9, tmp_path, set())
    assert got.name == "pixabay_2.mp4"
