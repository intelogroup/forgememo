#!/usr/bin/env python3
"""Forgemem CLI — flat ruff-style commands."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated
from rich.console import Console
from rich.panel import Panel

# ---------------------------------------------------------------------------
# App + console
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="forgemem",
    help="Long-term memory store for AI agents.",
    add_completion=False,
)
console = Console()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgemem.server.plist"
PLIST_LABEL = "com.forgemem.server"
LOG_PATH = Path.home() / "Library" / "Logs" / "forgemem.log"

SKILL_PATHS: dict[str, Path] = {
    "claude": Path.home() / ".claude" / "skills" / "forgemem.md",
    "gemini": Path.home() / ".gemini" / "forgemem-skill.md",
    "codex":  Path.home() / ".codex"  / "forgemem-skill.json",
}

SKILL_TEMPLATES_DIR = Path(__file__).parent / "skills"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_mcp(settings_path: Path) -> bool:
    """Idempotently add forgemem to ~/.claude/settings.json mcpServers."""
    data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    servers = data.setdefault("mcpServers", {})
    if "forgemem" not in servers:
        servers["forgemem"] = {"command": "forgemem", "args": ["mcp"]}
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(data, indent=2))
        return True
    return False


def _generate_skill(agent: str, dry_run: bool = False) -> None:
    """Read template from forgemem/skills/{agent}.{ext} and write to SKILL_PATHS[agent]."""
    ext = "json" if agent == "codex" else "md"
    template = SKILL_TEMPLATES_DIR / f"{agent}.{ext}"
    dest = SKILL_PATHS[agent]

    if dry_run:
        console.print(f"  [dim]dry-run[/] would write: {dest}")
        return

    if not template.exists():
        console.print(f"  [yellow]warning:[/] skill template not found: {template}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    content = template.read_text()
    dest.write_text(content)
    console.print(f"  [green]wrote[/] {dest}")


def _auto_detect_and_generate_skills(yes: bool) -> None:
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
    if not yes:
        proceed = typer.confirm(
            f"Generate Forgemem skill files for detected agents ({agents_str})?",
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts"),
):
    """Initialize DB, register MCP server, and detect agent skills."""
    # Python version guard
    if sys.version_info < (3, 9):
        console.print("[red]error:[/] Python 3.9+ required")
        raise typer.Exit(1)

    # DB init
    from forgemem.core import DB_PATH, get_conn, INIT_SQL
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(INIT_SQL)
    conn.commit()
    conn.close()
    console.print(f"[green]DB initialized:[/] {DB_PATH}")

    # MCP registration
    settings_path = Path.home() / ".claude" / "settings.json"
    registered = _register_mcp(settings_path)
    if registered:
        console.print(f"[green]MCP registered[/] in {settings_path}")
    else:
        console.print("[dim]MCP already registered[/]")

    # Skill auto-detect
    _auto_detect_and_generate_skills(yes)

    console.print(Panel(
        "[bold green]Forgemem initialized successfully![/]\n"
        "Run [cyan]forgemem status[/] to verify, [cyan]forgemem start[/] to launch the MCP server.",
        title="Forgemem Init",
        expand=False,
    ))


@app.command()
def start(
    schedule: Annotated[
        Optional[str],
        typer.Option(help="login|hourly|manual"),
    ] = None,
):
    """Start the MCP server. On macOS: installs a LaunchAgent plist."""
    if sys.platform != "darwin":
        console.print("[yellow]warning:[/] LaunchAgent is macOS only. On Linux, run [cyan]forgemem mcp[/] directly.")
        raise typer.Exit(0)

    forgemem_bin = shutil.which("forgemem")
    if not forgemem_bin:
        console.print("[red]error:[/] 'forgemem' binary not found in PATH. Install the package first.")
        raise typer.Exit(1)

    schedule_xml: str
    if schedule == "login" or schedule is None:
        schedule_xml = "<key>RunAtLoad</key><true/>"
    elif schedule == "hourly":
        schedule_xml = "<key>StartInterval</key><integer>3600</integer>"
    elif schedule == "manual":
        schedule_xml = ""
    else:
        console.print(f"[red]error:[/] unknown schedule '{schedule}'. Use login|hourly|manual.")
        raise typer.Exit(1)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{forgemem_bin}</string>
        <string>mcp</string>
    </array>
    {schedule_xml}
    <key>StandardOutPath</key><string>{LOG_PATH}</string>
    <key>StandardErrorPath</key><string>{LOG_PATH}</string>
</dict>
</plist>
"""
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)
    console.print(f"[green]plist written:[/] {PLIST_PATH}")

    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print("[green]LaunchAgent loaded.[/]")
    else:
        console.print(f"[yellow]launchctl load returned {result.returncode}:[/] {result.stderr.strip()}")


@app.command()
def stop():
    """Unload the LaunchAgent and optionally remove the plist."""
    if sys.platform != "darwin":
        console.print("[yellow]warning:[/] LaunchAgent is macOS only.")
        raise typer.Exit(0)

    if not PLIST_PATH.exists():
        console.print(f"[yellow]plist not found:[/] {PLIST_PATH}")
        raise typer.Exit(0)

    result = subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print("[green]LaunchAgent unloaded.[/]")
    else:
        console.print(f"[yellow]launchctl unload returned {result.returncode}:[/] {result.stderr.strip()}")

    remove = typer.confirm("Remove plist file?", default=False)
    if remove:
        PLIST_PATH.unlink(missing_ok=True)
        console.print(f"[dim]removed[/] {PLIST_PATH}")


