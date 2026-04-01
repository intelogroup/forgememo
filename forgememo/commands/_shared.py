"""Shared constants, console, and helper functions for all CLI command modules."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console


# ---------------------------------------------------------------------------
# Console + symbols
# ---------------------------------------------------------------------------


def _make_console() -> Console:
    """Return a Console that won't crash on narrow-encoding terminals (Windows cp1252/ascii)."""
    import io

    enc = (getattr(sys.stdout, "encoding", None) or "utf-8").lower()
    if enc in ("ascii", "cp1252", "latin-1", "latin1"):
        try:
            safe_file = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding=enc,
                errors="replace",
                line_buffering=True,
            )
            return Console(file=safe_file, highlight=False)
        except AttributeError:
            pass
    return Console()


console = _make_console()

_enc = (getattr(sys.stdout, "encoding", None) or "ascii").lower()
_UNICODE_OK = _enc not in ("ascii", "cp1252", "latin-1", "latin1")
CHECK = "\u2713" if _UNICODE_OK else "ok"
CROSS = "\u2717" if _UNICODE_OK else "x"


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgememo.daemon.plist"
PLIST_LABEL = "com.forgememo.daemon"
WORKER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgememo.worker.plist"
WORKER_PLIST_LABEL = "com.forgememo.worker"
MINER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgememo.miner.plist"
MINER_PLIST_LABEL = "com.forgememo.miner"
LOG_PATH = Path.home() / "Library" / "Logs" / "forgememo.log"

SKILL_PATHS: dict[str, Path] = {
    "claude": Path.home() / ".claude" / "skills" / "forgememo.md",
    "gemini": Path.home() / ".gemini" / "forgememo-skill.md",
    "codex": Path.home() / ".codex" / "forgememo-skill.json",
}

SKILL_TEMPLATES_DIR = Path(__file__).parent.parent / "skills"

_CONTEXT_FILES = [
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".github/copilot-instructions.md",
    ".opencode/instructions.md",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _replace_block(text: str, start: str, end: str, block: str) -> str:
    """Idempotently replace a block delimited by start/end markers."""
    if start in text and end in text:
        pre, rest = text.split(start, 1)
        _, post = rest.split(end, 1)
        return f"{pre}{block}{post}"
    sep = "\n" if text.endswith("\n") else "\n\n"
    return f"{text}{sep}{block}\n"


def _format_context_markdown(
    project: str,
    updated: str,
    principles: list[dict],
    last_session: dict | None,
) -> str:
    lines = [f"Project: {project}", f"Updated: {updated}", ""]
    lines.append("Principles:")
    if not principles:
        lines.append("_No principles found._")
    else:
        for p in principles:
            date = (p.get("ts") or "")[:10]
            score = p.get("impact_score")
            score_str = f"score {score}" if score is not None else "score n/a"
            narrative = p.get("narrative") or ""
            tail = f" \u2014 {narrative}" if narrative else ""
            lines.append(
                f"- [{p.get('type','')}] {p.get('title','')} ({score_str}, {date}){tail}"
            )

    lines.append("")
    lines.append("Last session:")
    if not last_session:
        lines.append("_No session summary found._")
    else:
        lines.append(f"Request: {last_session.get('request','')}")
        if last_session.get("investigation"):
            lines.append(f"Investigation: {last_session.get('investigation')}")
        if last_session.get("learnings"):
            lines.append(f"Learnings: {last_session.get('learnings')}")
        if last_session.get("next_steps"):
            lines.append(f"Next steps: {last_session.get('next_steps')}")
    return "\n".join(lines)


def _register_mcp(settings_path: Path) -> bool:
    """Idempotently add forgememo to ~/.claude/settings.json mcpServers."""
    data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    servers = data.setdefault("mcpServers", {})
    if "forgememo" not in servers:
        servers["forgememo"] = {"command": "forgememo", "args": ["mcp"]}
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(data, indent=2))
        except PermissionError as exc:
            console.print(
                f"[yellow]warning:[/] could not register MCP in {settings_path}: {exc}"
            )
            return False
        return True
    return False


def _register_hooks(settings_path: Path) -> bool:
    """Idempotently add UserPromptSubmit + Stop + PostToolUse hooks."""
    data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = data.setdefault("hooks", {})
    changed = False
    for event in ("UserPromptSubmit", "Stop", "PostToolUse"):
        existing = hooks.get(event, [])
        already = any(
            h.get("hooks", [{}])[0].get("command", "").startswith("forgememo hook")
            for h in existing
        )
        if not already:
            hooks.setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": f"forgememo hook {event}"}]}
            )
            changed = True
    if changed:
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(data, indent=2))
        except PermissionError as exc:
            console.print(
                f"[yellow]warning:[/] could not register hooks in {settings_path}: {exc}"
            )
            return False
        return True
    return False


def _write_project_context(project_dir: str, summary: dict) -> None:
    """Update <forgememo-context> block in all known agent context files."""
    from datetime import timezone

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    project_label = Path(project_dir).name
    inner = _format_context_markdown(project_label, updated, [], summary)
    block = f"<forgememo-context>\n{inner}\n</forgememo-context>"
    for rel in _CONTEXT_FILES:
        try:
            ctx_file = Path(project_dir) / rel
            if not ctx_file.exists():
                continue
            new_text = _replace_block(
                ctx_file.read_text(),
                "<forgememo-context>",
                "</forgememo-context>",
                block,
            )
            ctx_file.write_text(new_text)
        except Exception:
            pass


def _generate_skill(agent: str, dry_run: bool = False) -> None:
    """Read template from forgememo/skills/{agent}.{ext} and write to SKILL_PATHS[agent]."""
    ext = "json" if agent == "codex" else "md"
    template = SKILL_TEMPLATES_DIR / f"{agent}.{ext}"
    dest = SKILL_PATHS[agent]

    if dry_run:
        console.print(f"  [dim]dry-run[/] would write: {dest}")
        return

    if not template.exists():
        console.print(f"  [yellow]warning:[/] skill template not found: {template}")
        return

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(template.read_text())
        console.print(f"  [green]wrote[/] {dest}")
    except PermissionError as exc:
        console.print(f"  [yellow]warning:[/] could not write skill file {dest}: {exc}")


def _auto_detect_and_generate_skills(yes: bool) -> None:
    import typer

    detected: list[str] = []
    if (Path.home() / ".claude" / "settings.json").exists():
        detected.append("claude")
    if (Path.home() / ".gemini").exists():
        detected.append("gemini")
    if (Path.home() / ".codex").exists():
        detected.append("codex")

    if not detected:
        return

    agents_str = ", ".join(detected)
    if not yes and sys.stdin.isatty():
        proceed = typer.confirm(
            f"Generate Forgememo skill files for detected agents ({agents_str})?",
            default=True,
        )
        if not proceed:
            return

    for agent in detected:
        _generate_skill(agent)


def _detect_project_from_git() -> Optional[str]:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip()).name
    return None
