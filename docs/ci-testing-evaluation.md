# Cross-Platform CI Testing Evaluation for Forgememo

## Problem Statement

Forgememo is a Python CLI tool (daemon + MCP server) that integrates with 5 AI
coding agents (Claude Code, Gemini CLI, Codex, OpenCode, Copilot) across 3 OSes
(macOS, Linux, Windows). We need to test installation, daemon lifecycle, hook
integration, and MCP transport across a **3x3 matrix**:

| | macOS | Linux | Windows |
|---|---|---|---|
| **Claude Code** | UNIX socket + LaunchAgent | UNIX socket + systemd | HTTP + Task Scheduler |
| **Gemini CLI** | UNIX socket + LaunchAgent | UNIX socket + systemd | HTTP + Task Scheduler |
| **Codex CLI** | UNIX socket + LaunchAgent | UNIX socket + systemd | HTTP + Task Scheduler |

That's **9 environment combinations** minimum, times Python versions (3.10, 3.12)
= **18 test cells**.

---

## Current State

- **GitHub Actions**: `install-smoke-test.yml` runs ubuntu/macos/windows x
  Python 3.10/3.12 (6 cells) + a Docker container job
- **pytest CI**: `ci.yml` runs same 3x2 matrix with 321 unit tests
- **No AI agent integration tests in CI** (agents are tested via skill file
  validation and hook formatting, not live invocations)
- **No Depot, Lima, or local VM infrastructure** currently configured

---

## Infrastructure Options Evaluated

### 1. GitHub Actions (Current - Recommended as Primary)

**Verdict: Keep as primary CI. Best balance of coverage, cost, and simplicity.**

| Aspect | Details |
|---|---|
| **OS Support** | ubuntu-latest, macos-latest (M1), windows-latest -- all three natively |
| **Pricing** | Free: 2,000 min/mo (Linux), 200 min (macOS, 10x multiplier), 400 min (Windows, 2x). Pro: 3,000 min. Team/Enterprise: higher |
| **macOS** | M1 runners (macos-latest = macos-14+). Intel available via macos-13. Apple Silicon native. |
| **Windows** | Server 2022, full CMD/PowerShell. Docker (Linux containers via WSL2) works. |
| **Docker** | Native on Linux/macOS runners. Linux containers on Windows via WSL2. |
| **Strengths** | Zero setup (already configured), native matrix strategy, free tier sufficient for your scale, integrated with GitHub PRs |
| **Weaknesses** | macOS minutes expensive (10x), no persistent caching across runs by default, shared runner contention |

**Why keep it**: You already have working workflows. The 3x2 matrix covers the
core cases. Adding agent-specific jobs is incremental, not a rewrite.

### 2. Depot (depot.dev) - Recommended as Accelerator

**Verdict: Strong upgrade for speed and macOS cost. Drop-in replacement for GHA runners.**

| Aspect | Details |
|---|---|
| **OS Support** | Linux (x86 + ARM), macOS (M2, 8 CPU/24 GB), Windows (2022, 2025) |
| **Pricing** | $0.004/min Linux (2 CPU), per-second billing. Free tier: 2,000 min/mo. ~40-60% cheaper than GHA for equivalent compute. |
| **Performance** | ~2x faster CPUs (AMD EPYC Genoa), 10x faster cache, RAM-backed disk |
| **Migration** | Change `runs-on: ubuntu-latest` to `runs-on: depot-ubuntu-latest`. That's it. |
| **Strengths** | Persistent layer cache, no 10 GB cache limit, unlimited concurrency, per-second billing |
| **Weaknesses** | **No Docker on Windows runners** (no Hyper-V on EC2), macOS not fully elastic (queue times during peak), macOS only M2 (no Intel) |

**When to adopt**: When your GHA free tier runs out, or when you need faster
macOS builds. The migration is literally a one-line change per job.

### 3. Lima - Not Recommended for CI

**Verdict: Good for local dev testing on macOS. Wrong tool for CI.**

| Aspect | Details |
|---|---|
| **What it is** | Linux VM manager for macOS (like WSL2 for Mac). Uses QEMU/VZ framework. |
| **OS Support** | Runs Linux guests on macOS hosts. No Windows guest. No native Windows host. |
| **Strengths** | Great for "test the Linux path on my Mac" during development |
| **Weaknesses** | macOS-host-only, no Windows guests, not designed for CI pipelines, no Windows or Linux host support |

