# Changelog

## [Unreleased]

## [0.4.1] — 2026-04-01

### Added
- `forgememo config -i` / `--interactive` flag — opens the provider picker directly without the table+confirm step; lets agents surface the exact command for users to run when switching providers mid-session
- All three skill files (claude.md v5, gemini.md v6, codex.json v4) now include a `## Switching Providers` section teaching agents to run `forgememo config -i` and relay the output to the user
- `forgememo status` provider row now shows `(switch: forgememo config -i)` hint inline

### Changed
- Non-TTY `_prompt_provider_setup` message simplified to a single actionable line: "Provider picker requires a real terminal. Ask the user to run: forgememo config -i"

## [0.4.0] — 2026-04-01

### Cross-agent session hooks
- Hook adapter now handles all five agent platforms: `SessionStart`/`SessionEnd` (Claude Code), `UserPromptSubmit`/`Stop` (Codex), `BeforeAgent`/`AfterAgent` (Gemini), `sessionStart`/`agentStop` (Copilot), `session.created`/`session.idle`/`session.deleted` (OpenCode)
- `_SESSION_RECALL_EVENTS` and `_SESSION_END_EVENTS` dispatch sets cover every variant — no per-agent configuration needed in hook.py
- `PostToolUse` / `AfterTool` (Gemini) / `tool.done` (OpenCode) now captured and posted to daemon

### HTTP-first transport (Windows compatibility)
- `FORGEMEMO_HTTP_PORT` defaults to `"5555"` on all platforms; previous conditional `None` on POSIX caused Windows CI to fail with "daemon transport unavailable"
- Socket-first path preserved on POSIX (`requests-unixsocket`) with HTTP fallback; Windows skips socket entirely
- `FORGEMEMO_DAEMON_URL` env var allows full URL override for any transport scenario

### CLI refactored into commands/ subpackage
- `cli.py` split into `forgememo/commands/` for maintainability; entry point and public API unchanged
- `api.py` deleted; concurrent-write tests migrated to daemon + storage layer directly

### Daemon self-heal
- `_ensure_daemon()` in hook adapter auto-restarts the daemon if unreachable, then polls up to 5s before giving up
- Session recall emits an actionable message (`"run: forgememo start"`) rather than silently failing when daemon is down

### Test coverage
- Full hook test suite: 81 tests covering `strip_private`, `_normalize_event`, `_format_context_json`, `_read_stdin_json`, `_post_event` transport paths, `_daemon_get` socket/HTTP/error paths, session recall (narrative truncation, multi-summary, per-tool format), session end (POSIX/Windows, missing binary, missing cwd), `main()` error exits, and parametrized dispatch for every cross-agent event name

## [0.2.10] — 2026-03-31

- Ensure Claude Code hooks are idempotently registered via `forgememo init`, which now writes `forgememo hook UserPromptSubmit`/`Stop` into `~/.claude/settings.json` and exposes a hidden `forgememo hook` CLI entry so the hook payload can run without embedding hardcoded paths.
- When `end_session` synthesizes a summary, the CLI updates the `<forgememo-context>` block in each agent context file (Claude, Codex, Gemini, Copilot, OpenCode) so long-lived prompts see the latest memory state.
- The daemon now handles SQLite `database is locked` errors more gracefully, exposes `/events/batch`, and the MCP server adds a `session_sync` tool that registers `SessionStart` plus fetches recent context, letting lifecycle-aware agents (Gemini, Windows, etc.) opt into hooking even when shell hooks cannot run.
- Storage connections now open with a 30s timeout, `PRAGMA synchronous=NORMAL`, and a 30s busy timeout to reduce lock contention during bursty writes.

## [0.2.4] — 2026-03-31

- Fix: auto-init in `_main` callback called full `init()` which ran `_prompt_provider_setup` and raised `typer.Exit(1)` in non-TTY sessions, causing `forgememo status` to fail on first run even though the DB was created successfully; auto-init now calls only `init_db()` + `_register_mcp()` + `_auto_detect_and_generate_skills()` directly — no provider prompt

