"""Auto-pick the next unused topic (with a pre-written script) from
data/topic_bank.csv, so `main.py auto` needs zero arguments and never needs
ANTHROPIC_API_KEY to run.

Also owns the monthly refresh: `ensure_fresh_batch()` tops up the bank with a
new batch of topics once the current one is used up or a calendar month has
passed — via the Anthropic API if ANTHROPIC_API_KEY is set (best quality),
otherwise via a template generator over data/effects_pool.csv (~60 named
psychology effects), so topics never repeat across days and the bank never
needs a human/API to keep going for months.
"""

from __future__ import annotations

import calendar
import csv
import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from src.utils import get_logger

log = get_logger("topic_bank")

DEFAULT_BANK_PATH = Path("data/topic_bank.csv")
DEFAULT_EFFECTS_POOL_PATH = Path("data/effects_pool.csv")
DEFAULT_META_PATH = Path("data/topic_bank_meta.json")


def _days_in_month(dt: datetime) -> int:
    """28/29/30/31 depending on dt's actual month — used instead of a flat
    "30 days" constant so a batch generated in February isn't short by 2-3
    videos, and one generated in a 31-day month doesn't wait an extra day
    past when it should have refreshed."""
    return calendar.monthrange(dt.year, dt.month)[1]

_BANK_FIELDNAMES = ["id", "topic", "language", "script", "used_at"]
_EFFECTS_FIELDNAMES = ["id", "name", "mechanism", "example", "hook_subject", "topic", "used_batch"]

# Do/does-free so they're grammatically safe regardless of whether an
# effect's hook_subject clause has a singular or plural subject.
_HOOK_TEMPLATES = [
    "Ever notice how {hook_subject}?",
    "Here's something strange, {hook_subject}.",
    "Notice this next time, {hook_subject}.",
    "Here's a strange one, {hook_subject}.",
]

# Templates ending the previous sentence with "." need the mechanism clause
# capitalized (it starts a new sentence); templates continuing with "," need
# it lowercase. Kept as separate pools so _render_from_effect always pairs
# the right capitalization with the right punctuation.
_MECHANISM_TEMPLATES_COMMA = [
    "It's called the {name}, {mechanism}.",
    "That's the {name} at work, {mechanism}.",
    "This is known as the {name}, {mechanism}.",
]
_MECHANISM_TEMPLATES_PERIOD = [
    "Psychologists call this the {name}. {mechanism_cap}.",
]
_ACTION_TEMPLATES = [
    "Next time you notice this, pause and ask if it's really true, or just the {name} talking.",
    "Next time it happens, name the effect out loud, it usually breaks the spell.",
    "Next time you catch yourself doing this, ask what you'd think without that bias in play.",
    "Next time this shows up, just naming it to yourself is often enough to loosen its grip.",
]


# --- topic_bank.csv I/O -----------------------------------------------------

def _read_rows(bank_path: Path) -> list[dict]:
    with open(bank_path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(bank_path: Path, rows: list[dict]) -> None:
    with open(bank_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_BANK_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _BANK_FIELDNAMES})


def pick_next(bank_path: Path = DEFAULT_BANK_PATH, language: str | None = None) -> dict:
    """Return the next unused entry (id/topic/language/script), in bank order.

    Call ensure_fresh_batch() first (main.py's `auto` command does) so this
    normally always finds something new. As a last-resort fallback — only
    reached if batch generation itself failed — the whole bank (or matching
    language slice) is recycled with a warning, so this still never raises
    "nothing left to post".
    """
    if not bank_path.exists():
        raise FileNotFoundError(
            f"{bank_path} not found — this is the pre-written topic+script bank "
            "the `auto` command draws from."
        )
    rows = _read_rows(bank_path)
    if not rows:
        raise ValueError(f"{bank_path} is empty — add at least one topic row to use `auto`.")

    def matches(row: dict) -> bool:
        return not row.get("used_at") and (language is None or row["language"] == language)

    candidates = [r for r in rows if matches(r)]
    if not candidates:
        log.warning(
            "topic bank exhausted%s and no fresh batch could be generated — recycling from the top",
            f" for language={language}" if language else "",
        )
        for row in rows:
            if language is None or row["language"] == language:
                row["used_at"] = ""
        _write_rows(bank_path, rows)
        candidates = [r for r in rows if matches(r)]
    if not candidates:
        raise ValueError(f"No entries in {bank_path} match language={language!r}.")

    return candidates[0]


