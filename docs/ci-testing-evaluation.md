# Cross-Platform CI Testing Evaluation for Forgememo

## Problem Statement

Forgememo is a Python CLI tool (daemon + MCP server) that integrates with 5 AI
coding agents (Claude Code, Gemini CLI, Codex, OpenCode, Copilot) across 3 OSes
(macOS, Linux, Windows). This evaluation is scoped to the 3 agents with
headless CI support (Claude Code, Gemini CLI, Codex) -- OpenCode and Copilot
lack non-interactive CLI modes suitable for automated testing. We need to test
installation, daemon lifecycle, hook integration, and MCP transport across a
**3x3 matrix** (3 OSes x 3 agents):

| | macOS | Linux | Windows |
|---|---|---|---|
| **Claude Code** | UNIX socket + LaunchAgent | UNIX socket + systemd | HTTP + Task Scheduler |
| **Gemini CLI** | UNIX socket + LaunchAgent | UNIX socket + systemd | HTTP + Task Scheduler |
| **Codex CLI** | UNIX socket + LaunchAgent | UNIX socket + systemd | HTTP + Task Scheduler |

That's **9 environment combinations** minimum, times Python versions (3.10, 3.12)
= **18 test cells**.

---

## Current State

- **GitHub Actions**: `install-smoke-test.yml` runs ubuntu/macOS/windows x
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

### 6. Cirrus Runners - Strong Alternative for macOS

**Verdict: Best option if macOS cost is your main pain point.**

| Aspect | Details |
|---|---|
| **What it is** | Drop-in GitHub Actions runner replacement by Cirrus Labs (makers of Tart) |
| **OS Support** | Linux (x64, arm64), macOS (M4 Pro -- fastest available), Windows |
| **Pricing** | **$150/month per concurrent runner** (flat, unlimited minutes). Free for public repos. |
| **macOS speed** | M4 Pro chips, 2-3x faster than GitHub-hosted runners. Powered by Tart (Apple Virtualization.framework) |
| **Migration** | Single-line change: `runs-on: ghcr.io/cirruslabs/macos-sequoia-xcode:latest` |
| **Adoption** | Bitcoin Core uses Cirrus for Linux CI. Ranked #1 for x64 CI performance in benchmarks. |

**Why this matters for you**: GitHub charges 10x for macOS minutes. At $150/mo
flat for unlimited macOS CI, Cirrus Runners eliminate the macOS cost problem
entirely. If you're running 15+ macOS CI minutes per day, Cirrus pays for itself.

### 7. Other Options Evaluated

| Tool | Verdict | Why |
|---|---|---|
| **Buildkite** | Overkill | Self-hosted runners, great for large orgs. Too much infra overhead for your scale. |
| **Earthly** | Complementary | Reproducible build definitions (like Dockerfile for CI). Good for complex build steps, but doesn't solve the runner problem. Company pivoted to "Lunar" -- Earthfiles maintained but no longer primary focus. |
| **Dagger** | Complementary | CI pipelines as code (Go/Python/TS). All steps run in OCI containers (Linux only). Cannot test native macOS/Windows. |
| **Tart** | Niche | macOS VM tool by Cirrus Labs. Near-native speed via Apple Virtualization.framework. Great if you self-host on Apple Silicon hardware. Powers Cirrus Runners under the hood. |
| **Firecracker** | Too low-level | MicroVM hypervisor (powers Lambda/Fargate). Linux guests only. Docker Sandboxes already use this concept. |
| **Namespace.so** | Worth watching | Fast VMs for CI with GPU support. macOS M4 runners at $0.18-0.36/min. Good for AI workloads. |
| **RunsOn** | Budget option | EUR 300/yr + AWS costs. Linux/Windows on AWS. No macOS (AWS 24hr Mac reservation makes it impractical). |

### Runner Provider Comparison

| Provider | Linux | macOS | Windows | Pricing Model |
|---|---|---|---|---|
| **GitHub Actions** | x64, arm64 | M1 (arm64) | x64 | Per-minute (macOS 10x) |
| **Depot** | x64, arm64 | M2 (arm64) | x64 | Per-second, ~40-60% cheaper |
| **Cirrus Runners** | x64, arm64 | M4 Pro (arm64) | x64 | $150/mo flat per runner |
| **Namespace** | Yes | M4 | Yes | Per-minute |
| **RunsOn** | x64, arm64 | No | Yes | EUR 300/yr + AWS |

> **Note on macOS Intel**: GitHub is deprecating Intel macOS runners (macos-13
> retiring Fall 2027). All macOS CI is moving to Apple Silicon. This doesn't
> affect Forgememo since Python is architecture-agnostic, but be aware if you
> add native extensions later.

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
--append-system-prompt "..."    # Inject custom review/test instructions
--session-id / --resume         # Multi-turn CI sessions
```

- **Install**: `curl -fsSL https://claude.ai/install.sh | bash` (native, no Node.js) or `npm i -g @anthropic-ai/claude-code@<pinned-version>` (Node.js 18+)
- **Auth**: `ANTHROPIC_API_KEY` env var (or Bedrock/Vertex/Foundry credentials)
- **OS**: macOS 13+, Linux 64-bit (Ubuntu/Debian/Fedora; Alpine needs libgcc, libstdc++, ripgrep), Windows 11 (WSL 1/2 or native PowerShell preview)
- **Resources**: 4 GB RAM min, 500 MB disk, network required. All inference is server-side.
- **CI maturity**: High. Official GitHub Actions + GitLab CI integrations. Agent SDK in Python + TypeScript. 60%+ enterprise CI adoption.
- **Sandbox**: Native OS-level filesystem + network isolation. Docker Sandboxes (Desktop 4.60+) for microVM isolation.
- **Caveats**: Interactive skills (`/commit`, `/review`) unavailable in `-p` mode. Requires Pro/Max/Teams/Enterprise/Console account.