## [0.2.3] — 2026-03-31

- Fix: auto-init in `_main` callback called `init(yes=True)` without `provider=None` — Typer passes the `OptionInfo` default object which is truthy, causing `_configure_provider_noninteractive` to error with "Invalid provider: <typer.models.OptionInfo object>" on first `forgememo status` run
- Fix: `core.py` save summary printed "run: bm distill" (stale old CLI name) — now "run: forgememo distill"
- Fix: `scanner.py` error messages referenced "forgemem config" (old CLI name) — now "forgememo config"

## [0.2.2] — 2026-03-31

- Fix: sync `requirements.txt` with `pyproject.toml` — bump `fastmcp>=0.6.0` → `>=2.0.0`, add `typer`, `rich`, `questionary` (CI was installing incompatible versions)
- Add: `.github/workflows/ci.yml` — cross-platform matrix (ubuntu/macos/windows × Python 3.10/3.12) plus Linux container job

## [0.2.1] — 2026-03-31

- Fix: daemon `/search` N+1 concept query replaced with a single `WHERE id IN (...)` batch fetch
- Fix: scanner now writes learnings to daemon `/events` (v2) instead of legacy `core.cmd_save`; falls back to legacy path if daemon is not running
- Fix: `is_duplicate` in scanner checks v2 events table (via `json_extract`) in addition to legacy traces
- Fix: daemon log-path fallback catches `OSError` (covers read-only filesystem) not just `PermissionError`
- Perf: worker processes up to 10 events per poll cycle; sleeps only when queue is empty
- Perf: worker short-circuits inference for scanner events that carry a pre-extracted `_principle` — avoids a redundant API call
- Add: `integrations/` with hook config snippets for Claude Code, Codex, OpenCode, Gemini and an interactive `setup.sh`

## [0.2.0] — 2026-03-31

- Add: v2 storage layer — `events`, `distilled_summaries`, `session_summaries` tables with FTS5; compat view preserves legacy `traces`/`principles` data
- Add: daemon (`forgememo.daemon`) — single write path over UNIX socket (opt-in HTTP); routes `POST /events`, `/search`, `/timeline`, `/observation`, `/session_summaries`
- Add: background worker (`forgememo.worker`) — distills raw events into structured summaries via inference
- Add: hook adapter (`forgememo.hook`) — tool-agnostic event capture with `<private>` tag stripping
- Add: `claude_code` provider — use Claude subscription via CLI, no API key required
- Fix: `mcp_server.py` syntax error (import not indented inside try block)
- Fix: `forgemem` → `forgememo` import names across inference, scanner, core
- Fix: scanner and query_tool hardcoded wrong DB paths

## [0.1.12] — 2026-03-29

- Feature: selecting forgemem as provider during `forgemem init` now immediately triggers browser OAuth login — no separate `forgemem auth login` step required
- Fix: SQLite WAL mode + `timeout=10` + `PRAGMA busy_timeout=5000` applied consistently across all connection sites (core.py, api.py, mcp_server.py, query_tool.py, scanner.py, cli.py) to eliminate "database is locked" errors under concurrent terminal sessions

## [0.1.11] — 2026-03-29

- Change: first-run `forgemem init` now requires an interactive TTY for provider selection; `--yes` no longer lets agents or piped sessions bypass provider choice
- Change: install docs and agent skill files now instruct users to run `forgemem init` interactively instead of the previous unattended `init --yes` path

## [0.1.10] — 2026-03-28

- Fix: remove unused `INIT_SQL` import in `test_cli.py` (lint clean)

## [0.1.9] — 2026-03-28

- Fix: `forgemem init` and `forgemem start` now exit with code 1 when no inference provider is configured — agents that ignore warning panels can no longer silently proceed to start without a working provider
- Fix: `forgemem init` auto-runs `forgemem start` on success (provider configured) — reduces setup from two commands to one; agents only need `forgemem init` to complete full initialization

## [0.1.8] — 2026-03-28

