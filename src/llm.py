"""One place to ask an LLM for text, so script writing works with either:

  - the Claude Code CLI (`claude -p`), which runs on whatever account is logged
    in to Claude Code — no API key, usage counts against that subscription;
  - the Anthropic API (ANTHROPIC_API_KEY), billed per token on console.anthropic.com.

Which one is used is `script.provider` in settings.yaml:
  auto           API if ANTHROPIC_API_KEY is set, else the CLI if installed, else none
  claude_cli     always the CLI
  anthropic_api  always the API
  manual         never (jobs stop and wait for a hand-written script.txt)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.utils import get_logger

log = get_logger("llm")


class LLMUnavailable(Exception):
    """No usable backend (provider=manual, no key and no CLI, or the CLI failed)."""


def _cli_path() -> str | None:
    return shutil.which("claude")


def backend(cfg: dict) -> str | None:
    """"cli", "api" or None, per script.provider and what is actually available."""
    provider = cfg.get("script", {}).get("provider", "auto")
    has_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    has_cli = _cli_path() is not None
    if provider == "manual":
        return None
    if provider == "anthropic_api":
        return "api" if has_key else None
    if provider == "claude_cli":
        return "cli" if has_cli else None
    return "api" if has_key else ("cli" if has_cli else None)


def _complete_cli(system: str, user: str, model: str, timeout: int) -> str:
    claude = _cli_path()
    if claude is None:
        raise LLMUnavailable("`claude` CLI not found on PATH")
    # An ANTHROPIC_API_KEY in the environment (e.g. loaded from .env) would make
    # the CLI bill the API instead of the logged-in subscription — strip it.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    with tempfile.TemporaryDirectory(prefix="noggora-claude-") as tmp:
        system_file = Path(tmp) / "system.txt"
        system_file.write_text(system, encoding="utf-8")
        # Prompt goes through stdin and the system prompt through a file: on
        # Windows `claude` is a .cmd wrapper, where quotes/newlines/& in argv
        # get mangled. No tools, no session saved, cwd = empty temp dir so no
        # project files (CLAUDE.md etc.) leak into the request.
        cmd = [
            claude, "-p", "--model", model,
            "--system-prompt-file", str(system_file),
            "--tools", "", "--no-session-persistence", "--output-format", "text",
        ]
        try:
            result = subprocess.run(
                cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                timeout=timeout, cwd=tmp, env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMUnavailable(f"`claude -p` timed out after {timeout}s") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise LLMUnavailable(f"`claude -p` failed (exit {result.returncode}): {detail}")
    text = result.stdout.strip()
    if not text:
        raise LLMUnavailable("`claude -p` returned no text")
    return text


def _complete_api(system: str, user: str, model: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", "").strip())
    response = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def complete(system: str, user: str, cfg: dict, max_tokens: int = 1200) -> str:
    """Ask the configured backend; raises LLMUnavailable if there isn't one or
    the CLI fails. (API errors propagate as the SDK's own exceptions.)"""
    which = backend(cfg)
    script_cfg = cfg.get("script", {})
    if which == "cli":
        model = script_cfg.get("cli_model", "sonnet")
        return _complete_cli(system, user, model, int(script_cfg.get("cli_timeout_sec", 180)))
    if which == "api":
        return _complete_api(system, user, script_cfg.get("anthropic_model", "claude-sonnet-5"), max_tokens)
    raise LLMUnavailable(
        "no LLM backend available (script.provider=manual, or no ANTHROPIC_API_KEY and no `claude` CLI)"
    )
