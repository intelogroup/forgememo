# Changelog

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