@app.command()
def status():
    """Show DB stats, server health, and skill status."""
    from forgemem.core import DB_PATH, get_conn

    # DB stats
    if not DB_PATH.exists():
        console.print(f"[red]DB not found:[/] {DB_PATH}  (run [cyan]forgemem init[/])")
        raise typer.Exit(1)

    conn = get_conn()
    t_total     = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    p_total     = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
    undistilled = conn.execute("SELECT COUNT(*) FROM traces WHERE distilled=0").fetchone()[0]
    conn.close()

    console.print(Panel(
        f"[bold]DB:[/] {DB_PATH}\n"
        f"Traces: {t_total}  |  Principles: {p_total}  |  Undistilled: {undistilled}",
        title="Forgemem Status",
        expand=False,
    ))

    # Server health (macOS LaunchAgent)
    if sys.platform == "darwin":
        if PLIST_PATH.exists():
            console.print(f"[green]LaunchAgent plist:[/] {PLIST_PATH}")
        else:
            console.print("[dim]LaunchAgent not installed[/]")

    # Skill status
    console.print("\n[bold]Skills:[/]")
    for agent, path in SKILL_PATHS.items():
        if path.exists():
            console.print(f"  [green]✓[/] {agent}: {path}")
        else:
            console.print(f"  [dim]✗[/] {agent}: {path} (not installed)")

    # MCP registration
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            if "forgemem" in data.get("mcpServers", {}):
                console.print("\n[green]MCP:[/] registered in ~/.claude/settings.json")
            else:
                console.print("\n[yellow]MCP:[/] not registered (run [cyan]forgemem init[/])")
        except Exception:
            console.print("\n[yellow]MCP:[/] could not parse ~/.claude/settings.json")


@app.command()
def search(
    query: str,
    k: int = typer.Option(5, help="Max results"),
    project: Optional[str] = typer.Option(None),
    type: Optional[str] = typer.Option(None, help="success|failure|plan|note"),
    format: str = typer.Option("md", help="md|json"),
):
    """Search memory traces and principles."""
    from forgemem.core import cmd_retrieve

    args = argparse.Namespace(
        query=query,
        k=k,
        project=project,
        type=type,
        format=format,
    )
    cmd_retrieve(args)


@app.command()
def store(
    content: str,
    type: str = typer.Option("note", help="success|failure|plan|note"),
    project: Optional[str] = typer.Option(None),
    session: Optional[str] = typer.Option(None),
    distill: bool = typer.Option(False),
    principle: Optional[str] = typer.Option(None),
):
    """Save a memory trace."""
    from forgemem.core import cmd_save

    # Auto-detect project from git if not passed
    if project is None:
        project = _detect_project_from_git()

    args = argparse.Namespace(
        type=type,
        content=content,
        project=project,
        session=session,
        distill=distill,
        principle=principle,
        score=5,
        tags=None,
    )
    cmd_save(args)


@app.command()
def mine():
    """Run the memory scanner."""
    from forgemem import scanner
    scanner.run()


@app.command()
def distill(target: str = typer.Argument("all")):
    """Distill undistilled traces into principles."""
    from forgemem.core import cmd_distill

    # target can be a session id or "all"
    session = None if target == "all" else target
    args = argparse.Namespace(
        session=session,
        project=None,
    )
    cmd_distill(args)


@app.command()
def config(
    key: Optional[str] = typer.Argument(None),
    value: Optional[str] = typer.Argument(None),
):
    """Get or set configuration values."""
    config_path = Path.home() / ".config" / "forgemem" / "config.json"

    if key is None:
        # Print all config
        if config_path.exists():
            data = json.loads(config_path.read_text())
            console.print_json(json.dumps(data))
        else:
            console.print("[dim]No config found. Config path:[/] " + str(config_path))
        return

    # Load existing config
    data: dict = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except Exception:
            data = {}

    if value is None:
        # Get
        if key in data:
            console.print(f"{key} = {data[key]}")
        else:
            console.print(f"[yellow]{key}[/] not set")
    else:
        # Set
        data[key] = value
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(data, indent=2))
        console.print(f"[green]set[/] {key} = {value}")


@app.command()
def skill(
    action: str = typer.Argument("generate"),
    agent: Optional[str] = typer.Option(None, help="claude|gemini|codex"),
    dry_run: bool = typer.Option(False),
):
    """Generate, update, or list agent skill files."""
    if action == "list":
        console.print("[bold]Installed skills:[/]")
        for a, path in SKILL_PATHS.items():
            status_str = "[green]installed[/]" if path.exists() else "[dim]not installed[/]"
            console.print(f"  {a}: {path} — {status_str}")
        return

    if action in ("generate", "update"):
        agents_to_process: list[str]
        if agent:
            if agent not in SKILL_PATHS:
                console.print(f"[red]error:[/] unknown agent '{agent}'. Use claude|gemini|codex.")
                raise typer.Exit(1)
            agents_to_process = [agent]
        else:
            agents_to_process = list(SKILL_PATHS.keys())

        for a in agents_to_process:
            _generate_skill(a, dry_run=dry_run)
        return

    console.print(f"[red]error:[/] unknown action '{action}'. Use generate|update|list.")
    raise typer.Exit(1)


@app.command(hidden=True)
def mcp(http: bool = typer.Option(False)):
    """Run the MCP server (stdio only in v0.1)."""
    if http:
        console.print("[yellow]warning:[/] HTTP mode not supported in v0.1. Running stdio.", err=True)

    from forgemem import mcp_server
    mcp_server.mcp.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    app()


if __name__ == "__main__":
    main()
