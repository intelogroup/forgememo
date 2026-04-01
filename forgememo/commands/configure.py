"""Configure commands: config, auth, sync."""

from __future__ import annotations

import os
import sys
from typing import Optional

import typer

from forgememo.commands._shared import console


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

_POST_AUTH_TIMEOUT = 60


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
    """Run the browser-based OAuth login flow. Returns True on success."""
    import http.server
    import secrets
    import threading
    import urllib.parse
    import webbrowser

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

        def log_message(self, *args):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    except OSError:
        console.print("[red]error:[/] could not bind a local callback port. Try again.")
        raise typer.Exit(1)
    port = server.server_address[1]

    def _serve():
        server.handle_request()
        server.server_close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    _api_base = os.environ.get("FORGEMEM_API_URL", "https://forgememo-server.onrender.com")
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
        console.print("[green]Authenticated![/] Provider set to Forgememo Inference.")
        console.print("[dim]Your $5 free credits are ready.[/]")
        return True
    else:
        console.print("[red]Login timed out or was cancelled.[/]")
        raise typer.Exit(1)


def _do_post_auth_setup(jwt: str) -> list:
    """After login: check balance, optionally open browser for billing setup."""
    import http.server
    import secrets
    import threading
    import time
    import urllib.parse
    import webbrowser

    import requests as _req

    _api_base = os.environ.get("FORGEMEM_API_URL", "https://forgememo-server.onrender.com")

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
    console.print("\n[bold]Add credits to keep using Forgememo.[/]")
    console.print(f"Opening billing setup...\n{billing_url}")
    webbrowser.open(billing_url)
    console.print("[dim]Waiting for billing events (Ctrl+C to skip)...[/]")

    t.join(timeout=_POST_AUTH_TIMEOUT + 5)

    card_event = next((e for e in received_events if e.get("type") == "card_added"), None)
    credits_event = next(
        (e for e in received_events if e.get("type") == "credits_added"), None
    )

    if card_event:
        console.print("[green]Payment method added![/]")
    if credits_event:
        amount = credits_event.get("amount", "?")
        console.print(f"[green]${amount} credits added![/] You're ready to go.")
    if not card_event and not credits_event:
        console.print("[dim]Skipped. Run 'forgememo auth credits' later to add credits.[/]")

    return received_events


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def config(
    provider: Optional[str] = typer.Argument(
        None, help="Provider: anthropic | openai | gemini | ollama | forgememo"
    ),
    key: Optional[str] = typer.Option(None, "--key", "-k", help="API key for the provider"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override default model"),
    ollama_url: Optional[str] = typer.Option(
        None, "--ollama-url", help="Ollama base URL (default: http://localhost:11434)"
    ),
    show: bool = typer.Option(False, "--show", help="Print current config (masks keys)"),
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
      forgememo config forgememo                           # use Forgememo managed inference\n
    """
    from rich.panel import Panel
    from rich.table import Table
    from forgememo import config as fm_cfg
    from forgememo.commands.lifecycle import _prompt_provider_setup

    if provider is None or provider == "show" or show:
        provider = None
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
        console.print(Panel(tbl, title="Forgememo Config", expand=False))

        if sys.stdin.isatty() and not show:
            switch = typer.confirm("\nSwitch provider?", default=False)
            if switch:
                _prompt_provider_setup(yes=False, force=True)
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
                console.print("  [yellow]No models pulled yet.[/] Run: ollama pull llama3.2")
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
        msg += "\n[green]Inference runs locally \u2014 your traces never leave your machine.[/]"
    elif key:
        msg += f"\n[green]API key stored[/] in {fm_cfg.CONFIG_PATH}"
    else:
        msg += "\n[dim]No key stored \u2014 will fall back to env var[/]"
    if provider == "forgememo":
        console.print(msg)
        console.print("[green]Provider set to forgememo.[/] Let's authenticate now...")
        _do_auth_login()
        return
    console.print(msg)


def auth(
    action: str = typer.Argument("status", help="login | logout | status"),
):
    """Authenticate with Forgememo for managed inference.

    Examples:\n
      forgememo auth login    # open browser, store token\n
      forgememo auth status   # show current auth state\n
      forgememo auth logout   # remove stored token\n
    """
    from forgememo import config as fm_cfg

    if action == "status":
        token = fm_cfg.load().get("forgememo_token")
        if token:
            console.print("[green]Authenticated[/] with Forgememo Inference")
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


def sync(
    push_only: bool = typer.Option(False, "--push-only", help="Push local changes only"),
    pull_only: bool = typer.Option(False, "--pull-only", help="Pull remote changes only"),
):
    """Sync local memory with Forgememo cloud (requires Sync subscription).

    Pushes new local traces + principles to the cloud, then pulls changes
    from your other devices since the last sync. Safe to run repeatedly — all
    operations are idempotent.

    Examples:\n
      forgememo sync              # push + pull\n
      forgememo sync --push-only  # push local changes only\n
      forgememo sync --pull-only  # pull remote changes only\n
    """
    import sqlite3
    from datetime import datetime, timezone

    import requests as req

    from forgememo import config as fm_cfg
    from forgememo.core import DB_PATH

    token = fm_cfg.load().get("forgememo_token")
    if not token:
        console.print("[yellow]Not authenticated.[/] Run: forgememo auth login")
        raise typer.Exit(1)

    managed_url = os.environ.get("FORGEMEM_API_URL", "https://forgememo-server.onrender.com")
    device_id = fm_cfg.get_device_id()
    last_sync = fm_cfg.get_last_sync_ts()
    headers = {"Authorization": f"Bearer {token}"}

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
                        "device_name": os.uname().nodename if hasattr(os, "uname") else "",
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
