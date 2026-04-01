"""Processing commands: mine, distill."""

from __future__ import annotations

import argparse

import typer

from forgememo.commands._shared import console


def mine():
    """Run the memory scanner."""
    from forgememo import scanner

    with console.status("[dim]Scanning repos and memory files...[/]"):
        scanner.main()


def distill(target: str = typer.Argument("all")):
    """Distill undistilled traces into principles."""
    from forgememo.core import cmd_distill

    session = None if target == "all" else target
    args = argparse.Namespace(session=session, project=None)
    with console.status("[dim]Distilling traces into principles...[/]"):
        cmd_distill(args)