### Gemini CLI in CI

```bash
# Non-interactive mode
gemini -p "run forgememo init --provider gemini" --output-format json
```

- **Install**: `npm i -g @google/gemini-cli@<pinned-version>` (Node.js 20+ required). Also via Homebrew, MacPorts, Anaconda.
- **Auth**: `GEMINI_API_KEY` env var (simplest for CI; OAuth requires browser). Vertex AI via `GOOGLE_API_KEY` + `GOOGLE_GENAI_USE_VERTEXAI=true`.
- **OS**: macOS 15+, Ubuntu 20.04+, Windows 11 24H2+, any OS with Node.js 20+
- **Resources**: 4 GB RAM (16 GB recommended for large codebases), ~150 MB disk
- **CI maturity**: Medium. Official GitHub Action available. CI story still maturing vs Claude Code.
- **Free tier**: 60 req/min, 1,000 req/day. 1M token context window (Gemini 2.5 Pro).
- **Sandbox**: Trusted Folders system. Docker Sandboxes support. Dockerfile in official repo.
- **Caveats**: May need human guidance at key decision points (less autonomous in benchmarks). API key auth required for CI (OAuth is interactive).

### Codex CLI in CI

```bash
# Non-interactive mode
codex exec "run forgememo init --provider openai" --json --full-auto
# Or for fully isolated runners:
codex exec "..." --yolo  # Bypasses all sandboxing and approval
```

- **Install**: `npm i -g @openai/codex@<pinned-version>` (Node.js 22+ LTS, built in Rust). Git 2.23+ recommended.
- **Auth**: OpenAI API key or ChatGPT Plus/Pro/Business/Edu/Enterprise subscription
- **OS**: macOS 12+, Ubuntu 20.04+/Debian 10+, Windows 11 via WSL 2 (experimental native Windows with elevated/unelevated sandbox modes)
- **Resources**: 4 GB RAM min (8 GB recommended), minimal local overhead. Known issue: heavy concurrent sessions can cause memory pressure.
- **CI maturity**: Medium. Official GitHub Action. `--yolo` mode designed for isolated CI.
- **Sandbox modes**: `workspace-write` (default, no network), `danger-full-access`. Linux uses `bubblewrap`. Enterprise: `requirements.toml` enforces policies org-wide.
- **Caveats**: Windows support experimental. AppArmor distros may need `kernel.apparmor_restrict_unprivileged_userns=0`. Memory pressure under concurrent usage.

### Agent Comparison Matrix

| Feature | Claude Code | Gemini CLI | Codex CLI |
|---|---|---|---|
| **Non-interactive flag** | `-p` / `--print` | `-p` / `--prompt` | `codex exec` / `codex e` |
| **Bare/fast CI mode** | `--bare` | N/A | N/A |
| **Output formats** | text, json, stream-json | json, stream-json | json (`--json`) |
| **Skip permissions** | `--dangerously-skip-permissions` | N/A | `--yolo` |
| **Node.js requirement** | None (native) or 18+ (npm) | 20+ | 22+ |
| **Min RAM** | 4 GB | 4 GB (16 GB rec.) | 4 GB (8 GB rec.) |
| **Docker Sandboxes** | Yes (microVM) | Yes (microVM) | Yes (microVM) |
| **Official GitHub Action** | Yes | Yes | Yes |
| **Agent SDK** | Python + TypeScript | Developing | TypeScript |
| **License/Cost** | Pro/Max/Teams/Enterprise | Apache 2.0 (free: 1K req/day) | Plus/Pro/Business/Enterprise |
| **Context window** | 200K tokens | 1M tokens | Model-dependent |

### Practical CI Recommendations

1. **Use `--bare` (Claude) or equivalent minimal flags** for reproducibility
2. **Store API keys as CI secrets** -- never hardcode
3. **Run inside Docker Sandboxes or microVMs** when using `--dangerously-skip-permissions` / `--yolo`
4. **Limit tool access** with `--allowedTools` (Claude) to reduce blast radius
5. **Use JSON output** for machine-parseable results in pipeline steps
6. **Set timeouts**: agents can loop -- wrap calls with CI-level timeouts
7. **Pin CLI versions** in CI to avoid breaking changes
8. **Alpine Linux**: install libgcc, libstdc++, ripgrep for Claude Code; ensure Node.js 20+ for Gemini; 22+ for Codex

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

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "22"

      - name: Install Forgememo
        run: pip install -e .

      - name: Install Claude Code CLI
        if: matrix.agent == 'claude-code'
        run: npm i -g @anthropic-ai/claude-code@<pinned-version>

      - name: Install Gemini CLI
        if: matrix.agent == 'gemini-cli'
        run: npm i -g @google/gemini-cli@<pinned-version>

      - name: Install Codex CLI
        if: matrix.agent == 'codex-cli'
        run: npm i -g @openai/codex@<pinned-version>

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
# One-line change per job (example):

# job: test-linux
runs-on: depot-ubuntu-latest  # was: ubuntu-latest

# job: test-macos
runs-on: depot-macos-latest   # was: macos-latest

# job: test-windows
runs-on: depot-windows-latest # was: windows-latest
```

### Tier 4: Full Matrix (Future -- when budget allows)

The complete 18-cell matrix with live agent invocations:

```text
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
