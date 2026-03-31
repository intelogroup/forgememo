# Forgememo

**Persistent long-term memory for AI agents — cross-session context that actually sticks.**

[![PyPI version](https://img.shields.io/pypi/v/forgememo.svg)](https://pypi.org/project/forgememo/)
[![Python](https://img.shields.io/pypi/pyversions/forgememo.svg)](https://pypi.org/project/forgememo/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Forgememo mines your git history, session traces, and project notes to extract what actually happened — failures, successes, plans, and hard-won lessons. It stores them locally in SQLite and exposes them to AI agents via MCP so they start every session informed instead of blind.

---

## The Problem

Every new agent session starts from zero. It doesn't know you already tried the Zod 4 schema approach and it broke. It doesn't know that wildcard CORS killed auth in production last quarter. It doesn't know which approach you abandoned and why.

Without Forgememo, agents repeat your mistakes. With it, they skip straight to what works.

---

## Installation

```bash
pip install forgememo
forgememo init
```

`forgememo init` now requires a real TTY on first run so the user must choose an inference provider interactively. Agents cannot bypass that step with `--yes` or a piped session. After `init` completes, start the daemon with `forgememo start`; then **restart your AI agent** (Claude Code, Gemini CLI, or Codex) to pick up the MCP connection.

---

## How It Works

1. **Hook** — tool events are normalized and sent to the daemon (socket-first).
2. **Daemon** — single write path, dedup, event queue.
3. **Worker** — distills raw events into durable summaries.
4. **Store** — SQLite holds `events`, `distilled_summaries`, and `session_summaries`.
5. **Serve** — MCP tools query the daemon API (read-only).

Agents call `search_memories` and `get_memory_details` to pull prior context without re-learning it.

---

## Quick Start

```bash
# Install
pip install forgememo

# Initialize (interactive on first run)
forgememo init

# Start the daemon + worker (macOS LaunchAgents)
forgememo start

# Optional: enable legacy mining on a schedule
forgememo start --mine

# Restart your agent, then verify
forgememo status
```

---

## Agent Support

| Agent | Auto-detected | Skill file written |
|-------|:---:|:---:|
| Claude Code | ✓ | `~/.claude/skills/forgememo.md` |
| Gemini CLI | ✓ | `~/.gemini/forgememo-skill.md` |
| OpenAI Codex | ✓ | `~/.codex/forgememo-skill.json` |

`forgememo init` detects which agents are installed and writes the appropriate skill file automatically. Agents use `search_memories` and `get_memory_details` via MCP — no extra configuration needed.

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_memories` | Compact index search (IDs + titles) |
| `get_memory_details` | Full content for specific IDs |
| `get_memory_timeline` | Temporal context around a distilled summary |
| `save_session_summary` | Write a structured session summary via daemon |
| `get_session_summary` | Retrieve recent session summaries |
| `retrieve_memories` | Deprecated alias for `search_memories` |

---

## CLI Reference

```bash
forgememo init                # Initialize DB, choose provider, register MCP, write skill files
forgememo start               # Start daemon + worker (macOS LaunchAgents)
forgememo start --mine        # Also install a scheduled mining agent (hourly, legacy)
forgememo stop                # Stop daemon + worker
forgememo status              # Show DB stats, server health, skill status
forgememo export-context      # Write CLAUDE.md / AGENTS.md context blocks
forgememo daemon              # Run daemon in foreground
forgememo worker              # Run worker in foreground
forgememo store "<text>"      # Save a memory trace manually
forgememo search "<query>"    # Search stored memories
forgememo mine                # Scan repos and session files for new learnings
forgememo distill             # Condense undistilled traces into principles
forgememo config              # Set inference provider (anthropic / ollama / gemini / …)
forgememo auth login          # Authenticate for managed inference (no BYOK needed)
```

Legacy trace/principle commands (`store`, `search`, `mine`, `distill`) remain for backward compatibility.

---

## Inference Providers

Forgememo uses an LLM for mining and distillation. Three options:

| Provider | Setup | Cost |
|----------|-------|------|
| **Forgememo managed** | choose it in `forgememo init`, then run `forgememo auth login` | Free tier + paid plans |
| **Ollama** (local) | choose it in `forgememo init` | Free, fully private |
| **BYOK** (Anthropic / OpenAI / Gemini) | `forgememo config <provider> --key <key>` | Your API costs |

`forgememo init` now requires the user to choose a provider interactively on first run.

---

## Platform Support

| Platform | Daemon + Worker | Auto-start |
|----------|-----------------|------------|
| macOS | LaunchAgents (`launchctl`) | On login |
| Linux | systemd user services (instructions printed) | Manual |
| Windows | Task Scheduler (command printed) | Manual |

---

## Licensing

Forgememo is **Apache-2.0** for community use.

- **Community** — Apache-2.0, permissive, local-first, commercial-friendly
- **Enterprise** — hosted terms with SLA, SSO/SAML, audit logs, priority support, and private hosting
- **Contributing** — all contributors must sign the [CLA](CLA.md) by adding their name to [CONTRIBUTORS.md](CONTRIBUTORS.md) in their first PR
