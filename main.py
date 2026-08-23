#!/usr/bin/env python
"""CLI entrypoint for the Noggora pipeline.

    python main.py auto                                            # zero-argument: next topic from data/topic_bank.csv
    python main.py single --topic "..." --lang vi
    python main.py single --resume "output/<job_slug>"          # after editing script.txt
    python main.py batch --file data/topics.csv --limit 10
    python main.py batch --resume                                 # also retry awaiting_manual rows
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src import topic_bank
from src.pipeline import JobResult, run_job
from src.utils import check_ffmpeg, get_logger, load_config, new_job_slug

log = get_logger("main")

_TOPICS_FIELDNAMES = ["topic", "language", "status", "job_dir"]


def _print_result(i: int, total: int, topic: str, result: JobResult) -> None:
    short_topic = topic if len(topic) <= 40 else topic[:37] + "..."
    if result.status == "done":
        print(f'[{i}/{total}] "{short_topic}" -> {result.final_video} ✅')
        print(f"    Title (copy for YouTube): {result.title}")
    elif result.status == "awaiting_manual_script":
        print(f'[{i}/{total}] "{short_topic}" -> awaiting manual script: {result.out_dir / "script.txt"} ✋')
    else:
        print(f'[{i}/{total}] "{short_topic}" -> FAILED at step "{result.failed_step}": {result.error} ❌')


def cmd_auto(args: argparse.Namespace, cfg: dict) -> int:
    """Zero-argument daily video: pick the next unused topic from the bank,
    run it end to end, mark it used. Needs no ANTHROPIC_API_KEY — if one
    isn't set, the bank's pre-written script is used as-is; if one *is* set,
    run_job still calls the live API for a fresh script instead."""
    bank_path = Path(args.bank)
    topic_bank.ensure_fresh_batch(bank_path, model=cfg["script"]["anthropic_model"])
    try:
        entry = topic_bank.pick_next(bank_path, language=args.lang)
    except (FileNotFoundError, ValueError) as e:
        log.error(str(e))
        return 1

    out_dir = Path("output") / new_job_slug(entry["topic"])
    out_dir.mkdir(parents=True, exist_ok=True)

    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        (out_dir / "script.txt").write_text(entry["script"], encoding="utf-8")

    result = run_job(entry["topic"], entry["language"], cfg, out_dir=out_dir)
    if result.status == "done":
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

    result = run_job(topic, language, cfg, out_dir=out_dir)
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
