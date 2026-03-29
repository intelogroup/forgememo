#!/usr/bin/env python3
"""Forgemem CLI — flat ruff-style commands."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forgemem import __version__

# ---------------------------------------------------------------------------
# App + console
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="forgemem",
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
        console.print(f"forgemem {__version__}")
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
    cache_file = Path.home() / ".forgemem" / ".update_check"
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
                        f"[yellow]Update available:[/] forgemem {latest} "
                        f"(you have {__version__}) — run [bold]pip install -U forgemem[/]"
                    )
                    return
                else:
                    return  # empty/malformed cache — fall through to fresh check
        import requests as _requests
        resp = _requests.get("https://pypi.org/pypi/forgemem/json", timeout=2)
        data = resp.json()
        latest = data["info"]["version"]
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(latest)
        if _ver(latest) > _ver(__version__):
            console.print(
                f"[yellow]Update available:[/] forgemem {latest} "
                f"(you have {__version__}) — run [bold]pip install -U forgemem[/]"
            )
    except Exception:
        pass  # Never block the CLI for an update check


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Annotated[Optional[bool], typer.Option(
        "--version", "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    )] = None,
) -> None:
    from forgemem.core import DB_PATH
    _SKIP_AUTO_INIT = {"init", "mcp", "help", None}
    if ctx.invoked_subcommand not in _SKIP_AUTO_INIT and not DB_PATH.exists():
        console.print("[dim]First run detected — initializing forgemem...[/]")
        init(yes=True)
    if ctx.invoked_subcommand not in {"mcp"}:
        import atexit
        atexit.register(_check_for_update)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgemem.server.plist"
PLIST_LABEL = "com.forgemem.server"
MINER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.forgemem.miner.plist"
MINER_PLIST_LABEL = "com.forgemem.miner"
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
# Helpers (provider setup)
# ---------------------------------------------------------------------------


def _prompt_provider_setup(yes: bool) -> None:
    """If no provider is configured, require interactive provider selection."""
    from forgemem import config as fm_cfg

    if fm_cfg.load().get("provider") is not None:
        return  # already configured (e.g. Ollama was just set above)

    # Non-interactive mode: warn visibly and skip
    if yes or not sys.stdin.isatty():
        console.print(Panel(
            "[bold yellow]Interactive provider setup is required on first run.[/]\n\n"
            "Re-run [cyan]forgemem init[/] in a real terminal to choose your provider.\n"
            "Agents and non-TTY sessions cannot bypass this step.\n\n"
            "If you already configured a provider earlier, run:\n"
            "  [cyan]forgemem start[/]",
            title="[bold red]INTERACTIVE SETUP REQUIRED[/]",
            border_style="red",
            expand=False,
        ))
        raise typer.Exit(code=1)

    # Interactive menu
    import questionary  # lazy: only needed for the interactive provider picker
    _choices = [
        questionary.Choice("forgemem   (managed — no key needed, sign in once)", value="forgemem"),
        questionary.Choice("anthropic  (BYOK — needs ANTHROPIC_API_KEY)", value="anthropic"),
        questionary.Choice("openai     (BYOK — needs OPENAI_API_KEY)", value="openai"),
        questionary.Choice("gemini     (BYOK — needs GEMINI_API_KEY)", value="gemini"),
        questionary.Choice("ollama     (local, free, private — needs ollama running)", value="ollama"),
        questionary.Choice("skip for now  (configure later with forgemem config)", value=None),
    ]
    provider = questionary.select(
        "Choose an inference provider for memory distillation:",
        choices=_choices,
        default=_choices[-1],
    ).ask()

    if not provider:
        console.print("[dim]Skipped — run [cyan]forgemem config[/] to set a provider later.[/]")
        return

    if provider == "forgemem":
        fm_cfg.set_provider("forgemem")
        console.print("[green]Provider set to forgemem.[/] Let's authenticate now...")
        _do_auth_login()
        return

    if provider == "ollama":
        fm_cfg.set_provider("ollama")
        console.print("[green]Provider set to ollama.[/] Make sure it's running: [cyan]ollama serve[/]")
        return

    key = typer.prompt(
        f"Enter your {provider} API key (press Enter to skip)",
        default="",
        hide_input=True,
    )
    fm_cfg.set_provider(provider, api_key=key or None)
    if key:
        console.print(f"[green]Provider set to {provider}[/] — API key stored in {fm_cfg.CONFIG_PATH}")
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

    # Provider setup — runs only if still unconfigured (Ollama declined or not detected)
    from forgemem import config as fm_cfg
    _prompt_provider_setup(yes)

    provider_configured = fm_cfg.load().get("provider") is not None

    if not provider_configured:
        console.print(Panel(
            "[bold green]Forgemem initialized successfully![/]\n\n"
            "[bold red]⚠  REQUIRED BEFORE USE — configure a provider now:[/]\n"
            "  [cyan]forgemem config anthropic --key sk-ant-...[/]\n"
            "  [cyan]forgemem config openai    --key sk-...[/]\n"
            "  [cyan]forgemem config gemini    --key AIza...[/]\n"
            "  [cyan]forgemem config ollama[/]               [dim](local, free)[/]\n"
            "  [cyan]forgemem config forgemem[/]             [dim](managed, no key needed)[/]\n\n"
            "After configuring a provider, run:\n"
            "  [cyan]forgemem start[/]  →  restart your agent  →  [cyan]forgemem status[/]",
            title="[bold red]ACTION REQUIRED — provider not set[/]",
            border_style="red",
            expand=False,
        ))
        raise typer.Exit(code=1)
    else:
        console.print(Panel(
            "[bold green]Forgemem initialized successfully![/]\n\n"
            "[bold]Next steps:[/]\n"
            "  1. [cyan]forgemem start[/]          — launch the MCP server (background daemon)\n"
            "  2. Restart Claude Code / your AI agent to pick up the MCP connection\n"
            "  3. [cyan]forgemem status[/]         — verify everything is running\n\n"
            "[bold]Key commands:[/]\n"
            "  [cyan]forgemem store \"<text>\"[/]   — save a memory manually\n"
            "  [cyan]forgemem search \"<query>\"[/] — search stored memories\n"
            "  [cyan]forgemem mine[/]              — scan recent work and extract memories\n"
            "  [cyan]forgemem distill[/]           — condense traces into lasting principles\n"
            "  [cyan]forgemem config[/]            — set inference provider (anthropic/ollama/…)\n\n"
            "Run [cyan]forgemem help[/] at any time to see this again.",
            title="Forgemem Ready",
            expand=False,
        ))
        console.print("\n[dim]Auto-starting MCP server…[/]")
        _do_start()


def _do_start(
    schedule: Optional[str] = None,
    mine: bool = False,
    mine_interval: int = 3600,
) -> None:
    """Core start logic — install and load LaunchAgent(s). Called by start() and init()."""
    if sys.platform == "linux":
        forgemem_bin = shutil.which("forgemem") or "forgemem"
        console.print("[bold]Linux detected.[/] To run forgemem as a systemd user service, create:")
        console.print("\n  [dim]~/.config/systemd/user/forgemem.service[/]\n")
        console.print(
            f"[dim]  [Unit]\n"
            f"  Description=Forgemem MCP server\n"
            f"  After=default.target\n\n"
            f"  [Service]\n"
            f"  ExecStart={forgemem_bin} mcp\n"
            f"  Restart=on-failure\n\n"
            f"  [Install]\n"
            f"  WantedBy=default.target[/]\n"
        )
        console.print("Then enable it with:")
        console.print("  [cyan]systemctl --user daemon-reload[/]")
        console.print("  [cyan]systemctl --user enable --now forgemem[/]")
        console.print("\nOr just run directly: [cyan]forgemem mcp[/]")
        raise typer.Exit(0)

    if sys.platform == "win32":
        forgemem_bin = shutil.which("forgemem") or "forgemem"
        console.print("[bold]Windows detected.[/] To run forgemem at login via Task Scheduler, run:")
        console.print(
            f"\n  [cyan]schtasks /create /tn \"Forgemem MCP\" /tr \"{forgemem_bin} mcp\" "
            f"/sc ONLOGON /f[/]\n"
        )
        console.print("Or just run directly in a terminal: [cyan]forgemem mcp[/]")
        raise typer.Exit(0)

    if sys.platform != "darwin":
        console.print(f"[yellow]Unsupported platform '{sys.platform}'.[/] Run [cyan]forgemem mcp[/] directly.")
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

    if mine:
        miner_plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{MINER_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{forgemem_bin}</string>
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
            console.print(f"[yellow]launchctl load (miner) returned {miner_result.returncode}:[/] {miner_result.stderr.strip()}")


@app.command()
def start(
    schedule: Annotated[
        Optional[str],
        typer.Option(help="login|hourly|manual"),
    ] = None,
    mine: Annotated[bool, typer.Option("--mine/--no-mine", help="Also install a mining LaunchAgent (macOS only).")] = False,
    mine_interval: Annotated[int, typer.Option(help="Mining interval in seconds (default: 3600).")] = 3600,
):
    """Start the MCP server. On macOS: installs a LaunchAgent plist."""
    from forgemem import config as fm_cfg
    if fm_cfg.load().get("provider") is None:
        console.print(Panel(
            "[bold red]No inference provider configured.[/]\n\n"
            "Run one of these first, then retry [cyan]forgemem start[/]:\n"
            "  [cyan]forgemem config anthropic --key sk-ant-...[/]\n"
            "  [cyan]forgemem config openai    --key sk-...[/]\n"
            "  [cyan]forgemem config gemini    --key AIza...[/]\n"
            "  [cyan]forgemem config ollama[/]               [dim](local, free)[/]\n"
            "  [cyan]forgemem config forgemem[/]             [dim](managed, no key needed)[/]",
            title="[bold red]ACTION REQUIRED — configure provider first[/]",
            border_style="red",
            expand=False,
        ))
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
        console.print(f"[yellow]launchctl unload returned {result.returncode}:[/] {result.stderr.strip()}")

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
            console.print(f"[yellow]launchctl unload (miner) returned {miner_result.returncode}:[/] {miner_result.stderr.strip()}")

    remove = typer.confirm("Remove plist file(s)?", default=False)
    if remove:
        PLIST_PATH.unlink(missing_ok=True)
        console.print(f"[dim]removed[/] {PLIST_PATH}")
        if MINER_PLIST_PATH.exists():
            MINER_PLIST_PATH.unlink(missing_ok=True)
            console.print(f"[dim]removed[/] {MINER_PLIST_PATH}")


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON for scripting"),
):
    """Show DB stats, server health, and skill status."""
    from forgemem.core import DB_PATH, get_conn
    from forgemem import config as fm_cfg
    from forgemem.config import detect_ollama

    # DB stats
    if not DB_PATH.exists():
        if json_output:
            console.print('{"error": "DB not found — run forgemem init"}')
        else:
            console.print(f"[red]DB not found:[/] {DB_PATH}  (run [cyan]forgemem init[/])")
        raise typer.Exit(1)

    conn = get_conn()
    t_total     = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    p_total     = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
    undistilled = conn.execute("SELECT COUNT(*) FROM traces WHERE distilled=0").fetchone()[0]
    conn.close()

    provider = fm_cfg.get_provider() or "not set"

    # MCP registration
    settings_path = Path.home() / ".claude" / "settings.json"
    mcp_registered = False
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            mcp_registered = "forgemem" in data.get("mcpServers", {})
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
        f"[yellow]{undistilled}[/]  → run: [cyan]forgemem distill[/]"
        if undistilled else str(undistilled)
    )
    table.add_row("Undistilled", undistilled_val)
    table.add_row("Provider", f"[green]{provider}[/]" if provider != "not set" else "[yellow]not set[/]  → run: [cyan]forgemem config[/]")
    table.add_row("MCP", f"[green]{CHECK} registered[/]" if mcp_registered else f"[yellow]{CROSS} not registered[/]  → run: [cyan]forgemem init[/]")

    skills_str = "  ".join(
        f"[green]{a} {CHECK}[/]" if ok else f"[dim]{a} {CROSS}[/]"
        for a, ok in skills_status.items()
    )
    table.add_row("Skills", skills_str)

    # LaunchAgent / daemon status
    if sys.platform == "darwin":
        daemon_val = f"[green]{CHECK} plist installed[/]" if PLIST_PATH.exists() else f"[dim]{CROSS} not installed[/]  → run: [cyan]forgemem start[/]"
        table.add_row("Daemon", daemon_val)

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
                console.print("  [yellow]No models pulled.[/] Run: ollama pull llama3.2")
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
    with console.status("[dim]Scanning repos and memory files...[/]"):
        scanner.main()


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
    with console.status("[dim]Distilling traces into principles...[/]"):
        cmd_distill(args)


@app.command()
def config(
    provider: Optional[str] = typer.Argument(None, help="Provider: anthropic | openai | gemini | ollama | forgemem"),
    key: Optional[str] = typer.Option(None, "--key", "-k", help="API key for the provider (stored locally)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override default model for this provider"),
    ollama_url: Optional[str] = typer.Option(None, "--ollama-url", help="Ollama base URL (default: http://localhost:11434)"),
    show: bool = typer.Option(False, "--show", help="Print current config (masks keys)"),
):
    """Configure AI provider and API keys.

    Examples:\n
      forgemem config                                    # show current config\n
      forgemem config anthropic --key sk-ant-...         # set provider + key\n
      forgemem config openai --key sk-...                # switch to OpenAI\n
      forgemem config gemini --key AIza...               # switch to Gemini\n
      forgemem config ollama                             # use local Ollama (free, private)\n
      forgemem config ollama --model llama3.2            # use specific Ollama model\n
      forgemem config ollama --ollama-url http://host:11434  # remote Ollama\n
      forgemem config forgemem                           # use Forgemem starter inference\n
    """
    from forgemem import config as fm_cfg

    if provider is None or show:
        current = fm_cfg.load()
        active = current.get("provider", "anthropic")
        keys = current.get("api_keys", {})
        masked = {p: v[:8] + "..." + v[-4:] if len(v) > 12 else "***" for p, v in keys.items()}

        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column("key", style="bold", min_width=12)
        tbl.add_column("value")

        tbl.add_row("Provider", f"[green]{active}[/]")
        tbl.add_row("Model", current.get("model") or fm_cfg.DEFAULT_MODELS.get(active, "default"))
        tbl.add_row("Config", str(fm_cfg.CONFIG_PATH))
        tbl.add_row("Keys", str(masked) if masked else "[dim](none stored - using env vars)[/]")
        if active == "ollama":
            tbl.add_row("Ollama URL", fm_cfg.get_ollama_url())
        console.print(Panel(tbl, title="Forgemem Config", expand=False))
        return

    if provider not in fm_cfg.SUPPORTED_PROVIDERS:
        console.print(f"[red]Unknown provider '{provider}'.[/] Choose: {', '.join(fm_cfg.SUPPORTED_PROVIDERS)}")
        raise typer.Exit(1)

    fm_cfg.set_provider(provider, api_key=key)

    if provider == "ollama":
        from forgemem.config import detect_ollama
        ollama = detect_ollama()
        if ollama:
            console.print(f"[cyan]Ollama detected[/] at {ollama['url']}")
            if ollama["models"]:
                console.print("  Available models: " + ", ".join(ollama["models"][:8]))
                if not model:
                    model = ollama["models"][0]
                    console.print(f"[green]Auto-selected model:[/] {model}")
            else:
                console.print("  [yellow]No models pulled yet.[/] Run: ollama pull llama3.2")
        else:
            console.print("[yellow]Ollama not detected[/] at default port. Start it with: ollama serve")

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
        msg += "\n[green]Inference runs locally — your traces never leave your machine.[/]"
    elif key:
        msg += f"\n[green]API key stored[/] in {fm_cfg.CONFIG_PATH}"
    else:
        msg += "\n[dim]No key stored — will fall back to env var[/]"
    if provider == "forgemem":
        msg += "\n[yellow]Managed inference coming in v0.3 — for now use BYOK.[/]"
    console.print(msg)


def _do_auth_login() -> bool:
    """Run the browser-based OAuth login flow. Returns True on success, exits on failure."""
    import webbrowser
    import http.server
    import threading
    import secrets
    import urllib.parse
    from forgemem import config as fm_cfg

    port = 47474
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

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)

    def _serve():
        server.handle_request()  # handle one request then stop

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    login_url = (
        f"https://app.forgemem.com/cli-auth"
        f"?callback=http://127.0.0.1:{port}/callback"
        f"&state={state}"
    )
    console.print(f"Opening browser to authenticate...\n{login_url}")
    webbrowser.open(login_url)
    console.print("[dim]Waiting for browser callback (Ctrl+C to cancel)...[/]")

    t.join(timeout=120)

    if received_token.get("value"):
        cfg_data = fm_cfg.load()
        cfg_data["forgemem_token"] = received_token["value"]
        cfg_data["provider"] = "forgemem"
        fm_cfg.save(cfg_data)
        console.print("[green]Authenticated![/] Provider set to Forgemem Inference.")
        console.print("[dim]Your $5 free credits are ready.[/]")
        return True
    else:
        console.print("[red]Login timed out or was cancelled.[/]")
        raise typer.Exit(1)


@app.command()
def auth(
    action: str = typer.Argument("status", help="login | logout | status"),
):
    """Authenticate with Forgemem for managed inference.

    Examples:\n
      forgemem auth login    # open browser, store token\n
      forgemem auth status   # show current auth state\n
      forgemem auth logout   # remove stored token\n
    """
    from forgemem import config as fm_cfg

    if action == "status":
        token = fm_cfg.load().get("forgemem_token")
        if token:
            console.print("[green]Authenticated[/] with Forgemem Inference")
            console.print(f"[dim]Token: {token[:8]}...{token[-4:]}[/]")
            console.print("Run [bold]forgemem config[/] to see full provider state.")
        else:
            console.print("[yellow]Not authenticated.[/] Run: forgemem auth login")
        return

    if action == "logout":
        cfg_data = fm_cfg.load()
        if "forgemem_token" in cfg_data:
            del cfg_data["forgemem_token"]
            fm_cfg.save(cfg_data)
            console.print("[green]Logged out.[/] Token removed.")
        else:
            console.print("[dim]Not logged in.[/]")
        return

    if action == "login":
        _do_auth_login()
        return

    console.print(f"[red]Unknown action '{action}'.[/] Use: login | logout | status")
    raise typer.Exit(1)


@app.command()
def sync(
    push_only: bool = typer.Option(False, "--push-only", help="Push local changes only"),
    pull_only: bool = typer.Option(False, "--pull-only", help="Pull remote changes only"),
):
    """Sync local memory with Forgemem cloud (requires Sync subscription).

    Pushes new local traces + principles to the cloud, then pulls changes
    from your other devices since the last sync. Safe to run repeatedly — all
    operations are idempotent.

    Examples:\n
      forgemem sync              # push + pull\n
      forgemem sync --push-only  # push local changes only\n
      forgemem sync --pull-only  # pull remote changes only (e.g. from background task)\n
    """
    import sqlite3
    import requests as req
    from datetime import datetime, timezone
    from forgemem import config as fm_cfg
    from forgemem.core import DB_PATH

    token = fm_cfg.load().get("forgemem_token")
    if not token:
        console.print("[yellow]Not authenticated.[/] Run: forgemem auth login")
        raise typer.Exit(1)

    managed_url = os.environ.get("FORGEMEM_API_URL", "https://api.forgemem.com")
    device_id   = fm_cfg.get_device_id()
    last_sync   = fm_cfg.get_last_sync_ts()
    headers     = {"Authorization": f"Bearer {token}"}

    # ── Push ──────────────────────────────────────────────────────────────────
    if not pull_only:
        if not DB_PATH.exists():
            console.print("[yellow]No local DB found.[/] Run: forgemem init")
            raise typer.Exit(1)

        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row

        traces = [
            dict(r) for r in conn.execute(
                "SELECT id as local_id, ts, session_id, project_tag, type, content, distilled "
                "FROM traces WHERE ts > ?", (last_sync,)
            ).fetchall()
        ]
        principles = [
            dict(r) for r in conn.execute(
                "SELECT id as local_id, source_trace_id as source_local_id, project_tag, "
                "type, principle, impact_score, tags FROM principles WHERE ts > ?", (last_sync,)
            ).fetchall()
        ]
        conn.close()

        if traces or principles:
            try:
                resp = req.post(
                    f"{managed_url}/v1/sync/push",
                    json={
                        "device_id":   device_id,
                        "device_name": os.uname().nodename if hasattr(os, "uname") else "",
                        "traces":      traces,
                        "principles":  principles,
                    },
                    headers=headers,
                    timeout=30,
                )
                if resp.status_code == 402:
                    console.print(
                        "[yellow]Sync requires a Sync subscription.[/] "
                        "Upgrade at: https://app.forgemem.com/billing"
                    )
                    raise typer.Exit(1)
                resp.raise_for_status()
                data = resp.json()
                console.print(
                    f"[green]Pushed[/] {data.get('pushed_traces', 0)} trace(s), "
                    f"{data.get('pushed_principles', 0)} principle(s)"
                )
            except req.exceptions.ConnectionError:
                console.print("[red]Could not reach api.forgemem.com.[/] Check your connection.")
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
            if resp.status_code == 402:
                console.print(
                    "[yellow]Sync requires a Sync subscription.[/] "
                    "Upgrade at: https://app.forgemem.com/billing"
                )
                raise typer.Exit(1)
            resp.raise_for_status()
            data = resp.json()
        except req.exceptions.ConnectionError:
            console.print("[red]Could not reach api.forgemem.com.[/] Check your connection.")
            raise typer.Exit(1)

        remote_traces     = data.get("traces", [])
        remote_principles = data.get("principles", [])
        server_ts         = data.get("server_ts", datetime.now(timezone.utc).isoformat())

        if remote_traces or remote_principles:
            if not DB_PATH.exists():
                console.print("[yellow]No local DB found.[/] Run: forgemem init")
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
                        (t.get("session_id"), t.get("project_tag"),
                         t.get("type", "note"), t["content"], int(t.get("distilled", False))),
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
                        (p.get("project_tag"), p.get("type"),
                         p["principle"], int(p.get("impact_score", 5)), p.get("tags")),
                    )
                    inserted_p += 1
                except Exception as e:
                    console.print(f"  [yellow]warning:[/] skipped principle: {e}")
            conn.commit()
            conn.close()
            console.print(f"[green]Pulled[/] {inserted_t} trace(s), {inserted_p} principle(s)")
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


@app.command()
def help():
    """Show onboarding guide and command reference."""
    from forgemem.core import DB_PATH
    from forgemem import config as fm_cfg

    provider = fm_cfg.load().get("provider") or "[yellow]not set[/]"
    db_ok = f"[green]{CHECK}[/]" if DB_PATH.exists() else f"[red]{CROSS} (run forgemem init)[/]"

    _D = "-"  # use plain hyphen — avoids UnicodeEncodeError on Windows cp1252/ascii consoles
    console.print(Panel(
        "[bold]What is Forgemem?[/]\n"
        "Persistent cross-session memory for AI agents. Stores traces of your work,\n"
        "distills them into principles, and surfaces them via MCP so your AI remembers\n"
        "what you've built, decided, and learned - across every conversation.\n\n"

        "[bold]Setup (one-time):[/]\n"
        f"  [cyan]forgemem init[/]              {_D} initialize DB + register MCP + install agent skills\n"
        f"  [cyan]forgemem start[/]             {_D} launch the background MCP server\n"
        "  Restart Claude Code / your agent to activate the MCP connection\n\n"

        "[bold]Daily workflow:[/]\n"
        f"  [cyan]forgemem mine[/]              {_D} scan recent work and extract memories\n"
        f"  [cyan]forgemem distill[/]           {_D} condense traces into lasting principles\n"
        f"  [cyan]forgemem search \"<query>\"[/] {_D} search your memory bank\n"
        f"  [cyan]forgemem store \"<text>\"[/]   {_D} save a memory manually\n\n"

        "[bold]Management:[/]\n"
        f"  [cyan]forgemem status[/]            {_D} DB stats, server health, skill status\n"
        f"  [cyan]forgemem config[/]            {_D} set/view inference provider & model\n"
        f"  [cyan]forgemem auth[/]              {_D} login / logout / check API key status\n"
        f"  [cyan]forgemem skill generate[/]    {_D} regenerate agent skill files\n"
        f"  [cyan]forgemem stop[/]              {_D} stop the background server\n\n"

        f"[bold]Current state:[/]  DB {db_ok}   Provider: {provider}",
        title="Forgemem - Help",
        expand=False,
    ))


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
