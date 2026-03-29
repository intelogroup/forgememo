# Forgemem

**Persistent long-term memory for AI agents — cross-session context that actually sticks.**

[![PyPI version](https://img.shields.io/pypi/v/forgemem.svg)](https://pypi.org/project/forgemem/)
[![Python](https://img.shields.io/pypi/pyversions/forgemem.svg)](https://pypi.org/project/forgemem/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Forgemem mines your git history, session traces, and project notes to extract what actually happened — failures, successes, plans, and hard-won lessons. It stores them locally in SQLite and exposes them to AI agents via MCP so they start every session informed instead of blind.

---

## The Problem

Every new agent session starts from zero. It doesn't know you already tried the Zod 4 schema approach and it broke. It doesn't know that wildcard CORS killed auth in production last quarter. It doesn't know which approach you abandoned and why.

Without Forgemem, agents repeat your mistakes. With it, they skip straight to what works.

---

## Installation

```bash
pip install forgemem
forgemem init
```

`forgemem init` now requires a real TTY on first run so the user must choose an inference provider interactively. Agents cannot bypass that step with `--yes` or a piped session. After `init` completes, it auto-starts the MCP server; then **restart your AI agent** (Claude Code, Gemini CLI, or Codex) to pick up the new MCP connection.

---

## How It Works

1. **Mine** — scans git repos and session notes, extracts failure/success/plan traces, saves them with an impact score
2. **Store** — all traces and distilled principles live locally at `~/.forgemem/forgemem_memory.db`
3. **Serve** — an MCP server exposes the database to agents via the Model Context Protocol

Agents call `retrieve_memories` before starting any task. That one call surfaces the top matching failures and principles from your history — so they don't burn tokens re-learning what you already know.

---

## Quick Start

```bash
# Install
pip install forgemem

# Initialize (interactive on first run)
forgemem init

# Optionally: also schedule background mining every hour
forgemem start --mine

# Restart your agent, then verify
forgemem status
```

---

## Agent Support

| Agent | Auto-detected | Skill file written |
|-------|:---:|:---:|
| Claude Code | ✓ | `~/.claude/skills/forgemem.md` |
| Gemini CLI | ✓ | `~/.gemini/forgemem-skill.md` |
| OpenAI Codex | ✓ | `~/.codex/forgemem-skill.json` |

`forgemem init` detects which agents are installed and writes the appropriate skill file automatically. Agents use `retrieve_memories` and `save_trace` via MCP — no extra configuration needed.

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `retrieve_memories` | Search principles and traces by keyword before starting a task |
| `save_trace` | Save a failure, success, plan, or note during or after a session |
| `mine_session` | Agent-driven mining — no API key required, the agent is the LLM |
| `distill_session` | Condense raw traces into durable principles |
| `forgemem_stats` | Summary of DB contents and server health |

---

## CLI Reference

```bash
forgemem init                # Initialize DB, choose provider, register MCP, write skill files
forgemem start               # Start MCP server (background daemon on macOS)
forgemem start --mine        # Also install a scheduled mining agent (hourly)
forgemem stop                # Stop the daemon
forgemem status              # Show DB stats, server health, skill status
forgemem store "<text>"      # Save a memory trace manually
forgemem search "<query>"    # Search stored memories
forgemem mine                # Scan repos and session files for new learnings
forgemem distill             # Condense undistilled traces into principles
forgemem config              # Set inference provider (anthropic / ollama / gemini / …)
forgemem auth login          # Authenticate for managed inference (no BYOK needed)
```

---

## Inference Providers

Forgemem uses an LLM for mining and distillation. Three options:

| Provider | Setup | Cost |
|----------|-------|------|
| **Forgemem managed** | choose it in `forgemem init`, then run `forgemem auth login` | Free tier + paid plans |
| **Ollama** (local) | choose it in `forgemem init` | Free, fully private |
| **BYOK** (Anthropic / OpenAI / Gemini) | `forgemem config <provider> --key <key>` | Your API costs |

`forgemem init` now requires the user to choose a provider interactively on first run. The `mine_session` and `distill_session` MCP tools still work with any subscription — no separate key needed since the agent itself is the LLM.

---

## Platform Support

| Platform | Daemon | Auto-start |
|----------|--------|------------|
| macOS | LaunchAgent (`launchctl`) | On login |
| Linux | systemd user service (instructions printed) | Manual |
| Windows | Task Scheduler (command printed) | Manual |

---

## Licensing

Forgemem is **Apache-2.0** for community use.

- **Community** — Apache-2.0, permissive, local-first, commercial-friendly
- **Enterprise** — hosted terms with SLA, SSO/SAML, audit logs, priority support, and private hosting
- **Contributing** — all contributors must sign the [CLA](CLA.md) by adding their name to [CONTRIBUTORS.md](CONTRIBUTORS.md) in their first PR