def mark_used(bank_path: Path, entry_id: str) -> None:
    rows = _read_rows(bank_path)
    for row in rows:
        if row["id"] == str(entry_id):
            row["used_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            break
    _write_rows(bank_path, rows)


# --- monthly batch refresh --------------------------------------------------

def _read_effects(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_effects(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_EFFECTS_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _EFFECTS_FIELDNAMES})


def _render_from_effect(effect: dict, rng: random.Random) -> dict:
    hook = rng.choice(_HOOK_TEMPLATES).format(hook_subject=effect["hook_subject"])
    mechanism = effect["mechanism"]
    mechanism_cap = mechanism[0].upper() + mechanism[1:]
    mech_template = rng.choice(_MECHANISM_TEMPLATES_COMMA + _MECHANISM_TEMPLATES_PERIOD)
    mech = mech_template.format(name=effect["name"], mechanism=mechanism, mechanism_cap=mechanism_cap)
    example = effect["example"].strip().rstrip(".") + "."
    example = example[0].upper() + example[1:]
    action = rng.choice(_ACTION_TEMPLATES).format(name=effect["name"])
    script = " ".join([hook, mech, example, action])
    return {"topic": effect["topic"], "language": "en", "script": script}


def _generate_batch_template(n: int, effects_pool_path: Path, batch_id: int) -> list[dict]:
    """Free, keyless, offline fallback: render n unused rows from
    effects_pool.csv into topic+script pairs via templates. Marks the picked
    effects used_batch=<batch_id> so future batches don't reuse them."""
    if not effects_pool_path.exists():
        log.warning("%s not found — cannot template-generate a new batch", effects_pool_path)
        return []
    effects = _read_effects(effects_pool_path)
    unused = [e for e in effects if not e.get("used_batch")]
    if not unused:
        log.warning(
            "%s has no unused effects left across all past batches — "
            "add more rows to keep the monthly auto-refresh going",
            effects_pool_path,
        )
        return []
    rng = random.Random(f"batch-{batch_id}")
    rng.shuffle(unused)
    picked = unused[:n]
    for e in picked:
        e["used_batch"] = str(batch_id)
    _write_effects(effects_pool_path, effects)  # `effects` still holds refs to `picked` rows
    return [_render_from_effect(e, rng) for e in picked]


def _generate_batch_via_api(n: int, model: str, exclude_topics: set[str]) -> list[dict] | None:
    """Ask Anthropic for n fresh {topic, script} pairs in one call — better
    variety/quality than the template generator. Returns None on any failure
    (missing key, network error, bad response) so the caller falls back to
    templates; this is a nice-to-have upgrade path, never a hard dependency.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        exclude_list = "\n".join(f"- {t}" for t in sorted(exclude_topics)) or "(none yet)"
        system = (
            "Bạn là copywriter cho kênh short-video tâm lý học tên Noggora. Sinh ra "
            f"{n} chủ đề MỚI (chưa từng dùng), mỗi chủ đề kèm 1 script khoảng 90 từ tiếng Anh, "
            "giọng gần gũi không hàn lâm. Cấu trúc mỗi script bắt buộc: câu 1 hook gây tò mò "
            "hoặc nghịch lý; đoạn giữa 1 sự thật/insight tâm lý học có căn cứ, nêu tên hiệu ứng "
            "nếu có; câu cuối 1 hành động/góc nhìn người xem áp dụng được ngay. Không markdown, "
            "không hashtag, không tiêu đề trong script.\n\n"
            f"KHÔNG được trùng hoặc quá giống các chủ đề đã dùng sau:\n{exclude_list}\n\n"
            "Trả về DUY NHẤT 1 JSON array hợp lệ, không kèm chữ nào khác, dạng: "
            '[{"topic": "...", "script": "..."}, ...]'
        )
        response = client.messages.create(
            model=model, max_tokens=4000, system=system,
            messages=[{"role": "user", "content": f"Sinh {n} chủ đề mới."}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError("no JSON array found in API response")
        items = json.loads(match.group(0))
        entries = [
            {"topic": it["topic"].strip(), "language": "en", "script": it["script"].strip()}
            for it in items if it.get("topic") and it.get("script")
        ]
        return entries or None
    except Exception as e:
        log.warning("API batch generation failed (%s) — falling back to template generator", e)
        return None


def _load_or_init_meta(meta_path: Path) -> dict:
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    meta = {"current_batch": 1, "batch_started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def ensure_fresh_batch(
    bank_path: Path = DEFAULT_BANK_PATH,
    effects_pool_path: Path = DEFAULT_EFFECTS_POOL_PATH,
    meta_path: Path = DEFAULT_META_PATH,
    batch_size: int | None = None,
    interval_days: int | None = None,
    model: str = "claude-sonnet-5",
) -> None:
    """Call before pick_next(). Appends a fresh batch of topics to bank_path
    once the current batch is used up OR interval_days have passed since it
    started, whichever comes first — so topics are guaranteed not to repeat
    within a month even if the bank isn't literally exhausted yet.

    `batch_size`/`interval_days` default to None, which means "derive from
    the calendar" rather than a flat 30: interval_days becomes the actual
    number of days in the month the current batch started in (28-31), and a
    newly generated batch is sized to the number of days in the month it
    starts in — so at one video/day, a batch generated in February is 28
    topics (not 30, which would run 2 days short) and one generated in a
    31-day month is 31 (not 30, which would refresh a day early).
    """
    meta = _load_or_init_meta(meta_path)
    started = datetime.fromisoformat(meta["batch_started_at"])
    now = datetime.now(timezone.utc)
    days_elapsed = (now - started).days
    effective_interval_days = interval_days if interval_days is not None else _days_in_month(started)

    rows = _read_rows(bank_path) if bank_path.exists() else []
    unused_count = sum(1 for r in rows if not r.get("used_at"))

    if unused_count > 0 and days_elapsed < effective_interval_days:
        return  # current batch still has unused topics and isn't stale yet

    effective_batch_size = batch_size if batch_size is not None else _days_in_month(now)
    next_batch = meta["current_batch"] + 1
    existing_topics = {r["topic"] for r in rows}

    new_entries = _generate_batch_via_api(effective_batch_size, model, existing_topics)
    source = "Anthropic API"
    if new_entries is None:
        new_entries = _generate_batch_template(effective_batch_size, effects_pool_path, next_batch)
        source = "template generator (data/effects_pool.csv)"

    if not new_entries:
        log.warning("could not generate a new topic batch via %s — will fall back to recycling if needed", source)
        return

    next_id = max((int(r["id"]) for r in rows), default=0) + 1
    for entry in new_entries:
        rows.append({
            "id": str(next_id), "topic": entry["topic"], "language": entry["language"],
            "script": entry["script"], "used_at": "",
        })
        next_id += 1
    _write_rows(bank_path, rows)

    meta["current_batch"] = next_batch
    meta["batch_started_at"] = now.isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("appended batch #%d: %d new topics via %s", next_batch, len(new_entries), source)


if __name__ == "__main__":
    ensure_fresh_batch()
    entry = pick_next()
    print(f"next up: [{entry['id']}] ({entry['language']}) {entry['topic']}")