**Use case**: Keep for local development only. Not a CI solution.

### 4. Docker - Recommended for Linux Isolation Testing

**Verdict: Use for clean-room Linux testing. Not viable for macOS/Windows testing.**

| Aspect | Details |
|---|---|
| **OS Support** | Linux containers everywhere. macOS/Windows containers don't exist in a useful way for CLI testing. |
| **Strengths** | Reproducible environments, test multiple Python versions, test distro variants (Alpine, Debian, Ubuntu) |
| **Weaknesses** | Cannot test macOS or Windows paths. Cannot test LaunchAgent/systemd/Task Scheduler integration. |

**You already have this**: Your `install-smoke-test.yml` has a container job
on `python:3.12-slim`. Keep it for validating the "clean pip install" path.

### 5. Docker Sandboxes (docker.com/products/docker-sandbox) - Recommended for Agent Testing

**Verdict: Best option for testing AI agent interactions in isolation.**

| Aspect | Details |
|---|---|
| **What it is** | MicroVM-based sandboxes purpose-built for AI coding agents |
| **Supported Agents** | Claude Code, Gemini, Codex, Copilot, agent, Kiro -- native support |
| **Isolation** | Each sandbox gets its own kernel (not just a container), private Docker daemon |
| **Strengths** | Built specifically for your use case. Workspace syncing preserves paths. Agent-aware lifecycle management. |
| **Weaknesses** | Linux-only guests, requires Docker Desktop, relatively new product |

**Why this matters**: For testing Forgememo hooks with real agents, Docker
Sandboxes give you isolated, reproducible environments where you can run
`claude -p`, `gemini -p`, and `codex exec` without worrying about agent
side-effects leaking between tests.

### 6. Other Options Evaluated

| Tool | Verdict | Why |
|---|---|---|
| **Cirrus CI** | Viable alternative | Native macOS (M1/M2), Linux, Windows, FreeBSD. Persistent workers. Lower adoption than GHA. |
| **Buildkite** | Overkill | Self-hosted runners, great for large orgs. Too much infra overhead for your scale. |
| **Earthly** | Complementary | Reproducible build definitions (like Dockerfile for CI). Good for complex build steps, but doesn't solve the runner problem. |
| **Dagger** | Complementary | CI pipelines as code (Go/Python/TS). Interesting but adds abstraction layer. Doesn't provide runners. |
| **Tart** | Niche | macOS-only VM tool for CI (by Cirrus Labs). Great if you need macOS VMs specifically. Pairs with Cirrus CI. |
| **Firecracker** | Too low-level | MicroVM hypervisor (powers Lambda/Fargate). Would need significant wrapper tooling. Docker Sandboxes already use this concept. |
| **Namespace.so** | Worth watching | Fast VMs for CI with GPU support. Good for AI workloads. Newer, less battle-tested. |

---

## AI Agent CI Capabilities

### Claude Code in CI

```bash
# Non-interactive mode
claude -p "run forgememo init --provider anthropic" --bare --output-format json

# Key flags
--bare              # Skip auto-discovery (hooks, MCP, CLAUDE.md) -- faster, deterministic
--output-format json # Machine-readable output
--dangerously-skip-permissions  # For isolated CI environments only
--allowedTools "Bash,Read"      # Restrict tool access
```

- **Auth**: `ANTHROPIC_API_KEY` env var (no OAuth in CI)
- **OS**: macOS 10.15+, Linux (Ubuntu 18.04+), Windows 10+ (Git for Windows or WSL)
- **Resources**: 4 GB RAM min, 500 MB disk, network required
- **CI maturity**: High. Official GitHub Actions integration. 60%+ enterprise adoption in CI.

### Gemini CLI in CI

```bash
# Non-interactive mode
gemini -p "run forgememo init --provider gemini" --output-format json
```

- **Auth**: `GEMINI_API_KEY` env var (simplest for CI; OAuth requires browser)
- **OS**: macOS 15+, Windows 11, Ubuntu 20.04+, any OS with Node.js 20+
- **Resources**: Node.js 20+ required, 4 GB RAM, ~150 MB disk
- **CI maturity**: Medium. GitHub Action available but CI story less mature than Claude Code.
- **Free tier**: 60 req/min, 1,000 req/day

### Codex CLI in CI

