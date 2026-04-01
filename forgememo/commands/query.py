"""Query commands: search, store, logs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from forgememo.commands._shared import _detect_project_from_git, console


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


def logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (like tail -f)"),
    worker: bool = typer.Option(False, "--worker", help="Show worker logs instead of daemon"),
):
    """Tail the daemon or worker log file."""
    log_dir = Path.home() / ".forgememo" / "logs"
    log_file = log_dir / ("forgememo_worker.log" if worker else "forgememo_daemon.log")

    if not log_file.exists():
        console.print(f"[yellow]Log file not found:[/] {log_file}")
        console.print("[dim]Start the daemon first: forgememo start[/]")
        raise typer.Exit(1)

    if follow:
        console.print(f"[dim]Following {log_file} (Ctrl+C to stop)...[/]")
        try:
            subprocess.run(
                ["tail", "-f", "-n", str(lines), str(log_file)]
                if sys.platform != "win32"
                else ["powershell", "-Command", f"Get-Content -Path '{log_file}' -Tail {lines} -Wait"],
                check=False,
            )
        except KeyboardInterrupt:
            pass
    else:
        try:
            text = log_file.read_text(errors="replace")
            tail = text.splitlines()[-lines:]
            for line in tail:
                console.print(line)
        except Exception as e:
            console.print(f"[red]error:[/] {e}")
            raise typer.Exit(1)
