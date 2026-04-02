"""Lifecycle commands: init, start, stop, status, doctor."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from forgememo.port import read_port
from forgememo.commands._shared import (
    CHECK,
    CROSS,
    LOG_PATH,
    MINER_PLIST_PATH,
    MINER_PLIST_LABEL,
    PLIST_LABEL,
    PLIST_PATH,
    SKILL_PATHS,
    SKILL_TEMPLATES_DIR,
    WORKER_PLIST_LABEL,
    WORKER_PLIST_PATH,
    _auto_detect_and_generate_skills,
    _register_hooks,
    _register_mcp,
    console,
)


# ---------------------------------------------------------------------------
# Provider setup helpers (used by init)
# ---------------------------------------------------------------------------


def _configure_provider_noninteractive(provider: str) -> None:
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


def _prompt_provider_setup(yes: bool, force: bool = False) -> None:
    from forgememo import config as fm_cfg
    from forgememo.commands.configure import _do_auth_login

    if fm_cfg.load().get("provider") is not None and not force:
        return

    if yes or not sys.stdin.isatty():
        console.print(
            "Provider picker requires a real terminal.\n"
            "Ask the user to run:  [cyan]forgememo config -i[/]"
        )
        return

    import questionary

    _choices = [
        questionary.Choice(
            "forgememo   (recommended — works with any AI tool, sign in once, no key)",
            value="forgememo",
        ),
        questionary.Choice(
            "claude_code (Claude subscription via `claude` CLI — no API key needed)",
            value="claude_code",
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
# _do_start — core start logic (called by start() and init())
# ---------------------------------------------------------------------------


def _do_start(
    schedule: Optional[str] = None,
    mine: bool = False,
    mine_interval: int = 3600,
) -> None:
    if sys.platform == "linux":
        forgememo_bin = shutil.which("forgememo") or "forgememo"
        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True, exist_ok=True)

        daemon_unit = systemd_dir / "forgememo-daemon.service"
        daemon_unit.write_text(
            f"[Unit]\nDescription=Forgememo Daemon\nAfter=default.target\n\n"
            f"[Service]\nExecStart={forgememo_bin} daemon\nRestart=on-failure\n\n"
            f"[Install]\nWantedBy=default.target\n"
        )
        worker_unit = systemd_dir / "forgememo-worker.service"
        worker_unit.write_text(
            f"[Unit]\nDescription=Forgememo Worker\nAfter=default.target\n\n"
            f"[Service]\nExecStart={forgememo_bin} worker\nRestart=on-failure\n\n"
            f"[Install]\nWantedBy=default.target\n"
        )
        console.print(f"[green]wrote[/] {daemon_unit}")
        console.print(f"[green]wrote[/] {worker_unit}")

        reload_r = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=False, capture_output=True, text=True,
        )
        if reload_r.returncode != 0:
            console.print(
                f"[yellow]systemctl daemon-reload failed:[/] {reload_r.stderr.strip()}\n"
                "Run manually: [cyan]systemctl --user daemon-reload && "
                "systemctl --user enable --now forgememo-daemon forgememo-worker[/]"
            )
            raise typer.Exit(1)

        enable_r = subprocess.run(
            ["systemctl", "--user", "enable", "--now", "forgememo-daemon", "forgememo-worker"],
            check=False, capture_output=True, text=True,
        )
        if enable_r.returncode == 0:
            console.print("[green]forgememo-daemon and forgememo-worker enabled and started.[/]")
        else:
            console.print(
                f"[yellow]systemctl enable --now failed:[/] {enable_r.stderr.strip()}\n"
                "Start manually: [cyan]forgememo daemon[/]"
            )
        raise typer.Exit(0)

    if sys.platform == "win32":
        forgememo_bin = shutil.which("forgememo") or "forgememo"
        http_port = str(read_port())
        task_cmd = f'cmd /c "set FORGEMEMO_HTTP_PORT={http_port} && \\"{forgememo_bin}\\" daemon"'
        worker_cmd = f'cmd /c "set FORGEMEMO_HTTP_PORT={http_port} && \\"{forgememo_bin}\\" worker"'
        for tn, tr in [("Forgememo Daemon", task_cmd), ("Forgememo Worker", worker_cmd)]:
            r = subprocess.run(
                ["schtasks", "/create", "/tn", tn, "/tr", tr, "/sc", "ONLOGON", "/f"],
                capture_output=True, text=True, check=False,
            )
            if r.returncode == 0:
                console.print(f"[green]Task Scheduler task created:[/] {tn}")
            else:
                console.print(f"[yellow]schtasks failed for '{tn}':[/] {r.stderr.strip()}")
        env = os.environ.copy()
        env["FORGEMEMO_HTTP_PORT"] = http_port
        subprocess.Popen(
            [forgememo_bin, "daemon"],
            env=env,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        import time as _time
        import urllib.request as _urllib
        for _i in range(10):
            try:
                _urllib.urlopen(f"http://127.0.0.1:{http_port}/health", timeout=1)
                console.print(f"[green]Daemon started and healthy[/] on http://127.0.0.1:{http_port}")
                break
            except Exception:
                _time.sleep(1)
        else:
            console.print("[yellow]Daemon spawned but health check timed out[/] — check logs at %TEMP%\\forgememo_daemon.log")
        raise typer.Exit(0)

    if sys.platform != "darwin":
        console.print(
            f"[yellow]Unsupported platform '{sys.platform}'.[/] "
            "Run [cyan]forgememo daemon[/] directly."
        )
        raise typer.Exit(0)

    forgememo_bin = shutil.which("forgememo")
    if not forgememo_bin:
        console.print(
            "[red]error:[/] 'forgememo' binary not found in PATH. Install the package first."
        )
        raise typer.Exit(1)

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
        ["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True
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
        ["launchctl", "load", str(WORKER_PLIST_PATH)], capture_output=True, text=True
    )
    if worker_result.returncode == 0:
        console.print("[green]worker loaded.[/]")
    else:
        console.print(
            f"[yellow]launchctl load (worker) returned {worker_result.returncode}:[/] "
            f"{worker_result.stderr.strip()}"
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
            ["launchctl", "load", str(MINER_PLIST_PATH)], capture_output=True, text=True
        )
        if miner_result.returncode == 0:
            console.print(f"[green]mining agent loaded[/] (interval: {mine_interval}s)")
        else:
            console.print(
                f"[yellow]launchctl load (miner) returned {miner_result.returncode}:[/] "
                f"{miner_result.stderr.strip()}"
            )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def init(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts"),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Provider: anthropic | openai | gemini | ollama | forgememo",
    ),
):
    """Initialize DB, register MCP server, and detect agent skills."""
    from rich.panel import Panel

    if sys.version_info < (3, 9):
        console.print("[red]error:[/] Python 3.9+ required")
        raise typer.Exit(1)

    from forgememo.storage import init_db, DB_PATH

    init_db()
    console.print(f"[green]DB initialized:[/] {DB_PATH}")

    settings_path = Path.home() / ".claude" / "settings.json"
    registered = _register_mcp(settings_path)
    if registered:
        console.print(f"[green]MCP registered[/] in {settings_path}")
    else:
        console.print("[dim]MCP already registered[/]")

    hooks_registered = _register_hooks(settings_path)
    if hooks_registered:
        console.print(f"[green]Hooks registered[/] in {settings_path}")
    else:
        console.print("[dim]Hooks already registered[/]")

    _auto_detect_and_generate_skills(yes)

    from forgememo import config as fm_cfg

    if provider:
        _configure_provider_noninteractive(provider)
        console.print(f"[green]Provider set to {provider} via --provider flag.[/]")
    else:
        _prompt_provider_setup(yes)

    provider_configured = fm_cfg.load().get("provider") is not None

    if not provider_configured:
        console.print(
            Panel(
                "[bold green]Forgememo initialized successfully![/]\n\n"
                "[bold red]\u26a0  REQUIRED BEFORE USE \u2014 configure a provider now:[/]\n"
                "  [cyan]forgememo config anthropic --key sk-ant-...[/]\n"
                "  [cyan]forgememo config openai    --key sk-...[/]\n"
                "  [cyan]forgememo config gemini    --key AIza...[/]\n"
                "  [cyan]forgememo config ollama[/]               [dim](local, free)[/]\n"
                "  [cyan]forgememo config forgememo[/]             [dim](managed, no key needed)[/]\n\n"
                "After configuring a provider, run:\n"
                "  [cyan]forgememo start[/]  \u2192  restart your agent  \u2192  [cyan]forgememo status[/]",
                title="[bold red]ACTION REQUIRED \u2014 provider not set[/]",
                border_style="red",
                expand=False,
            )
        )
        raise typer.Exit(code=1)
    else:
        console.print(
            Panel(
                "[bold green]Forgememo initialized successfully![/]\n\n"
                "[bold]Next steps:[/]\n"
                "  1. [cyan]forgememo start[/]          \u2014 launch the daemon + worker\n"
                "  2. Restart Claude Code / your AI agent to pick up the MCP connection\n"
                "  3. [cyan]forgememo status[/]         \u2014 verify everything is running\n\n"
                "[bold]Key commands:[/]\n"
                '  [cyan]forgememo store "<text>"[/]   \u2014 save a memory manually\n'
                '  [cyan]forgememo search "<query>"[/] \u2014 search stored memories\n'
                "  [cyan]forgememo mine[/]              \u2014 scan recent work and extract memories\n"
                "  [cyan]forgememo distill[/]           \u2014 condense traces into lasting principles\n"
                "  [cyan]forgememo config[/]            \u2014 set inference provider (anthropic/ollama/\u2026)\n\n"
                "Run [cyan]forgememo help[/] at any time to see this again.",
                title="Forgememo Ready",
                expand=False,
            )
        )
        console.print("\n[dim]Auto-starting MCP server\u2026[/]")
        _do_start()


def start(
    schedule: Annotated[Optional[str], typer.Option(help="login|hourly|manual")] = None,
    mine: Annotated[
        bool, typer.Option("--mine/--no-mine", help="Also install a mining LaunchAgent (macOS only).")
    ] = False,
    mine_interval: Annotated[
        int, typer.Option(help="Mining interval in seconds (default: 3600).")
    ] = 3600,
):
    """Start the MCP server. On macOS: installs a LaunchAgent plist."""
    from forgememo import config as fm_cfg

    if fm_cfg.load().get("provider") is None:
        console.print("[dim]No provider configured \u2014 running first-time setup\u2026[/]")
        init(yes=False, provider=None)
        return
    _do_start(schedule=schedule, mine=mine, mine_interval=mine_interval)


def stop():
    """Unload the LaunchAgent and optionally remove the plist."""
    if sys.platform == "linux":
        def _run_systemctl(*args):
            try:
                return subprocess.run(
                    ["systemctl", "--user", *args],
                    check=False, capture_output=True, text=True, timeout=10,
                )
            except subprocess.TimeoutExpired:
                return None

        _stop_r = _run_systemctl("stop", "forgememo-daemon", "forgememo-worker")
        _dis_r = _run_systemctl("disable", "forgememo-daemon", "forgememo-worker")

        if _stop_r is None:
            console.print("[yellow]systemctl stop timed out \u2014 killing process directly[/]")
            subprocess.run(["pkill", "-f", "forgememo.*daemon"], check=False)
        elif _stop_r.returncode == 0:
            console.print("[green]forgememo-daemon and forgememo-worker stopped.[/]")
        else:
            subprocess.run(["pkill", "-f", "forgememo.*daemon"], check=False)
            console.print(f"[yellow]stop failed:[/] {_stop_r.stderr.strip()}")

        if _dis_r is not None and _dis_r.returncode == 0:
            console.print("[green]services disabled.[/]")

        systemd_dir = Path.home() / ".config" / "systemd" / "user"
        remove = typer.confirm("Remove unit files?", default=False) if sys.stdin.isatty() else False
        if remove:
            for name in ("forgememo-daemon.service", "forgememo-worker.service"):
                p = systemd_dir / name
                if p.exists():
                    p.unlink()
                    console.print(f"[dim]removed[/] {p}")
            _run_systemctl("daemon-reload")
        raise typer.Exit(0)

    if sys.platform == "win32":
        import socket as _sock

        http_port = str(read_port())
        try:
            with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as _s:
                _s.settimeout(1)
                alive = _s.connect_ex(("127.0.0.1", int(http_port))) == 0
        except Exception:
            alive = False
        if alive:
            result = subprocess.run(
                ["taskkill", "/f", "/im", "forgememo.exe"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                console.print("[green]Forgememo processes stopped.[/]")
            else:
                console.print(f"[yellow]Could not stop processes:[/] {result.stderr.strip()}")
        else:
            console.print("[dim]Daemon not running.[/]")
        for tn in ("Forgememo Daemon", "Forgememo Worker"):
            subprocess.run(
                ["schtasks", "/delete", "/tn", tn, "/f"],
                capture_output=True, text=True, check=False,
            )
        raise typer.Exit(0)

    if sys.platform != "darwin":
        console.print(
            f"[yellow]Unsupported platform '{sys.platform}'.[/] Kill the daemon process manually."
        )
        raise typer.Exit(0)

    if not PLIST_PATH.exists():
        console.print(f"[yellow]plist not found:[/] {PLIST_PATH}")
        raise typer.Exit(0)

    result = subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)], check=False, capture_output=True, text=True
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
            check=False, capture_output=True, text=True,
        )
        if miner_result.returncode == 0:
            console.print("[green]mining agent unloaded.[/]")
        else:
            console.print(
                f"[yellow]launchctl unload (miner) returned {miner_result.returncode}:[/] "
                f"{miner_result.stderr.strip()}"
            )

    if WORKER_PLIST_PATH.exists():
        worker_result = subprocess.run(
            ["launchctl", "unload", str(WORKER_PLIST_PATH)],
            check=False, capture_output=True, text=True,
        )
        if worker_result.returncode == 0:
            console.print("[green]worker unloaded.[/]")
        else:
            console.print(
                f"[yellow]launchctl unload (worker) returned {worker_result.returncode}:[/] "
                f"{worker_result.stderr.strip()}"
            )

    remove = typer.confirm("Remove plist file(s)?", default=False) if sys.stdin.isatty() else False
    if remove:
        PLIST_PATH.unlink(missing_ok=True)
        console.print(f"[dim]removed[/] {PLIST_PATH}")
        WORKER_PLIST_PATH.unlink(missing_ok=True)
        console.print(f"[dim]removed[/] {WORKER_PLIST_PATH}")
        if MINER_PLIST_PATH.exists():
            MINER_PLIST_PATH.unlink(missing_ok=True)
            console.print(f"[dim]removed[/] {MINER_PLIST_PATH}")


def status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON for scripting"),
):
    """Show DB stats, server health, and skill status."""
    import json as _json
    import tempfile as _tempfile

    from rich.panel import Panel
    from rich.table import Table
    from forgememo.core import DB_PATH, get_conn
    from forgememo import config as fm_cfg
    from forgememo.config import detect_ollama

    flag = fm_cfg.get_credits_flag()
    if flag and not json_output:
        console.print(
            Panel(
                f"[bold]Scheduled runs have stopped \u2014 inference credits exhausted.[/]\n\n"
                f"Balance: [red]${flag['balance_usd']}[/]  \u00b7  Last failed: {flag['ts'][:10]}\n\n"
                f"  Add credits \u2192 [cyan]https://forgememo.com/billing[/]\n"
                f"  Or switch provider \u2192 [cyan]forgememo config -i[/]",
                title="[bold red]ACTION REQUIRED \u2014 credits exhausted[/]",
                border_style="red",
                expand=False,
            )
        )

    if not DB_PATH.exists():
        if json_output:
            console.print('{"error": "DB not found \u2014 run forgememo init"}')
        else:
            console.print(
                f"[red]DB not found:[/] {DB_PATH}  (run [cyan]forgememo init[/])"
            )
        raise typer.Exit(1)

    conn = get_conn()
    t_total = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
    p_total = conn.execute("SELECT COUNT(*) FROM principles").fetchone()[0]
    undistilled = conn.execute("SELECT COUNT(*) FROM traces WHERE distilled=0").fetchone()[0]
    e_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    ds_total = conn.execute("SELECT COUNT(*) FROM distilled_summaries").fetchone()[0]
    ss_total = conn.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
    conn.close()

    provider = fm_cfg.get_provider() or "not set"

    settings_path = Path.home() / ".claude" / "settings.json"
    mcp_registered = False
    if settings_path.exists():
        try:
            data = _json.loads(settings_path.read_text())
            mcp_registered = "forgememo" in data.get("mcpServers", {})
        except Exception:
            pass

    skills_status = {agent: path.exists() for agent, path in SKILL_PATHS.items()}

    if json_output:
        out = {
            "db": str(DB_PATH),
            "events": e_total,
            "distilled_summaries": ds_total,
            "session_summaries": ss_total,
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
    table.add_row("Events", str(e_total))
    table.add_row("Distilled", str(ds_total))
    table.add_row("Sessions", str(ss_total))
    table.add_row("Traces", str(t_total))
    table.add_row("Principles", str(p_total))
    undistilled_val = (
        f"[yellow]{undistilled}[/]  \u2192 run: [cyan]forgememo distill[/]"
        if undistilled
        else str(undistilled)
    )
    table.add_row("Undistilled", undistilled_val)
    provider_val = (
        f"[green]{provider}[/]  [dim](switch: forgememo config -i)[/]"
        if provider != "not set"
        else "[yellow]not set[/]  \u2192 run: [cyan]forgememo config -i[/]"
    )
    table.add_row("Provider", provider_val)
    table.add_row(
        "MCP",
        f"[green]{CHECK} registered[/]"
        if mcp_registered
        else f"[yellow]{CROSS} not registered[/]  \u2192 run: [cyan]forgememo init[/]",
    )

    def _skill_version(path: Path):
        if not path.exists():
            return None
        try:
            first_line = path.read_text().splitlines()[0]
            if "version" in first_line.lower():
                import re
                m = re.search(r"(\d+)", first_line)
                return int(m.group(1)) if m else None
        except Exception:
            pass
        return None

    def _template_version(agent: str):
        ext = "json" if agent == "codex" else "md"
        t = SKILL_TEMPLATES_DIR / f"{agent}.{ext}"
        return _skill_version(t)

    skills_parts = []
    for a, ok in skills_status.items():
        if ok:
            installed_v = _skill_version(SKILL_PATHS[a])
            template_v = _template_version(a)
            if installed_v is not None and template_v is not None and installed_v < template_v:
                skills_parts.append(f"[yellow]{a} {CHECK} (v{installed_v}\u2192v{template_v})[/]")
            else:
                skills_parts.append(f"[green]{a} {CHECK}[/]")
        else:
            skills_parts.append(f"[dim]{a} {CROSS}[/]")
    skills_str = "  ".join(skills_parts)
    stale_skills = any(
        skills_status[a]
        and _skill_version(SKILL_PATHS[a]) is not None
        and _template_version(a) is not None
        and _skill_version(SKILL_PATHS[a]) < _template_version(a)
        for a in skills_status
    )
    if stale_skills:
        skills_str += "  \u2192 run: [cyan]forgememo skill update[/]"
    table.add_row("Skills", skills_str)

    _socket_path = Path(
        os.environ.get(
            "FORGEMEMO_SOCKET",
            os.path.join(_tempfile.gettempdir(), "forgememo.sock"),
        )
    )
    _socket_alive = _socket_path.exists()

    if sys.platform == "darwin":
        _plist_ok = PLIST_PATH.exists()
        _worker_ok = WORKER_PLIST_PATH.exists()
        if _plist_ok and _socket_alive:
            daemon_val = f"[green]{CHECK} running[/]"
        elif _plist_ok:
            daemon_val = f"[yellow]{CHECK} plist installed, not running[/]"
        elif _socket_alive:
            daemon_val = f"[green]{CHECK} running (manual)[/]"
        else:
            daemon_val = f"[dim]{CROSS} not installed[/]  \u2192 run: [cyan]forgememo start[/]"
        worker_val = (
            f"[green]{CHECK} plist installed[/]"
            if _worker_ok
            else f"[dim]{CROSS} not installed[/]  \u2192 run: [cyan]forgememo start[/]"
        )
        table.add_row("Daemon", daemon_val)
        table.add_row("Worker", worker_val)
    elif sys.platform == "linux":
        _sd = subprocess.run(
            ["systemctl", "--user", "is-active", "forgememo-daemon"],
            capture_output=True, text=True, check=False,
        )
        _sd_active = _sd.returncode == 0
        if _sd_active or _socket_alive:
            _label = "running" if _sd_active else "running (manual)"
            daemon_val = f"[green]{CHECK} {_label}[/]"
        else:
            _sd_enabled = subprocess.run(
                ["systemctl", "--user", "is-enabled", "forgememo-daemon"],
                capture_output=True, text=True, check=False,
            ).returncode == 0
            if _sd_enabled:
                daemon_val = f"[yellow]{CHECK} service installed, not running[/]"
            else:
                daemon_val = f"[dim]{CROSS} not installed[/]  \u2192 run: [cyan]forgememo start[/]"
        table.add_row("Daemon", daemon_val)
    elif sys.platform == "win32":
        _http_port = str(read_port())
        import socket as _sock
        try:
            with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as _s:
                _s.settimeout(1)
                _win_alive = _s.connect_ex(("127.0.0.1", int(_http_port))) == 0
        except Exception:
            _win_alive = False
        daemon_val = (
            f"[green]{CHECK} running[/] on http://127.0.0.1:{_http_port}"
            if _win_alive
            else f"[dim]{CROSS} not running[/]  \u2192 run: [cyan]forgememo start[/]"
        )
        table.add_row("Daemon", daemon_val)

    console.print(Panel(table, title="Forgememo Status", expand=False))

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


def doctor():
    """Run end-to-end self-test: DB, daemon, write, search, MCP."""
    import json as _json
    import time
    import uuid
    import tempfile as _tempfile

    from forgememo.storage import DB_PATH

    checks_passed = 0
    checks_failed = 0

    def _pass(msg: str):
        nonlocal checks_passed
        checks_passed += 1
        console.print(f"  [green]{CHECK}[/] {msg}")

    def _fail(msg: str):
        nonlocal checks_failed
        checks_failed += 1
        console.print(f"  [red]{CROSS}[/] {msg}")

    console.print("[bold]Forgememo Doctor[/]\n")

    if DB_PATH.exists():
        _pass(f"DB exists: {DB_PATH}")
    else:
        _fail(f"DB not found: {DB_PATH} \u2014 run: forgememo init")
        raise typer.Exit(1)

    from forgememo.storage import get_conn

    conn = get_conn()
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    required_tables = {"events", "distilled_summaries", "session_summaries"}
    missing_tables = required_tables - tables
    conn.close()
    if not missing_tables:
        _pass(f"DB schema OK (tables: {', '.join(sorted(required_tables))})")
    else:
        _fail(f"Missing tables: {', '.join(sorted(missing_tables))} \u2014 run: forgememo init")

    daemon_url = None
    if sys.platform == "win32":
        daemon_url = f"http://127.0.0.1:{read_port()}"
    elif os.environ.get("FORGEMEMO_DAEMON_URL"):
        daemon_url = os.environ["FORGEMEMO_DAEMON_URL"]
    elif os.environ.get("FORGEMEMO_HTTP_PORT"):
        # Explicit env override takes priority — use as-is without lockfile lookup
        daemon_url = f"http://127.0.0.1:{os.environ['FORGEMEMO_HTTP_PORT']}"
    else:
        socket_path = os.environ.get(
            "FORGEMEMO_SOCKET", os.path.join(_tempfile.gettempdir(), "forgememo.sock")
        )
        daemon_url = "socket" if Path(socket_path).exists() else None

    daemon_alive = False
    if daemon_url and daemon_url != "socket":
        try:
            import requests as _req

            resp = _req.get(f"{daemon_url}/health", timeout=3)
            daemon_alive = resp.ok and resp.json().get("ok")
        except Exception:
            pass
    elif daemon_url == "socket":
        try:
            import requests_unixsocket

            socket_path = os.environ.get(
                "FORGEMEMO_SOCKET", os.path.join(_tempfile.gettempdir(), "forgememo.sock")
            )
            sess = requests_unixsocket.Session()
            sock_url = "http+unix://" + socket_path.replace("/", "%2F")
            resp = sess.get(f"{sock_url}/health", timeout=3)
            daemon_alive = resp.ok and resp.json().get("ok")
            daemon_url = f"unix://{socket_path}"
        except Exception:
            pass

    if daemon_alive:
        _pass(f"Daemon reachable at {daemon_url}")
    else:
        _fail("Daemon not reachable \u2014 run: forgememo start")
        console.print(f"\n  [bold]{checks_passed} passed, {checks_failed} failed[/]")
        console.print("  [dim]Fix daemon connectivity before running write/search tests.[/]")
        raise typer.Exit(1)

    probe_id = f"doctor-{uuid.uuid4().hex[:8]}"
    probe_payload = {"message": f"doctor probe {probe_id}"}
    try:
        import requests as _req

        if daemon_url.startswith("unix://"):
            import requests_unixsocket

            socket_path = daemon_url.replace("unix://", "")
            sess = requests_unixsocket.Session()
            sock_url = "http+unix://" + socket_path.replace("/", "%2F")
            resp = sess.post(
                f"{sock_url}/events",
                json={
                    "session_id": probe_id,
                    "project_id": probe_id,
                    "source_tool": "doctor",
                    "event_type": "doctor_probe",
                    "tool_name": None,
                    "payload": probe_payload,
                    "seq": int(time.time() * 1000),
                },
                timeout=5,
            )
        else:
            resp = _req.post(
                f"{daemon_url}/events",
                json={
                    "session_id": probe_id,
                    "project_id": probe_id,
                    "source_tool": "doctor",
                    "event_type": "doctor_probe",
                    "tool_name": None,
                    "payload": probe_payload,
                    "seq": int(time.time() * 1000),
                },
                timeout=5,
            )
        if resp.status_code == 201:
            _pass("Event write OK (POST /events -> 201)")
        else:
            _fail(f"Event write unexpected status {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        _fail(f"Event write failed: {e}")

    try:
        if daemon_url.startswith("unix://"):
            resp = sess.get(f"{sock_url}/search", params={"q": probe_id}, timeout=5)
        else:
            resp = _req.get(f"{daemon_url}/search", params={"q": probe_id}, timeout=5)
        results = resp.json().get("results", [])
        event_found = any(r.get("id", "").startswith("e:") for r in results)
        if event_found:
            _pass("Event search OK (event found via /search)")
        else:
            _fail("Event search FAILED \u2014 event was written but /search returned no e: results")
    except Exception as e:
        _fail(f"Event search failed: {e}")

    settings_path = Path.home() / ".claude" / "settings.json"
    mcp_ok = False
    if settings_path.exists():
        try:
            data = _json.loads(settings_path.read_text())
            mcp_ok = "forgememo" in data.get("mcpServers", {})
        except Exception:
            pass
    if mcp_ok:
        _pass("MCP registered in ~/.claude/settings.json")
    else:
        _fail("MCP not registered \u2014 run: forgememo init")

    console.print(f"\n  [bold]{checks_passed} passed, {checks_failed} failed[/]")
    if checks_failed == 0:
        console.print("  [green]All checks passed![/]")
    else:
        console.print("  [yellow]Some checks failed. See above for details.[/]")
        raise typer.Exit(1)
