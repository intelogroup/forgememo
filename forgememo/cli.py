#!/usr/bin/env python3
"""Forgememo CLI — flat ruff-style commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from typing_extensions import Annotated

from forgememo import __version__
from forgememo.commands._shared import (  # re-exported for backward-compat
    MINER_PLIST_PATH,  # noqa: F401
    PLIST_PATH,  # noqa: F401
    LOG_PATH,  # noqa: F401
    _auto_detect_and_generate_skills,
    _make_console,  # noqa: F401
    _register_mcp,
    console,
)
from forgememo.commands.configure import _check_api_response  # noqa: F401  re-exported for tests
from forgememo.commands.lifecycle import _do_start  # noqa: F401  re-exported for tests

# Re-derive at import/reload time so that reloading cli with a different
# stdout encoding (e.g. in tests) produces the correct symbol values.
_enc = (getattr(sys.stdout, "encoding", None) or "ascii").lower()
_UNICODE_OK = _enc not in ("ascii", "cp1252", "latin-1", "latin1")
CHECK = "\u2713" if _UNICODE_OK else "ok"
CROSS = "\u2717" if _UNICODE_OK else "x"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="forgememo",
    help="Long-term memory store for AI agents.",
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Update check (stays here — uses Path/requests directly, tested via cli.Path)
# ---------------------------------------------------------------------------


def _ver(v: str) -> tuple:
    """Parse a semver string into a comparable tuple of ints."""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"forgememo {__version__}")
        raise typer.Exit()


def _check_for_update() -> None:
    """Print a one-liner if a newer version is available on PyPI (cached 24h)."""
    import time

    cache_file = Path.home() / ".forgememo" / ".update_check"
    try:
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < 86400:
                latest = cache_file.read_text().strip()
                if latest and _ver(__version__) >= _ver(latest):
                    cache_file.unlink(missing_ok=True)
                elif latest and _ver(latest) > _ver(__version__):
                    console.print(
                        f"[yellow]Update available:[/] forgememo {latest} "
                        f"(you have {__version__}) \u2014 run [bold]pip install -U forgememo[/]"
                    )
                    return
                else:
                    return
        import requests as _requests

        resp = _requests.get("https://pypi.org/pypi/forgememo/json", timeout=2)
        data = resp.json()
        latest = data["info"]["version"]
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(latest)
        if _ver(latest) > _ver(__version__):
            console.print(
                f"[yellow]Update available:[/] forgememo {latest} "
                f"(you have {__version__}) \u2014 run [bold]pip install -U forgememo[/]"
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# App callback
# ---------------------------------------------------------------------------


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
        console.print("[dim]First run detected \u2014 initializing forgememo...[/]")
        from forgememo.storage import init_db

        init_db()
        console.print(f"[dim]DB initialized: {DB_PATH}[/]")
        _register_mcp(Path.home() / ".claude" / "settings.json")
        _auto_detect_and_generate_skills(yes=True)
    if ctx.invoked_subcommand not in {"mcp"}:
        import atexit

        atexit.register(_check_for_update)


# ---------------------------------------------------------------------------
# Register commands
# ---------------------------------------------------------------------------

from forgememo.commands.lifecycle import init, start, stop, status, doctor  # noqa: E402
from forgememo.commands.query import search, store, logs  # noqa: E402
from forgememo.commands.processing import mine, distill  # noqa: E402
from forgememo.commands.configure import config, auth, sync  # noqa: E402
from forgememo.commands.integration import skill, help_cmd, export_context  # noqa: E402
from forgememo.commands.internal import mcp_cmd, daemon_cmd, worker_cmd, end_session, hook_cmd  # noqa: E402

for _fn in [init, start, stop, status, doctor, search, store, logs, mine, distill, config, auth, sync, skill]:
    app.command()(_fn)

app.command("export-context")(export_context)
app.command()(help_cmd)  # registered as "help-cmd" by default — override below
app.registered_commands[-1].name = "help"

app.command(hidden=True)(mcp_cmd)
app.registered_commands[-1].name = "mcp"
app.command(hidden=True)(daemon_cmd)
app.registered_commands[-1].name = "daemon"
app.command(hidden=True)(worker_cmd)
app.registered_commands[-1].name = "worker"
app.command(name="end-session", hidden=True)(end_session)
app.command(hidden=True)(hook_cmd)
app.registered_commands[-1].name = "hook"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    app()


if __name__ == "__main__":
    main()