```bash
# Non-interactive mode
codex exec "run forgememo init --provider openai" --json --full-auto
# Or for fully isolated runners:
codex exec "..." --yolo  # Bypasses all sandboxing
```

- **Auth**: OpenAI API key or ChatGPT Plus/Pro subscription
- **OS**: macOS (full), Linux (full, Landlock kernel 5.13+), Windows (experimental)
- **Resources**: npm install, Rust binary, minimal local overhead
- **CI maturity**: Medium. GitHub Action available. `--yolo` mode designed for CI.
- **Sandbox modes**: `workspace-write` (default), `danger-full-access`

---

## Recommended Architecture

### Tier 1: Core CI (GitHub Actions -- keep current)

What you have works. Enhance it:

```yaml
# .github/workflows/install-smoke-test.yml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python-version: ["3.10", "3.12"]
```

This covers the 6 core cells (3 OS x 2 Python). Keep this as your merge gate.

### Tier 2: Agent Integration Tests (New workflow, Docker Sandboxes)

Add a separate workflow for testing Forgememo with real agents:

```yaml
# .github/workflows/agent-integration.yml
name: Agent Integration Tests
on:
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday 6am UTC
  workflow_dispatch: {}

jobs:
  agent-test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        agent: [claude-code, gemini-cli, codex-cli]
    steps:
      - uses: actions/checkout@v4

      - name: Install Forgememo
        run: pip install -e .

      - name: Start daemon
        run: forgememo start --background

      - name: Test with Claude Code
        if: matrix.agent == 'claude-code'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          claude -p "Run: forgememo search 'test query'" --bare --output-format json

      - name: Test with Gemini CLI
        if: matrix.agent == 'gemini-cli'
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: |
          gemini -p "Run: forgememo search 'test query'" --output-format json

      - name: Test with Codex CLI
        if: matrix.agent == 'codex-cli'
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          codex exec "Run: forgememo search 'test query'" --json --full-auto
```

**Cost note**: These tests consume API credits. Run weekly or on-demand, not on every PR.

### Tier 3: Speed Upgrade (Depot -- when needed)

When GHA minutes become a bottleneck:

```yaml
# One-line change per job:
runs-on: depot-ubuntu-latest  # was: ubuntu-latest
runs-on: depot-macos-latest   # was: macos-latest
runs-on: depot-windows-latest # was: windows-latest
```

### Tier 4: Full Matrix (Future -- when budget allows)

The complete 18-cell matrix with live agent invocations:

```
3 OSes x 3 Agents x 2 Python versions = 18 cells
```

At ~$0.01-0.05 per agent API call + runner minutes, the full matrix costs
roughly $5-15 per run. Run on release tags only.

---

## Decision Matrix

| Criteria | GitHub Actions | Depot | Docker Sandboxes | Lima | Local VMs |
|---|---|---|---|---|---|
| **macOS testing** | Native (M1) | Native (M2) | No | No | QEMU (slow) |
| **Windows testing** | Native | Native | No | No | QEMU (slow) |
| **Linux testing** | Native | Native (faster) | Native | Guest only | Native |
| **Agent isolation** | Process-level | Process-level | MicroVM | VM | VM |
| **Setup complexity** | Already done | 1-line change | Medium | Medium | High |
| **Cost at your scale** | Free tier OK | Free tier OK | Free (Docker Desktop) | Free | Free |
| **CI integration** | Native | Drop-in for GHA | Manual | Manual | Manual |
| **Agent support** | All 3 | All 3 | All 3 (native) | Claude/Gemini/Codex | All 3 |
| **Reproducibility** | High | High | Highest | Medium | Medium |

---

## TL;DR Recommendation

1. **Keep GitHub Actions** as primary CI (you're 80% there already)
2. **Add agent integration tests** as a separate weekly workflow using headless
   modes (`claude -p --bare`, `gemini -p`, `codex exec --full-auto`)
3. **Use Docker Sandboxes** for local agent testing during development (purpose-built for this)
4. **Migrate to Depot runners** when you hit GHA free tier limits (one-line change)
5. **Skip Lima for CI** (keep it for local dev if you like it)
6. **Skip local VMs** (too much infra overhead for diminishing returns)

The complexity you're worried about (3 OS x 3 agents) is best managed as a
**GitHub Actions matrix strategy** with conditional steps, not by introducing
new infrastructure. The infrastructure you have is the right one -- you just
need to extend the test matrix, not replace the platform.
