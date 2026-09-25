"""The channel's categories (config content.categories) and which one is next.

Rotation is "least recently used": the category whose latest video is oldest wins, a category never
used wins over any used one, ties go to the config order. Unlike a fixed day-of-week schedule this
stays even when days are skipped or a category's video failed, and it needs no state file — the
history (data/topic_bank_used.csv, column `category`) is the state.
"""

from __future__ import annotations

_FALLBACK = [{
    "id": "psychology", "label": "PSYCHOLOGY", "hashtags": ["#psychology", "#psychologyfacts", "#mindset"],
    "brief": "How the human mind works: cognitive biases, memory, perception, emotions, social behaviour.",
}]

# What footage suits each category — goes into the prompt so Claude's visual_keywords fit the subject.
FOOTAGE_HINTS = {
    "psychology": "people, faces and everyday situations (a commute, a dinner, a phone in a hand); never a named person",
    "science": "labs, microscopes, chemical reactions, nature, everyday objects in close-up",
    "space": "astronomy stock footage: nebulae, planets, the night sky, stars, telescopes, rockets, Earth from orbit",
    "world": "landscapes, aerial views, cities, wildlife, weather, people of different cultures at work and play",
    "technology": "circuit boards, servers, phones and screens, robots, satellites, everyday devices in use",
    "whatif": "dramatic skies and cityscapes, the real-world scene the scenario would change, nature in extreme conditions",
}


def get_categories(cfg: dict) -> list[dict]:
    cats = cfg.get("content", {}).get("categories") or _FALLBACK
    return [c for c in cats if c.get("id")]


def by_id(cfg: dict, category_id: str | None) -> dict | None:
    return next((c for c in get_categories(cfg) if c["id"] == category_id), None)


def label_for(cfg: dict, category_id: str | None) -> str:
    cat = by_id(cfg, category_id)
    return cat["label"] if cat else ""


def pick_next(cfg: dict, history: list[dict], forced: str | None = None) -> dict:
    """The category for the next video. `history` is every known row, oldest first (rows without a
    category, like the pre-written bank, are ignored). `forced` = a category id to use regardless."""
    cats = get_categories(cfg)
    if forced:
        cat = by_id(cfg, forced)
        if cat is None:
            raise ValueError(f"unknown category {forced!r}; configured: {', '.join(c['id'] for c in cats)}")
        return cat
    last_seen = {c["id"]: -1 for c in cats}
    for i, row in enumerate(history):
        if row.get("category") in last_seen:
            last_seen[row["category"]] = i
    order = {c["id"]: n for n, c in enumerate(cats)}
    return min(cats, key=lambda c: (last_seen[c["id"]], order[c["id"]]))
