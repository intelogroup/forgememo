"""Integration commands: skill, help, export_context."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from forgememo.commands._shared import (
    CHECK,
    SKILL_PATHS,
    _format_context_markdown,
    _generate_skill,
    _replace_block,
    console,
)


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
            console.print(f"  {a}: {path} \u2014 {status_str}")
        return

    if action in ("generate", "update"):
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


def help_cmd():
    """Show onboarding guide and command reference."""
    from rich.panel import Panel
    from forgememo.core import DB_PATH
    from forgememo import config as fm_cfg
    from forgememo.commands._shared import CHECK, CROSS

    provider = fm_cfg.load().get("provider") or "[yellow]not set[/]"
    db_ok = (
        f"[green]{CHECK}[/]"
        if DB_PATH.exists()
        else f"[red]{CROSS} (run forgememo init)[/]"
    )

    _D = "-"
    console.print(
        Panel(
            "[bold]What is Forgememo?[/]\n"
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
            f"  [cyan]forgememo doctor[/]            {_D} end-to-end self-test (DB, daemon, write/search)\n"
            f"  [cyan]forgememo logs[/]              {_D} tail daemon logs (--follow, --worker)\n"
            f"  [cyan]forgememo config[/]            {_D} set/view inference provider & model\n"
            f"  [cyan]forgememo auth[/]              {_D} login / logout / check API key status\n"
            f"  [cyan]forgememo skill generate[/]    {_D} regenerate agent skill files\n"
            f"  [cyan]forgememo stop[/]              {_D} stop the background server\n\n"
            f"[bold]Current state:[/]  DB {db_ok}   Provider: {provider}",
            title="Forgememo - Help",
            expand=False,
        )
    )


def export_context(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project ID to scope results"),
    k: int = typer.Option(10, "--k", help="Max principles to include"),
    template: str = typer.Option("claude", "--template", help="Template: claude | codex | generic"),
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

    updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    project_label = project or "all"
    context_md = _format_context_markdown(project_label, updated, principles, last_session)

    if template_file:
        try:
            from jinja2 import Template
        except ImportError:
            console.print(
                "[red]error:[/] jinja2 required for --template-file (pip install jinja2)"
            )
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
        console.print(f"[green]{CHECK}[/] wrote {output.resolve()}")
    else:
        console.print(block)