- Fix: `forgemem init` non-interactive (agent/piped) runs now show a prominent red `ACTION REQUIRED` panel when no inference provider is configured, listing all five provider options — previously a single yellow line was easily missed
- Fix: `forgemem init` success panel now promotes provider setup to a required step 0 when provider is not yet configured, preventing users from proceeding with an unconfigured distillation backend

## [0.1.7] — 2026-03-28

- Fix: eliminate version string duplication between `pyproject.toml` and `forgemem/__init__.py` — `__init__.py` now derives `__version__` from `importlib.metadata` at runtime, making `pyproject.toml` the single source of truth
- Add: `TestVersionSync` tests assert that `forgemem.__version__`, installed package metadata, and `pyproject.toml` all agree — prevents future drift

## [0.1.6] — 2026-03-28

- Fix: `__version__` in `forgemem/__init__.py` was not bumped when 0.1.5 was released — `pip show forgemem` reported 0.1.5 but `forgemem.__version__` returned 0.1.4

## [0.1.5] — 2026-03-28

- Internal release (version string mismatch — superseded by 0.1.6)

## [0.1.4] — 2026-03-28

Bug fixes and agent UX improvements:

- **Fix: backwards update notification** — `_check_for_update` used `!=` comparison, so running a newer version than the cached PyPI value printed "Update available: 0.1.2 (you have 0.1.3)". Now uses proper `>` tuple comparison via new `_ver()` helper
- **Fix: stale update cache after upgrade** — cache was only invalidated after 24h, so false notifications persisted until TTL expired. Now invalidated immediately when current version ≥ cached version
- **Fix: `forgemem init` hangs in agent/non-TTY sessions** — stdin non-TTY is now auto-detected and sets `--yes` automatically, preventing `typer.confirm`/`typer.prompt` from blocking
- **New: `forgemem start --mine`** — installs a second macOS LaunchAgent (`com.forgemem.miner.plist`) that runs `forgemem mine` on a schedule (default 3600s, configurable via `--mine-interval`)
- **New: `forgemem stop` unloads miner agent** — if a miner plist is present, `stop` now unloads and optionally removes it alongside the server plist
- **New: skill files include Setup section** — all agent skill files (Claude Code, Gemini, Codex) now have a `## Setup` section with the exact non-interactive install commands (`forgemem init --yes && forgemem start`)
- **New: `test_cli.py`** — 22 edge-case unit tests covering version comparison, cache invalidation, non-TTY init, miner plist creation, and stop cleanup

## [0.1.3] — 2026-03-28

CLI streamlined as a modern tool:

- `status` now renders a structured table (DB, traces, principles, provider, MCP, skills, daemon) with actionable hints
- `status --json` added for scripting
- `config` (no args) displays current config as a table
- `mine` and `distill` show a spinner instead of raw inline prints
- `mine` bug fix: `scanner.run()` → `scanner.main()` (would have crashed on every call)
- `start` is now cross-platform: Linux shows systemd unit snippet, Windows shows Task Scheduler command
- `help` no longer crashes on Windows cp1252/ascii consoles (Unicode-safe Console + plain-hyphen separators)
- `✓`/`✗` symbols fall back to `ok`/`x` on narrow-encoding terminals
- Update-available banner moved to end of output (via `atexit`) so it no longer buries command output
- Silent `except Exception: pass` in `sync` now surfaces warnings for skipped traces/principles
- All `print()` calls replaced with `console.print()` for consistent Rich output

## [0.1.0] — 2026-03-28

Initial public release under Apache-2.0.

- MCP server (stdio + HTTP/SSE) with `retrieve_memories` and `save_trace` tools
- CLI: `init`, `start`, `stop`, `status`, `search`, `store`, `mine`, `distill`, `config`, `skill`
- SQLite + FTS5 local database at `~/.forgemem/`
- Auto-registration for Claude Code, Gemini CLI, Codex
- macOS LaunchAgent support for background scanning
- Daily scan mines git history and session traces via Claude
