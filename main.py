#!/usr/bin/env python
"""CLI entrypoint for the Noggora pipeline.

    python main.py auto                                            # zero-argument: Claude writes today's topic + script
    python main.py auto --bank-only                                # skip AI, use the pre-written data/topic_bank.csv
    python main.py single --topic "..." --lang vi
    python main.py single --resume "output/<job_slug>"          # after editing script.txt
    python main.py batch --file data/topics.csv --limit 10
    python main.py batch --resume                                 # also retry awaiting_manual rows
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src import daily_topic, topic_bank
from src.pipeline import JobResult, run_job
from src.utils import check_ffmpeg, get_logger, load_config, new_job_slug

log = get_logger("main")

_TOPICS_FIELDNAMES = ["topic", "language", "status", "job_dir"]


def _print_result(i: int, total: int, topic: str, result: JobResult) -> None:
    short_topic = topic if len(topic) <= 40 else topic[:37] + "..."
    if result.status == "done":
        print(f'[{i}/{total}] "{short_topic}" -> {result.final_video} ✅')
        print(f"\n--- COPY FOR THE POST (also saved in {result.out_dir / 'post.txt'}) ---")
        print(f"Title:\n{result.title}")
        if result.description:
            print(f"\nDescription:\n{result.description}")
        else:
            print("\n(No description generated — Claude wasn't available. Title only.)")
        if result.cover:
            print(f"\nCover image: {result.cover}")
        print("---")
    elif result.status == "awaiting_manual_script":
        print(f'[{i}/{total}] "{short_topic}" -> awaiting manual script: {result.out_dir / "script.txt"} ✋')
    else:
        print(f'[{i}/{total}] "{short_topic}" -> FAILED at step "{result.failed_step}": {result.error} ❌')


def cmd_auto(args: argparse.Namespace, cfg: dict) -> int:
    """Zero-argument video for today. Preferred: ask Claude (the `claude` CLI
    on your Claude Code login, or the Anthropic API if a key is set) to write
    a brand-new topic + script + post caption + footage keywords, so nothing is
    pre-generated in bulk. If that isn't possible the run stops with an error,
    unless script.fallback_to_bank is on (or --bank-only is passed), in which
    case the next unused pre-written topic from the bank is used instead."""
    bank_path = Path(args.bank)

    entry = None
    generated = False
    if not args.bank_only and args.lang in (None, "en"):
        entry = daily_topic.generate_daily_entry(cfg, bank_path)
        generated = entry is not None
        if entry is None:
            if not cfg["script"].get("fallback_to_bank", False):
                log.error(
                    "Claude could not produce a new topic + script (see warnings above). Check that `claude` "
                    "is logged in (run `claude` once) and retry; or run `main.py auto --bank-only` to use a "
                    "pre-written topic."
                )
                return 1
            log.warning("could not generate a fresh script — using the pre-written topic bank instead")

    if entry is None:
        topic_bank.ensure_fresh_batch(bank_path, model=cfg["script"]["anthropic_model"])
        try:
            entry = topic_bank.pick_next(bank_path, language=args.lang)
        except (FileNotFoundError, ValueError) as e:
            log.error(str(e))
            return 1

    out_dir = Path("output") / new_job_slug(entry["topic"])
    out_dir.mkdir(parents=True, exist_ok=True)
    # Either way the script is already in hand, so run_job must not generate another.
    (out_dir / "script.txt").write_text(entry["script"], encoding="utf-8")
    if entry.get("fact_check"):
        notes = "\n".join(f"- {n}" for n in entry.get("fact_check_notes", [])) or "(nothing flagged)"
        (out_dir / "factcheck.txt").write_text(f"verdict: {entry['fact_check']}\n{notes}\n", encoding="utf-8")
    # Everything the generation produced, so `single --resume` re-renders the same video
    # (same footage keywords and caption) instead of regenerating those parts.
    (out_dir / "entry.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")

    if generated:
        # Archive right away, not after the render: if the video step fails, this
        # topic must still count as made, or tomorrow could generate it again.
        # (Re-render the same script with: main.py single --resume <that output dir>.)
        topic_bank.record_generated_used(entry, bank_path)
        log.info("saved to %s — script also in %s", topic_bank.DEFAULT_USED_PATH, out_dir / "script.txt")

    visual_keywords = [k.strip() for k in (entry.get("visual_keywords") or "").split(";") if k.strip()]
    result = run_job(
        entry["topic"], entry["language"], cfg, out_dir=out_dir,
        visual_keywords=visual_keywords or None, description=entry.get("description") or None,
    )
    if result.status == "done" and not generated:
        topic_bank.mark_used(bank_path, entry["id"])
    _print_result(1, 1, entry["topic"], result)
    return 0 if result.status == "done" else 1


def cmd_single(args: argparse.Namespace, cfg: dict) -> int:
    out_dir = Path(args.resume) if args.resume else None
    topic, language = args.topic, args.lang

    if out_dir:
        if not out_dir.exists():
            log.error("--resume path does not exist: %s", out_dir)
            return 1
        job_log_path = out_dir / "job_log.json"
        if not topic and job_log_path.exists():
            data = json.loads(job_log_path.read_text(encoding="utf-8"))
            topic = data.get("topic")
            language = data.get("language", language)

    if not topic:
        log.error("--topic is required (or point --resume at a job dir with a job_log.json)")
        return 1

    # A job made by `auto` left its generated extras beside the script — reuse them.
    visual_keywords, description = None, None
    entry_path = out_dir / "entry.json" if out_dir else None
    if entry_path and entry_path.exists():
        saved = json.loads(entry_path.read_text(encoding="utf-8"))
        if saved.get("topic") == topic:
            visual_keywords = [k.strip() for k in (saved.get("visual_keywords") or "").split(";") if k.strip()] or None
            description = saved.get("description") or None

    result = run_job(topic, language, cfg, out_dir=out_dir, visual_keywords=visual_keywords, description=description)
    _print_result(1, 1, topic, result)
    return 0 if result.status == "done" else 1


def _read_topics(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row.setdefault("job_dir", "")
    return rows


def _write_topics(path: str, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_TOPICS_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _TOPICS_FIELDNAMES})


def cmd_batch(args: argparse.Namespace, cfg: dict) -> int:
    rows = _read_topics(args.file)
    wanted = {"pending"} | ({"awaiting_manual"} if args.resume else set())
    todo = [r for r in rows if r.get("status", "pending") in wanted]
    if args.limit:
        todo = todo[: args.limit]

    total = len(todo)
    if total == 0:
        print(f"Nothing to do (no rows with status in {sorted(wanted)}).")
        return 0

    exit_code = 0
    for i, row in enumerate(todo, 1):
        out_dir = Path(row["job_dir"]) if row.get("job_dir") else None
        result = run_job(row["topic"], row["language"], cfg, out_dir=out_dir)
        _print_result(i, total, row["topic"], result)

        if result.status == "done":
            row["status"] = "done"
        elif result.status == "awaiting_manual_script":
            row["status"] = "awaiting_manual"
        else:
            row["status"] = "failed"
            exit_code = 1
        row["job_dir"] = str(result.out_dir)

        _write_topics(args.file, rows)  # persist progress after every job, not just at the end

    return exit_code


def main() -> int:
    # Windows consoles often default to a legacy codepage (cp1252) that can't
    # encode the ✅/✋/❌ status markers or non-ASCII topics — force UTF-8 out.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    load_dotenv()
    parser = argparse.ArgumentParser(prog="main.py", description="Noggora faceless-video pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auto = sub.add_parser("auto", help="Zero-argument video: auto-picks the next unused topic from data/topic_bank.csv")
    p_auto.add_argument("--bank", default="data/topic_bank.csv")
    p_auto.add_argument("--lang", choices=["en", "vi"], default=None, help="Restrict to one language (default: whichever comes up next in the bank)")
    p_auto.add_argument("--bank-only", action="store_true", help="Skip AI generation and use the pre-written topic bank")

    p_single = sub.add_parser("single", help="Generate one video from one topic")
    p_single.add_argument("--topic", help="Topic string (required unless --resume points at a job with a job_log.json)")
    p_single.add_argument("--lang", default="en", choices=["en", "vi"])
    p_single.add_argument("--resume", metavar="OUT_DIR", help="Resume a job stuck at awaiting_manual_script")

    p_batch = sub.add_parser("batch", help="Generate videos for pending topics in a CSV")
    p_batch.add_argument("--file", default="data/topics.csv")
    p_batch.add_argument("--limit", type=int, default=None, help="Max number of videos to produce this run")
    p_batch.add_argument("--resume", action="store_true", help="Also retry rows stuck at status=awaiting_manual")

    args = parser.parse_args()
    check_ffmpeg()
    cfg = load_config()

    if args.command == "auto":
        return cmd_auto(args, cfg)
    if args.command == "single":
        return cmd_single(args, cfg)
    return cmd_batch(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
