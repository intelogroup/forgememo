#!/usr/bin/env python3
"""Forgemem CLI — flat ruff-style commands."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forgememo import __version__

# ---------------------------------------------------------------------------
# App + console
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="forgememo",
    help="Long-term memory store for AI agents.",
    add_completion=False,
)


def _make_console() -> Console:
    """Return a Console that won't crash on narrow-encoding terminals (Windows cp1252/ascii).

    Wraps stdout with errors='replace' so unencodable characters become '?' instead
    of raising UnicodeEncodeError.
    """
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
            pass  # sys.stdout has no .buffer (e.g. pytest capture) — fall through
    return Console()


console = _make_console()

# ASCII-safe symbols for cross-platform compatibility
_enc = (getattr(sys.stdout, "encoding", None) or "ascii").lower()
_UNICODE_OK = _enc not in ("ascii", "cp1252", "latin-1", "latin1")
CHECK = "✓" if _UNICODE_OK else "ok"
CROSS = "✗" if _UNICODE_OK else "x"


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"forgememo {__version__}")
        raise typer.Exit()


def _ver(v: str) -> tuple:
    """Parse a semver string into a comparable tuple of ints."""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _check_for_update() -> None:
    """Print a one-liner if a newer version is available on PyPI (cached 24h)."""
    import time

    cache_file = Path.home() / ".forgememo" / ".update_check"
    try:
        # Only check once per 24 hours
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < 86400:
                latest = cache_file.read_text().strip()
                if latest and _ver(__version__) >= _ver(latest):
                    # We're up-to-date or ahead of cached value — stale cache, re-check fresh
                    cache_file.unlink(missing_ok=True)
                elif latest and _ver(latest) > _ver(__version__):
                    console.print(
                        f"[yellow]Update available:[/] forgememo {latest} "
                        f"(you have {__version__}) — run [bold]pip install -U forgememo[/]"
                    )
                    return
                else:
                    return  # empty/malformed cache — fall through to fresh check
        import requests as _requests

        resp = _requests.get("https://pypi.org/pypi/forgememo/json", timeout=2)
        data = resp.json()
        latest = data["info"]["version"]
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(latest)
        if _ver(latest) > _ver(__version__):
            console.print(
                f"[yellow]Update available:[/] forgememo {latest} "
                f"(you have {__version__}) — run [bold]pip install -U forgememo[/]"
            )
    except Exception:
        pass  # Never block the CLI for an update check


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    from forgememo.core import DB_PATH

    _SKIP_AUTO_INIT = {"init", "mcp", "help", None}
    if ctx.invoked_subcommand not in _SKIP_AUTO_INIT and not DB_PATH.exists():
        console.print("[dim]First run detected — initializing forgememo...[/]")
        from forgememo.storage import init_db
        init_db()
        _register_mcp(Path.home() / ".claude" / "settings.json")
        _auto_detect_and_generate_skills(yes=True)
    if ctx.invoked_subcommand not in {"mcp"}:
        import atexit

        atexit.register(_check_for_update)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgememo.daemon.plist"
PLIST_LABEL = "com.forgememo.daemon"
WORKER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgememo.worker.plist"
WORKER_PLIST_LABEL = "com.forgememo.worker"
MINER_PLIST_PATH = (
    Path.home() / "Library" / "LaunchAgents" / "com.forgememo.miner.plist"
)
MINER_PLIST_LABEL = "com.forgememo.miner"
LOG_PATH = Path.home() / "Library" / "Logs" / "forgememo.log"

SKILL_PATHS: dict[str, Path] = {
    "claude": Path.home() / ".claude" / "skills" / "forgememo.md",
    "gemini": Path.home() / ".gemini" / "forgememo-skill.md",
    "codex": Path.home() / ".codex" / "forgememo-skill.json",
}

SKILL_TEMPLATES_DIR = Path(__file__).parent / "skills"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replace_block(text: str, start: str, end: str, block: str) -> str:
    """Idempotently replace a block delimited by start/end markers."""
    if start in text and end in text:
        pre, rest = text.split(start, 1)
        _, post = rest.split(end, 1)
        return f"{pre}{block}{post}"
    sep = "\n" if text.endswith("\n") else "\n\n"
    return f"{text}{sep}{block}\n"


def _format_context_markdown(project: str, updated: str, principles: list[dict], last_session: dict | None) -> str:
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
            tail = f" — {narrative}" if narrative else ""
            lines.append(f"- [{p.get('type','')}] {p.get('title','')} ({score_str}, {date}){tail}")

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
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(data, indent=2))
        return True
    return False


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
# Helpers (provider setup)
# ---------------------------------------------------------------------------


def _configure_provider_noninteractive(provider: str) -> None:
    """Configure provider non-interactively (for --provider flag)."""
    from forgememo import config as fm_cfg

    valid = ["anthropic", "openai", "gemini", "ollama", "claude_code", "forgememo"]
    if provider not in valid:
        console.print(f"[red]Invalid provider: {provider}[/]")
        console.print(f"Valid providers: {', '.join(valid)}")
        raise typer.Exit(1)

    if provider == "forgememo":
        fm_cfg.set_provider("forgememo")
        console.print(
            "[green]Provider set to forgememo.[/] Run [cyan]forgememo auth[/] to sign in."
        )
    elif provider == "ollama":
        fm_cfg.set_provider("ollama")
        console.print(
            "[green]Provider set to ollama.[/] Make sure it's running: [cyan]ollama serve[/]"
        )
    elif provider == "claude_code":
        fm_cfg.set_provider("claude_code")
        console.print(
            "[green]Provider set to claude_code.[/] Uses your [cyan]claude[/] CLI session — no API key needed.\n"
            "  Make sure Claude Code is installed and logged in: [cyan]claude login[/]"
        )
    else:
        fm_cfg.set_provider(provider)
        console.print(
            f"[green]Provider set to {provider}.[/] "
            f"Set [cyan]{provider.upper()}_API_KEY[/] env var or run:\n"
            f"  [cyan]forgememo config {provider} --key YOUR_KEY[/]"
        )


def _prompt_provider_setup(yes: bool) -> None:
    """If no provider is configured, require interactive provider selection."""
    from forgememo import config as fm_cfg

    if fm_cfg.load().get("provider") is not None:
        return  # already configured (e.g. Ollama was just set above)

    # Non-interactive mode: warn visibly and skip
    if yes or not sys.stdin.isatty():
        console.print(
            Panel(
                "[bold yellow]Interactive provider setup is required on first run.[/]\n\n"
                "Re-run [cyan]forgememo init[/] in a real terminal to choose your provider.\n"
                "Agents and non-TTY sessions cannot bypass this step.\n\n"
                "If you already configured a provider earlier, run:\n"
                "  [cyan]forgememo start[/]",
                title="[bold red]INTERACTIVE SETUP REQUIRED[/]",
                border_style="red",
                expand=False,
            )
        )
        raise typer.Exit(code=1)

    # Interactive menu
    import questionary  # lazy: only needed for the interactive provider picker

    _choices = [
        questionary.Choice(
            "forgememo   (recommended — works with any AI tool, sign in once, no key)", value="forgememo"
        ),
        questionary.Choice(
            "claude_code (Claude subscription via `claude` CLI — no API key needed)", value="claude_code"
        ),
        questionary.Choice(
            "ollama     (local, free, fully private — needs ollama running)", value="ollama"
        ),
        questionary.Choice(
            "anthropic  (BYOK — needs ANTHROPIC_API_KEY)", value="anthropic"
        ),
        questionary.Choice("openai     (BYOK — needs OPENAI_API_KEY)", value="openai"),
        questionary.Choice("gemini     (BYOK — needs GEMINI_API_KEY)", value="gemini"),
        questionary.Choice(
            "skip for now  (configure later with forgememo config)", value=None
        ),
    ]
    provider = questionary.select(
        "Choose an inference provider for memory distillation:",
        choices=_choices,
        default=_choices[0],
    ).ask()

    if not provider:
        console.print(
            "[dim]Skipped — run [cyan]forgememo config[/] to set a provider later.[/]"
        )
        return

    if provider == "forgememo":
        fm_cfg.set_provider("forgememo")
        console.print("[green]Provider set to forgememo.[/] Let's authenticate now...")
        _do_auth_login()
        return

    if provider == "claude_code":
        fm_cfg.set_provider("claude_code")
        console.print(
            "[green]Provider set to claude_code.[/] "
            "Forgememo will use your [cyan]claude[/] CLI session — no API key needed.\n"
            "  Make sure you're logged in: [cyan]claude login[/]"
        )
        return

    if provider == "ollama":
        fm_cfg.set_provider("ollama")
        console.print(
            "[green]Provider set to ollama.[/] Make sure it's running: [cyan]ollama serve[/]"
        )
        return

    key = typer.prompt(
        f"Enter your {provider} API key (press Enter to skip)",
        default="",
        hide_input=True,
    )
    fm_cfg.set_provider(provider, api_key=key or None)
    if key:
        console.print(
            f"[green]Provider set to {provider}[/] — API key stored in {fm_cfg.CONFIG_PATH}"
        )
    else:
        console.print(
            f"[green]Provider set to {provider}.[/] "
            f"[dim]Set [cyan]{provider.upper()}_API_KEY[/] env var to supply the key.[/]"
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts"),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Provider: anthropic | openai | gemini | ollama | forgememo (skips interactive picker)",
    ),
):
    """Initialize DB, register MCP server, and detect agent skills."""
    # Python version guard
    if sys.version_info < (3, 9):
        console.print("[red]error:[/] Python 3.9+ required")
        raise typer.Exit(1)

    # DB init (v2 schema: events + distilled_summaries + session_summaries + legacy compat)
    from forgememo.storage import init_db, DB_PATH

    init_db()
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

    # Provider setup — runs only if still unconfigured (Ollama declined or not detected)
    from forgememo import config as fm_cfg

    # If provider provided as argument, configure it directly (non-interactive)
    if provider:
        _configure_provider_noninteractive(provider)
        console.print(f"[green]Provider set to {provider} via --provider flag.[/]")
    else:
        _prompt_provider_setup(yes)

    provider_configured = fm_cfg.load().get("provider") is not None

    if not provider_configured:
        console.print(
            Panel(
                "[bold green]Forgemem initialized successfully![/]\n\n"
                "[bold red]⚠  REQUIRED BEFORE USE — configure a provider now:[/]\n"
                "  [cyan]forgememo config anthropic --key sk-ant-...[/]\n"
                "  [cyan]forgememo config openai    --key sk-...[/]\n"
                "  [cyan]forgememo config gemini    --key AIza...[/]\n"
                "  [cyan]forgememo config ollama[/]               [dim](local, free)[/]\n"
                "  [cyan]forgememo config forgememo[/]             [dim](managed, no key needed)[/]\n\n"
                "After configuring a provider, run:\n"
                "  [cyan]forgememo start[/]  →  restart your agent  →  [cyan]forgememo status[/]",
                title="[bold red]ACTION REQUIRED — provider not set[/]",
                border_style="red",
                expand=False,
            )
        )
        raise typer.Exit(code=1)
    else:
        console.print(
            Panel(
                "[bold green]Forgemem initialized successfully![/]\n\n"
                "[bold]Next steps:[/]\n"
            "  1. [cyan]forgememo start[/]          — launch the daemon + worker\n"
                "  2. Restart Claude Code / your AI agent to pick up the MCP connection\n"
                "  3. [cyan]forgememo status[/]         — verify everything is running\n\n"
                "[bold]Key commands:[/]\n"
                '  [cyan]forgememo store "<text>"[/]   — save a memory manually\n'
                '  [cyan]forgememo search "<query>"[/] — search stored memories\n'
                "  [cyan]forgememo mine[/]              — scan recent work and extract memories\n"
                "  [cyan]forgememo distill[/]           — condense traces into lasting principles\n"
                "  [cyan]forgememo config[/]            — set inference provider (anthropic/ollama/…)\n\n"
                "Run [cyan]forgememo help[/] at any time to see this again.",
                title="Forgemem Ready",
                expand=False,
            )
        )
        console.print("\n[dim]Auto-starting MCP server…[/]")
        _do_start()


def _do_start(
    schedule: Optional[str] = None,
    mine: bool = False,
    mine_interval: int = 3600,
) -> None:
    """Core start logic — install and load LaunchAgent(s). Called by start() and init()."""
    if sys.platform == "linux":
        forgememo_bin = shutil.which("forgememo") or "forgememo"
        console.print(
            "[bold]Linux detected.[/] To run forgememo as a systemd user service, create:"
        )
        console.print("\n  [dim]~/.config/systemd/user/forgememo-daemon.service[/]\n")
        console.print(
            f"[dim]  [Unit]\n"
            f"  Description=Forgemem Daemon\n"
            f"  After=default.target\n\n"
            f"  [Service]\n"
            f"  ExecStart={forgememo_bin} daemon\n"
            f"  Restart=on-failure\n\n"
            f"  [Install]\n"
            f"  WantedBy=default.target[/]\n"
        )
        console.print("\n  [dim]~/.config/systemd/user/forgememo-worker.service[/]\n")
        console.print(
            f"[dim]  [Unit]\n"
            f"  Description=Forgemem Worker\n"
            f"  After=default.target\n\n"
            f"  [Service]\n"
            f"  ExecStart={forgememo_bin} worker\n"
            f"  Restart=on-failure\n\n"
            f"  [Install]\n"
            f"  WantedBy=default.target[/]\n"
        )
        console.print("Then enable them with:")
        console.print("  [cyan]systemctl --user daemon-reload[/]")
        console.print("  [cyan]systemctl --user enable --now forgememo-daemon[/]")
        console.print("  [cyan]systemctl --user enable --now forgememo-worker[/]")
        console.print("\nOr just run directly: [cyan]forgememo daemon[/]")
        raise typer.Exit(0)

    if sys.platform == "win32":
        forgememo_bin = shutil.which("forgememo") or "forgememo"
        console.print(
            "[bold]Windows detected.[/] To run forgememo at login via Task Scheduler, run:"
        )
        console.print(
            f'\n  [cyan]schtasks /create /tn "Forgemem Daemon" /tr "{forgememo_bin} daemon" '
            f"/sc ONLOGON /f[/]\n"
        )
        console.print(
            f'\n  [cyan]schtasks /create /tn "Forgemem Worker" /tr "{forgememo_bin} worker" '
            f"/sc ONLOGON /f[/]\n"
        )
        console.print("Or just run directly in a terminal: [cyan]forgememo daemon[/]")
        raise typer.Exit(0)

    if sys.platform != "darwin":
        console.print(
            f"[yellow]Unsupported platform '{sys.platform}'.[/] Run [cyan]forgememo daemon[/] directly."
        )
        raise typer.Exit(0)

    forgememo_bin = shutil.which("forgememo")
    if not forgememo_bin:
        console.print(
            "[red]error:[/] 'forgememo' binary not found in PATH. Install the package first."
        )
        raise typer.Exit(1)

    schedule_xml: str
    if schedule == "login" or schedule is None:
        schedule_xml = "<key>RunAtLoad</key><true/>"
    elif schedule == "hourly":
        schedule_xml = "<key>StartInterval</key><integer>3600</integer>"
    elif schedule == "manual":
        schedule_xml = ""
    else:
        console.print(
            f"[red]error:[/] unknown schedule '{schedule}'. Use login|hourly|manual."
        )
        raise typer.Exit(1)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{forgememo_bin}</string>
        <string>daemon</string>
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
        console.print(
            f"[yellow]launchctl load returned {result.returncode}:[/] {result.stderr.strip()}"
        )

    worker_plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{WORKER_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{forgememo_bin}</string>
        <string>worker</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>{LOG_PATH}</string>
    <key>StandardErrorPath</key><string>{LOG_PATH}</string>
</dict>
</plist>
"""
    WORKER_PLIST_PATH.write_text(worker_plist_content)
    console.print(f"[green]worker plist written:[/] {WORKER_PLIST_PATH}")
    worker_result = subprocess.run(
        ["launchctl", "load", str(WORKER_PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if worker_result.returncode == 0:
        console.print("[green]worker loaded.[/]")
    else:
        console.print(
            f"[yellow]launchctl load (worker) returned {worker_result.returncode}:[/] {worker_result.stderr.strip()}"
        )

    if mine:
        miner_plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{MINER_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{forgememo_bin}</string>
        <string>mine</string>
    </array>
    <key>StartInterval</key><integer>{mine_interval}</integer>
    <key>StandardOutPath</key><string>{LOG_PATH}</string>
    <key>StandardErrorPath</key><string>{LOG_PATH}</string>
</dict>
</plist>
"""
        MINER_PLIST_PATH.write_text(miner_plist_content)
        console.print(f"[green]miner plist written:[/] {MINER_PLIST_PATH}")
        miner_result = subprocess.run(
            ["launchctl", "load", str(MINER_PLIST_PATH)],
            capture_output=True,
            text=True,
        )
        if miner_result.returncode == 0:
            console.print(f"[green]mining agent loaded[/] (interval: {mine_interval}s)")
        else:
            console.print(
                f"[yellow]launchctl load (miner) returned {miner_result.returncode}:[/] {miner_result.stderr.strip()}"
            )


@app.command()
def start(
    schedule: Annotated[
        Optional[str],
        typer.Option(help="login|hourly|manual"),
    ] = None,
    mine: Annotated[
        bool,
        typer.Option(
            "--mine/--no-mine", help="Also install a mining LaunchAgent (macOS only)."
        ),
    ] = False,
    mine_interval: Annotated[
        int, typer.Option(help="Mining interval in seconds (default: 3600).")
    ] = 3600,
):
    """Start the MCP server. On macOS: installs a LaunchAgent plist."""
    from forgememo import config as fm_cfg

    if fm_cfg.load().get("provider") is None:
        console.print(
            Panel(
                "[bold red]No inference provider configured.[/]\n\n"
                "Run one of these first, then retry [cyan]forgememo start[/]:\n"
                "  [cyan]forgememo config anthropic --key sk-ant-...[/]\n"
                "  [cyan]forgememo config openai    --key sk-...[/]\n"
                "  [cyan]forgememo config gemini    --key AIza...[/]\n"
                "  [cyan]forgememo config ollama[/]               [dim](local, free)[/]\n"
                "  [cyan]forgememo config forgememo[/]             [dim](managed, no key needed)[/]",
                title="[bold red]ACTION REQUIRED — configure provider first[/]",
                border_style="red",
                expand=False,
            )
        )
        raise typer.Exit(code=1)
    _do_start(schedule=schedule, mine=mine, mine_interval=mine_interval)


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
        console.print(
            f"[yellow]launchctl unload returned {result.returncode}:[/] {result.stderr.strip()}"
        )

    if MINER_PLIST_PATH.exists():
        miner_result = subprocess.run(
            ["launchctl", "unload", str(MINER_PLIST_PATH)],
            check=False,
            capture_output=True,
            text=True,
        )
        if miner_result.returncode == 0:
            console.print("[green]mining agent unloaded.[/]")
        else:
            console.print(
                f"[yellow]launchctl unload (miner) returned {miner_result.returncode}:[/] {miner_result.stderr.strip()}"
            )

    if WORKER_PLIST_PATH.exists():
        worker_result = subprocess.run(
            ["launchctl", "unload", str(WORKER_PLIST_PATH)],
            check=False,
            capture_output=True,
            text=True,
        )
        if worker_result.returncode == 0:
            console.print("[green]worker unloaded.[/]")
        else:
            console.print(
                f"[yellow]launchctl unload (worker) returned {worker_result.returncode}:[/] {worker_result.stderr.strip()}"
            )

    remove = typer.confirm("Remove plist file(s)?", default=False)
    if remove:
        PLIST_PATH.unlink(missing_ok=True)
        console.print(f"[dim]removed[/] {PLIST_PATH}")
        WORKER_PLIST_PATH.unlink(missing_ok=True)
        console.print(f"[dim]removed[/] {WORKER_PLIST_PATH}")
        if MINER_PLIST_PATH.exists():
            MINER_PLIST_PATH.unlink(missing_ok=True)
            console.print(f"[dim]removed[/] {MINER_PLIST_PATH}")


@app.command()
def status(
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON for scripting"
    ),
):
    """Show DB stats, server health, and skill status."""
    from forgememo.core import DB_PATH, get_conn
    from forgememo import config as fm_cfg
    from forgememo.config import detect_ollama

    # Credits-exhausted warning (shown before anything else)
    flag = fm_cfg.get_credits_flag()
    if flag and not json_output:
        console.print(
            Panel(
                f"[bold]Scheduled runs have stopped — inference credits exhausted.[/]\n\n"
                f"Balance: [red]${flag['balance_usd']}[/]  ·  Last failed: {flag['ts'][:10]}\n\n"
                f"  Add credits → [cyan]https://forgememo.com/billing[/]\n"
                f"  Or switch provider → [cyan]forgememo config provider anthropic --key sk-ant-...[/]",
                title="[bold red]ACTION REQUIRED — credits exhausted[/]",
                border_style="red",
                expand=False,
            )
        )

    # DB stats
    if not DB_PATH.exists():
        if json_output:
            console.print('{"error": "DB not found — run forgememo init"}')
        else:
            console.print(
                f"[red]DB not found:[/] {DB_PATH}  (run [cyan]forgememo init[/])"
            )
        raise typer.Exit(1)

    conn = get_conn()
    t_total = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    p_total = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
    undistilled = conn.execute(
        "SELECT COUNT(*) FROM traces WHERE distilled=0"
    ).fetchone()[0]
    conn.close()

    provider = fm_cfg.get_provider() or "not set"

    # MCP registration
    settings_path = Path.home() / ".claude" / "settings.json"
    mcp_registered = False
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            mcp_registered = "forgememo" in data.get("mcpServers", {})
        except Exception:
            pass

    # Skill status
    skills_status = {agent: path.exists() for agent, path in SKILL_PATHS.items()}

    if json_output:
        import json as _json

        out = {
            "db": str(DB_PATH),
            "traces": t_total,
            "principles": p_total,
            "undistilled": undistilled,
            "provider": provider,
            "mcp_registered": mcp_registered,
            "skills": skills_status,
        }
        console.print(_json.dumps(out))
        return

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("key", style="bold", min_width=14)
    table.add_column("value")

    table.add_row("DB", str(DB_PATH))
    table.add_row("Traces", str(t_total))
    table.add_row("Principles", str(p_total))
    undistilled_val = (
        f"[yellow]{undistilled}[/]  → run: [cyan]forgememo distill[/]"
        if undistilled
        else str(undistilled)
    )
    table.add_row("Undistilled", undistilled_val)
    table.add_row(
        "Provider",
        f"[green]{provider}[/]"
        if provider != "not set"
        else "[yellow]not set[/]  → run: [cyan]forgememo config[/]",
    )
    table.add_row(
        "MCP",
        f"[green]{CHECK} registered[/]"
        if mcp_registered
        else f"[yellow]{CROSS} not registered[/]  → run: [cyan]forgememo init[/]",
    )

    skills_str = "  ".join(
        f"[green]{a} {CHECK}[/]" if ok else f"[dim]{a} {CROSS}[/]"
        for a, ok in skills_status.items()
    )
    table.add_row("Skills", skills_str)

    # LaunchAgent / daemon status
    if sys.platform == "darwin":
        daemon_val = (
            f"[green]{CHECK} plist installed[/]"
            if PLIST_PATH.exists()
            else f"[dim]{CROSS} not installed[/]  → run: [cyan]forgememo start[/]"
        )
        worker_val = (
            f"[green]{CHECK} plist installed[/]"
            if WORKER_PLIST_PATH.exists()
            else f"[dim]{CROSS} not installed[/]  → run: [cyan]forgememo start[/]"
        )
        table.add_row("Daemon", daemon_val)
        table.add_row("Worker", worker_val)

    console.print(Panel(table, title="Forgemem Status", expand=False))

    # Ollama health (only shown when ollama is the active provider)
    if provider == "ollama":
        console.print("\n[bold]Ollama:[/]")
        ollama = detect_ollama()
        if ollama:
            console.print(f"  [green]{CHECK} running[/] at {ollama['url']}")
            if ollama["models"]:
                console.print("  Models: " + ", ".join(ollama["models"][:8]))
            else:
                console.print(
                    "  [yellow]No models pulled.[/] Run: ollama pull llama3.2"
                )
        else:
            url = fm_cfg.get_ollama_url()
            console.print(f"  [red]{CROSS} not reachable[/] at {url}")
            console.print("  Start with: [cyan]ollama serve[/]")


@app.command()
def search(
    query: str,
    k: int = typer.Option(5, help="Max results"),
    project: Optional[str] = typer.Option(None),
    type: Optional[str] = typer.Option(None, help="success|failure|plan|note"),
    format: str = typer.Option("md", help="md|json"),
):
    """Search memory traces and principles."""
    from forgememo.core import cmd_retrieve

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
    from forgememo.core import cmd_save

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
    from forgememo import scanner

    with console.status("[dim]Scanning repos and memory files...[/]"):
        scanner.main()


@app.command()
def distill(target: str = typer.Argument("all")):
    """Distill undistilled traces into principles."""
    from forgememo.core import cmd_distill

    # target can be a session id or "all"
    session = None if target == "all" else target
    args = argparse.Namespace(
        session=session,
        project=None,
    )
    with console.status("[dim]Distilling traces into principles...[/]"):
        cmd_distill(args)


@app.command()
def config(
    provider: Optional[str] = typer.Argument(
        None, help="Provider: anthropic | openai | gemini | ollama | forgememo"
    ),
    key: Optional[str] = typer.Option(
        None, "--key", "-k", help="API key for the provider (stored locally)"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Override default model for this provider"
    ),
    ollama_url: Optional[str] = typer.Option(
        None, "--ollama-url", help="Ollama base URL (default: http://localhost:11434)"
    ),
    show: bool = typer.Option(
        False, "--show", help="Print current config (masks keys)"
    ),
):
    """Configure AI provider and API keys.

    Examples:\n
      forgememo config                                    # show current config\n
      forgememo config anthropic --key sk-ant-...         # set provider + key\n
      forgememo config openai --key sk-...                # switch to OpenAI\n
      forgememo config gemini --key AIza...               # switch to Gemini\n
      forgememo config ollama                             # use local Ollama (free, private)\n
      forgememo config ollama --model llama3.2            # use specific Ollama model\n
      forgememo config ollama --ollama-url http://host:11434  # remote Ollama\n
      forgememo config forgememo                           # use Forgemem starter inference\n
    """
    from forgememo import config as fm_cfg

    if provider is None or show:
        current = fm_cfg.load()
        active = current.get("provider", "anthropic")
        keys = current.get("api_keys", {})
        masked = {
            p: v[:8] + "..." + v[-4:] if len(v) > 12 else "***" for p, v in keys.items()
        }

        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column("key", style="bold", min_width=12)
        tbl.add_column("value")

        tbl.add_row("Provider", f"[green]{active}[/]")
        tbl.add_row(
            "Model",
            current.get("model") or fm_cfg.DEFAULT_MODELS.get(active, "default"),
        )
        tbl.add_row("Config", str(fm_cfg.CONFIG_PATH))
        tbl.add_row(
            "Keys", str(masked) if masked else "[dim](none stored - using env vars)[/]"
        )
        if active == "ollama":
            tbl.add_row("Ollama URL", fm_cfg.get_ollama_url())
        console.print(Panel(tbl, title="Forgemem Config", expand=False))
        return

    if provider not in fm_cfg.SUPPORTED_PROVIDERS:
        console.print(
            f"[red]Unknown provider '{provider}'.[/] Choose: {', '.join(fm_cfg.SUPPORTED_PROVIDERS)}"
        )
        raise typer.Exit(1)

    fm_cfg.set_provider(provider, api_key=key)

    if provider == "ollama":
        from forgememo.config import detect_ollama

        ollama = detect_ollama()
        if ollama:
            console.print(f"[cyan]Ollama detected[/] at {ollama['url']}")
            if ollama["models"]:
                console.print("  Available models: " + ", ".join(ollama["models"][:8]))
                if not model:
                    model = ollama["models"][0]
                    console.print(f"[green]Auto-selected model:[/] {model}")
            else:
                console.print(
                    "  [yellow]No models pulled yet.[/] Run: ollama pull llama3.2"
                )
        else:
            console.print(
                "[yellow]Ollama not detected[/] at default port. Start it with: ollama serve"
            )

    cfg_data = fm_cfg.load()
    if model:
        cfg_data["model"] = model
        fm_cfg.save(cfg_data)
    if ollama_url and provider == "ollama":
        cfg_data["ollama_url"] = ollama_url
        fm_cfg.save(cfg_data)

    msg = f"[green]Provider set to:[/] {provider}"
    if provider == "ollama":
        url = ollama_url or fm_cfg.get_ollama_url()
        used_model = model or fm_cfg.DEFAULT_MODELS["ollama"]
        msg += f"\n[dim]Ollama URL:[/] {url}"
        msg += f"\n[dim]Model:[/] {used_model}"
        msg += (
            "\n[green]Inference runs locally — your traces never leave your machine.[/]"
        )
    elif key:
        msg += f"\n[green]API key stored[/] in {fm_cfg.CONFIG_PATH}"
    else:
        msg += "\n[dim]No key stored — will fall back to env var[/]"
    if provider == "forgememo":
        console.print(msg)
        console.print("[green]Provider set to forgememo.[/] Let's authenticate now...")
        _do_auth_login()
        return
    console.print(msg)


def _check_api_response(resp, console) -> None:
    """Handle common API error codes before raise_for_status()."""
    if resp.status_code == 401:
        console.print("[yellow]Session expired.[/] Run: [bold]forgememo auth login[/]")
        raise typer.Exit(1)
    if resp.status_code == 402:
        console.print(
            "[yellow]Sync requires a Sync subscription.[/] "
            "Upgrade at: https://forgememo.com/billing"
        )
        raise typer.Exit(1)


def _do_auth_login() -> bool:
    """Run the browser-based OAuth login flow. Returns True on success, exits on failure."""
    import webbrowser
    import http.server
    import threading
    import secrets
    import urllib.parse
    from forgememo import config as fm_cfg

    port = 0
    state = secrets.token_urlsafe(16)
    received_token: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("state", [""])[0] == state and "token" in params:
                received_token["value"] = params["token"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h2>Authenticated! You can close this tab.</h2>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args):  # silence request logs
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    except OSError:
        console.print(
            "[red]error:[/] could not bind a local callback port. Try again."
        )
        raise typer.Exit(1)
    port = server.server_address[1]

    def _serve():
        server.handle_request()  # handle one request then stop
        server.server_close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    _api_base = os.environ.get(
        "FORGEMEM_API_URL", "https://forgememo-server.onrender.com"
    )
    login_url = (
        f"{_api_base}/cli-auth?callback=http://127.0.0.1:{port}/callback&state={state}"
    )
    console.print(f"Opening browser to authenticate...\n{login_url}")
    webbrowser.open(login_url)
    console.print("[dim]Waiting for browser callback (Ctrl+C to cancel)...[/]")

    t.join(timeout=120)

    if received_token.get("value"):
        cfg_data = fm_cfg.load()
        cfg_data["forgememo_token"] = received_token["value"]
        cfg_data["provider"] = "forgememo"
        fm_cfg.save(cfg_data)
        fm_cfg.clear_credits_flag()
        console.print("[green]Authenticated![/] Provider set to Forgemem Inference.")
        console.print("[dim]Your $5 free credits are ready.[/]")
        return True
    else:
        console.print("[red]Login timed out or was cancelled.[/]")
        raise typer.Exit(1)


_POST_AUTH_TIMEOUT = 60  # seconds to wait for billing events


def _do_post_auth_setup(jwt: str) -> list:
    """After login: check balance, optionally open browser for billing setup.

    Returns list of received event dicts. Returns [] if balance sufficient or timeout.
    """
    import webbrowser
    import http.server
    import threading
    import secrets
    import urllib.parse
    import time
    import requests as _req

    _api_base = os.environ.get(
        "FORGEMEM_API_URL", "https://forgememo-server.onrender.com"
    )

    try:
        resp = _req.get(
            f"{_api_base}/v1/balance",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=5,
        )
        if resp.status_code == 200 and resp.json().get("balance_usd", 0.0) > 2.0:
            console.print(f"[dim]Balance: ${resp.json()['balance_usd']:.2f}[/]")
            return []
    except Exception:
        pass

    port = 0
    state = secrets.token_urlsafe(16)
    received_events: list = []

    class _EventHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("state", [""])[0] == state:
                event = {k: v[0] for k, v in params.items()}
                received_events.append(event)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h2>Done! You can close this tab.</h2>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args):
            pass

    class _ReuseAddrServer(http.server.HTTPServer):
        allow_reuse_address = True

    try:
        server = _ReuseAddrServer(("127.0.0.1", port), _EventHandler)
    except OSError:
        return []
    port = server.server_address[1]

    server.timeout = 1.0

    def _serve_events():
        deadline = time.time() + _POST_AUTH_TIMEOUT
        while time.time() < deadline and len(received_events) < 2:
            server.handle_request()
        server.server_close()

    t = threading.Thread(target=_serve_events, daemon=True)
    t.start()

    billing_url = (
        f"{_api_base}/billing/cli-setup"
        f"?cli_callback={urllib.parse.quote(f'http://127.0.0.1:{port}/event', safe='')}"
        f"&state={state}"
        f"&token={urllib.parse.quote(jwt)}"
    )
    console.print("\n[bold]Add credits to keep using Forgemem.[/]")
    console.print(f"Opening billing setup...\n{billing_url}")
    webbrowser.open(billing_url)
    console.print("[dim]Waiting for billing events (Ctrl+C to skip)...[/]")

    t.join(timeout=_POST_AUTH_TIMEOUT + 5)

    card_event = next(
        (e for e in received_events if e.get("type") == "card_added"), None
    )
    credits_event = next(
        (e for e in received_events if e.get("type") == "credits_added"), None
    )

    if card_event:
        console.print("[green]Payment method added![/]")
    if credits_event:
        amount = credits_event.get("amount", "?")
        console.print(f"[green]${amount} credits added![/] You're ready to go.")
    if not card_event and not credits_event:
        console.print(
            "[dim]Skipped. Run 'forgememo auth credits' later to add credits.[/]"
        )

    return received_events


@app.command()
def auth(
    action: str = typer.Argument("status", help="login | logout | status"),
):
    """Authenticate with Forgemem for managed inference.

    Examples:\n
      forgememo auth login    # open browser, store token\n
      forgememo auth status   # show current auth state\n
      forgememo auth logout   # remove stored token\n
    """
    from forgememo import config as fm_cfg

    if action == "status":
        token = fm_cfg.load().get("forgememo_token")
        if token:
            console.print("[green]Authenticated[/] with Forgemem Inference")
            console.print(f"[dim]Token: {token[:8]}...{token[-4:]}[/]")
            console.print("Run [bold]forgememo config[/] to see full provider state.")
        else:
            console.print("[yellow]Not authenticated.[/] Run: forgememo auth login")
        return

    if action == "logout":
        cfg_data = fm_cfg.load()
        if "forgememo_token" in cfg_data:
            del cfg_data["forgememo_token"]
            fm_cfg.save(cfg_data)
            console.print("[green]Logged out.[/] Token removed.")
        else:
            console.print("[dim]Not logged in.[/]")
        return

    if action == "login":
        result = _do_auth_login()
        if result:
            from forgememo import config as fm_cfg

            token = fm_cfg.load().get("forgememo_token", "")
            _do_post_auth_setup(token)
        return

    console.print(f"[red]Unknown action '{action}'.[/] Use: login | logout | status")
    raise typer.Exit(1)


@app.command()
def sync(
    push_only: bool = typer.Option(
        False, "--push-only", help="Push local changes only"
    ),
    pull_only: bool = typer.Option(
        False, "--pull-only", help="Pull remote changes only"
    ),
):
    """Sync local memory with Forgemem cloud (requires Sync subscription).

    Pushes new local traces + principles to the cloud, then pulls changes
    from your other devices since the last sync. Safe to run repeatedly — all
    operations are idempotent.

    Examples:\n
      forgememo sync              # push + pull\n
      forgememo sync --push-only  # push local changes only\n
      forgememo sync --pull-only  # pull remote changes only (e.g. from background task)\n
    """
    import sqlite3
    import requests as req
    from datetime import datetime, timezone
    from forgememo import config as fm_cfg
    from forgememo.core import DB_PATH

    token = fm_cfg.load().get("forgememo_token")
    if not token:
        console.print("[yellow]Not authenticated.[/] Run: forgememo auth login")
        raise typer.Exit(1)

    managed_url = os.environ.get(
        "FORGEMEM_API_URL", "https://forgememo-server.onrender.com"
    )
    device_id = fm_cfg.get_device_id()
    last_sync = fm_cfg.get_last_sync_ts()
    headers = {"Authorization": f"Bearer {token}"}

    # ── Push ──────────────────────────────────────────────────────────────────
    if not pull_only:
        if not DB_PATH.exists():
            console.print("[yellow]No local DB found.[/] Run: forgememo init")
            raise typer.Exit(1)

        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row

        traces = [
            dict(r)
            for r in conn.execute(
                "SELECT id as local_id, ts, session_id, project_tag, type, content, distilled "
                "FROM traces WHERE ts > ?",
                (last_sync,),
            ).fetchall()
        ]
        principles = [
            dict(r)
            for r in conn.execute(
                "SELECT id as local_id, source_trace_id as source_local_id, project_tag, "
                "type, principle, impact_score, tags FROM principles WHERE ts > ?",
                (last_sync,),
            ).fetchall()
        ]
        conn.close()

        if traces or principles:
            try:
                resp = req.post(
                    f"{managed_url}/v1/sync/push",
                    json={
                        "device_id": device_id,
                        "device_name": os.uname().nodename
                        if hasattr(os, "uname")
                        else "",
                        "traces": traces,
                        "principles": principles,
                    },
                    headers=headers,
                    timeout=30,
                )
                _check_api_response(resp, console)
                resp.raise_for_status()
                data = resp.json()
                console.print(
                    f"[green]Pushed[/] {data.get('pushed_traces', 0)} trace(s), "
                    f"{data.get('pushed_principles', 0)} principle(s)"
                )
            except req.exceptions.ConnectionError:
                console.print(
                    "[red]Could not reach api.forgememo.com.[/] Check your connection."
                )
                raise typer.Exit(1)
        else:
            console.print("[dim]Nothing new to push.[/]")

    # ── Pull ──────────────────────────────────────────────────────────────────
    if not push_only:
        try:
            resp = req.get(
                f"{managed_url}/v1/sync/pull",
                params={"since": last_sync, "device_id": device_id},
                headers=headers,
                timeout=30,
            )
            _check_api_response(resp, console)
            resp.raise_for_status()
            data = resp.json()
        except req.exceptions.ConnectionError:
            console.print(
                "[red]Could not reach api.forgememo.com.[/] Check your connection."
            )
            raise typer.Exit(1)

        remote_traces = data.get("traces", [])
        remote_principles = data.get("principles", [])
        server_ts = data.get("server_ts", datetime.now(timezone.utc).isoformat())

        if remote_traces or remote_principles:
            if not DB_PATH.exists():
                console.print("[yellow]No local DB found.[/] Run: forgememo init")
                raise typer.Exit(1)

            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            inserted_t = inserted_p = 0
            for t in remote_traces:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO traces "
                        "(session_id, project_tag, type, content, distilled) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            t.get("session_id"),
                            t.get("project_tag"),
                            t.get("type", "note"),
                            t["content"],
                            int(t.get("distilled", False)),
                        ),
                    )
                    inserted_t += 1
                except Exception as e:
                    console.print(f"  [yellow]warning:[/] skipped trace: {e}")
            for p in remote_principles:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO principles "
                        "(project_tag, type, principle, impact_score, tags) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            p.get("project_tag"),
                            p.get("type"),
                            p["principle"],
                            int(p.get("impact_score", 5)),
                            p.get("tags"),
                        ),
                    )
                    inserted_p += 1
                except Exception as e:
                    console.print(f"  [yellow]warning:[/] skipped principle: {e}")
            conn.commit()
            conn.close()
            console.print(
                f"[green]Pulled[/] {inserted_t} trace(s), {inserted_p} principle(s)"
            )
        else:
            console.print("[dim]Already up to date.[/]")

        fm_cfg.set_last_sync_ts(server_ts)


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
            status_str = (
                "[green]installed[/]" if path.exists() else "[dim]not installed[/]"
            )
            console.print(f"  {a}: {path} — {status_str}")
        return

    if action in ("generate", "update"):
        agents_to_process: list[str]
        if agent:
            if agent not in SKILL_PATHS:
                console.print(
                    f"[red]error:[/] unknown agent '{agent}'. Use claude|gemini|codex."
                )
                raise typer.Exit(1)
            agents_to_process = [agent]
        else:
            agents_to_process = list(SKILL_PATHS.keys())

        for a in agents_to_process:
            _generate_skill(a, dry_run=dry_run)
        return

    console.print(
        f"[red]error:[/] unknown action '{action}'. Use generate|update|list."
    )
    raise typer.Exit(1)


@app.command()
def help():
    """Show onboarding guide and command reference."""
    from forgememo.core import DB_PATH
    from forgememo import config as fm_cfg

    provider = fm_cfg.load().get("provider") or "[yellow]not set[/]"
    db_ok = (
        f"[green]{CHECK}[/]"
        if DB_PATH.exists()
        else f"[red]{CROSS} (run forgememo init)[/]"
    )

    _D = "-"  # use plain hyphen — avoids UnicodeEncodeError on Windows cp1252/ascii consoles
    console.print(
        Panel(
            "[bold]What is Forgemem?[/]\n"
            "Persistent cross-session memory for AI agents. Stores traces of your work,\n"
            "distills them into principles, and surfaces them via MCP so your AI remembers\n"
            "what you've built, decided, and learned - across every conversation.\n\n"
            "[bold]Setup (one-time):[/]\n"
            f"  [cyan]forgememo init[/]              {_D} initialize DB + register MCP + install agent skills\n"
            f"  [cyan]forgememo start[/]             {_D} launch the background MCP server\n"
            "  Restart Claude Code / your agent to activate the MCP connection\n\n"
            "[bold]Daily workflow:[/]\n"
            f"  [cyan]forgememo mine[/]              {_D} scan recent work and extract memories\n"
            f"  [cyan]forgememo distill[/]           {_D} condense traces into lasting principles\n"
            f'  [cyan]forgememo search "<query>"[/] {_D} search your memory bank\n'
            f'  [cyan]forgememo store "<text>"[/]   {_D} save a memory manually\n\n'
            f"  [cyan]forgememo export-context[/]   {_D} write agent context block (CLAUDE.md / AGENTS.md)\n\n"
            "[bold]Management:[/]\n"
            f"  [cyan]forgememo status[/]            {_D} DB stats, server health, skill status\n"
            f"  [cyan]forgememo config[/]            {_D} set/view inference provider & model\n"
            f"  [cyan]forgememo auth[/]              {_D} login / logout / check API key status\n"
            f"  [cyan]forgememo skill generate[/]    {_D} regenerate agent skill files\n"
            f"  [cyan]forgememo stop[/]              {_D} stop the background server\n\n"
            f"[bold]Current state:[/]  DB {db_ok}   Provider: {provider}",
            title="Forgemem - Help",
            expand=False,
        )
    )


@app.command("export-context")
def export_context(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project ID to scope results"),
    k: int = typer.Option(10, "--k", help="Max principles to include"),
    template: str = typer.Option(
        "claude",
        "--template",
        help="Template: claude | codex | generic",
    ),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to file"),
    template_file: Optional[Path] = typer.Option(None, "--template-file", help="Custom Jinja2 template path"),
):
    """Export a compact context block for agents."""
    from forgememo.storage import get_conn

    if template_file and not template_file.exists():
        console.print(f"[red]error:[/] template file not found: {template_file}")
        raise typer.Exit(1)

    if template not in {"claude", "codex", "generic"}:
        console.print("[red]error:[/] template must be one of: claude | codex | generic")
        raise typer.Exit(1)

    conn = get_conn()
    try:
        params = []
        where = ""
        if project:
            where = "WHERE project_id = ?"
            params.append(project)

        sql = (
            "SELECT id, ts, type, title, narrative, impact_score "
            "FROM distilled_summaries "
            f"{where} "
            "UNION ALL "
            "SELECT id, ts, type, title, narrative, impact_score "
            "FROM distilled_summaries_compat "
            f"{where} "
            "ORDER BY impact_score DESC, ts DESC LIMIT ?"
        )
        params = params + params + [k]
        rows = conn.execute(sql, params).fetchall()
        principles = [dict(r) for r in rows]

        sess_params = []
        sess_where = ""
        if project:
            sess_where = "WHERE project_id = ?"
            sess_params.append(project)
        last_session = conn.execute(
            f"SELECT * FROM session_summaries {sess_where} ORDER BY ts DESC LIMIT 1",
            sess_params,
        ).fetchone()
        last_session = dict(last_session) if last_session else None
    finally:
        conn.close()

    updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    project_label = project or "all"

    context_md = _format_context_markdown(project_label, updated, principles, last_session)

    if template_file:
        try:
            from jinja2 import Template
        except ImportError:
            console.print("[red]error:[/] jinja2 required for --template-file (pip install jinja2)")
            raise typer.Exit(1)
        tmpl = Template(template_file.read_text())
        body = tmpl.render(
            project=project_label,
            updated=updated,
            principles=principles,
            last_session=last_session,
        )
        block = body
        start = end = None
    else:
        if template == "claude":
            block = f"<forgememo-context>\n{context_md}\n</forgememo-context>\n"
            start = "<forgememo-context>"
            end = "</forgememo-context>"
        elif template == "codex":
            block = (
                "<!-- forgememo-context:start -->\n"
                f"{context_md}\n"
                "<!-- forgememo-context:end -->\n"
            )
            start = "<!-- forgememo-context:start -->"
            end = "<!-- forgememo-context:end -->"
        else:
            block = context_md + "\n"
            start = end = None

    if not output:
        if template == "claude":
            output = Path("CLAUDE.md")
        elif template == "codex":
            output = Path("AGENTS.md")

    if output:
        existing = output.read_text() if output.exists() else ""
        if start and end:
            updated_text = _replace_block(existing, start, end, block)
        else:
            updated_text = block if not existing else existing + "\n" + block
        output.write_text(updated_text)
        console.print(f"[green]{CHECK}[/] wrote {output}")
    else:
        console.print(block)


@app.command(hidden=True)
def mcp(http: bool = typer.Option(False)):
    """Run the MCP server (stdio only in v0.1)."""
    if http:
        console.print(
            "[yellow]warning:[/] HTTP mode not supported in v0.1. Running stdio.",
            err=True,
        )

    from forgememo import mcp_server

    mcp_server.mcp.run()


@app.command(hidden=True)
def daemon():
    """Run the daemon API server (socket-first)."""
    from forgememo import daemon as _daemon

    _daemon.main()


@app.command(hidden=True)
def worker():
    """Run the background distillation worker."""
    from forgememo import worker as _worker

    _worker.main()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    app()


if __name__ == "__main__":
    main()
