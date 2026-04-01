#!/usr/bin/env python3
"""
Forgememo Daily Scanner — runs nightly via macOS LaunchAgent.

Pass 1: Scans all git repos in ~/Developer for 24h commits → extracts learnings.
Pass 2: Scans ~/.claude/projects/*/memory/*.md files for changes → extracts principles.
        Uses content hashes to skip unchanged files (no duplicate saves).

Logs to ~/Developer/Forgememo/daily_scan.log
"""

import argparse
import contextlib
import hashlib
import json
import subprocess
import sys

if sys.platform != "win32":
    import fcntl
import time
from datetime import datetime
from pathlib import Path

from forgememo import core, inference
from forgememo import config as cfg

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

FORGEMEM_DIR = Path.home() / ".forgememo"
SCAN_ROOT = Path.home()
MEMORY_ROOT = Path.home() / ".claude" / "projects"
LOG_FILE = FORGEMEM_DIR / "daily_scan.log"
FORGEMEM_CLI = FORGEMEM_DIR / "forgemem.py"
HASH_FILE = FORGEMEM_DIR / "md_scan_hashes.json"
PYTHON = sys.executable

# Repos to skip (add any you don't want scanned)
SKIP_DIRS = {"Forgememo", "Forgemem", "node_modules", ".git"}

# Max chars to send to Claude per file (keep costs low)
MAX_LOG_CHARS = 3000
MAX_MD_CHARS = 4000

# Skip index file — it's just pointers, not learnings
SKIP_MD_FILES = {"MEMORY.md"}


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def find_git_repos() -> list[Path]:
    repos = []
    for d in SCAN_ROOT.iterdir():
        if not d.is_dir() or d.name.startswith(".") or d.name in SKIP_DIRS:
            continue
        if (d / ".git").exists():
            repos.append(d)
        else:
            # One level deeper (e.g. ~/Developer/myorg/repo)
            try:
                for sub in d.iterdir():
                    if (
                        sub.is_dir()
                        and (sub / ".git").exists()
                        and sub.name not in SKIP_DIRS
                    ):
                        repos.append(sub)
            except PermissionError:
                pass
    return sorted(repos)


def git_log_since_24h(repo: Path) -> str:
    result = subprocess.run(
        ["git", "log", "--since=24 hours ago", "--oneline", "--stat", "--no-merges"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        log(f"  [WARN] git log failed for {repo.name}: {result.stderr.strip()}")
        return ""
    return result.stdout.strip()


LEARNING_TOOL = {
    "name": "save_learnings",
    "description": "Save extracted learnings as structured traces.",
    "input_schema": {
        "type": "object",
        "properties": {
            "learnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["success", "failure", "plan", "note"],
                        },
                        "content": {
                            "type": "string",
                            "description": "One paragraph with enough context to be useful later",
                        },
                        "principle": {
                            "type": "string",
                            "description": "One concrete actionable sentence — the lasting lesson",
                        },
                        "impact_score": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "type",
                        "content",
                        "principle",
                        "impact_score",
                        "tags",
                    ],
                },
            }
        },
        "required": ["learnings"],
    },
}


def call_haiku_tool(client, prompt: str, max_tokens: int) -> list[dict]:
    """Call Haiku with tool use — guaranteed structured output, no JSON parse errors."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        tools=[LEARNING_TOOL],
        tool_choice={"type": "tool", "name": "save_learnings"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "save_learnings":
            return block.input.get("learnings", [])
    return []


def _extract_via_inference(prompt: str) -> list[dict]:
    """Extract learnings via inference.call() for non-Anthropic providers.
    Uses a JSON-structured prompt since tool_use is Anthropic-specific."""
    json_prompt = (
        prompt + "\n\nRespond with JSON only — no markdown fences, no extra text:\n"
        '{"learnings": [{"type": "success|failure|plan|note", "content": "...", '
        '"principle": "...", "impact_score": 5, "tags": ["..."]}]}'
    )
    try:
        raw = inference.call(json_prompt, max_tokens=1000)
    except SystemExit:
        return []

    # Strip markdown code fences if model wraps anyway
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        items = data.get("learnings", [])
        valid = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in ("success", "failure", "plan", "note"):
                item["type"] = "note"
            if not str(item.get("content", "")).strip():
                continue
            valid.append(item)
        return valid
    except (json.JSONDecodeError, AttributeError) as e:
        log(f"  JSON parse error from inference response: {e}")
        return []


def extract_learnings(project: str, git_log: str) -> list[dict]:
    """Extract learnings from git activity using the configured provider."""
    provider = cfg.get_provider()
    prompt = (
        f'Analyze git commit activity for project "{project}". '
        f"Extract 1-3 meaningful learnings (skip trivial commits/version bumps). "
        f"Focus on bugs fixed (failures), features added (successes), and technical decisions (plans/notes).\n\n"
        f"Git activity (last 24h):\n{git_log[:MAX_LOG_CHARS]}"
    )

    if provider == "anthropic":
        try:
            import anthropic
        except ImportError:
            log("ERROR: pip install anthropic")
            return []
        api_key = cfg.get_api_key("anthropic")
        if not api_key:
            log(
                "ERROR: No Anthropic API key. Run: forgememo config anthropic --key sk-ant-..."
            )
            return []
        client = anthropic.Anthropic(api_key=api_key)
        try:
            return call_haiku_tool(client, prompt, max_tokens=1000)
        except Exception as e:
            log(f"  Claude error for {project}: {e}")
            return []
    else:
        return _extract_via_inference(prompt)


def is_duplicate(content: str, project: str) -> bool:
    """Check if a near-identical learning already exists in the DB (v2 events or legacy traces)."""
    from forgememo.storage import get_conn

    conn = get_conn()
    try:
        fingerprint = content[:120].strip()
        # Check v2 events (scanner_learning events store content in payload JSON)
        try:
            v2_row = conn.execute(
                "SELECT id FROM events WHERE project_id=? AND event_type='scanner_learning' "
                "AND substr(json_extract(payload,'$.content'),1,120)=?",
                (project, fingerprint),
            ).fetchone()
            if v2_row:
                return True
        except Exception:
            pass
        # Check legacy traces
        row = conn.execute(
            "SELECT id FROM traces WHERE project_tag=? AND substr(content,1,120)=?",
            (project, fingerprint),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _post_learning_to_daemon(project: str, learning: dict, session_id: str) -> bool:
    """Post a learning to the daemon as a v2 event. Returns True on success."""
    try:
        from forgememo.hook import _post_event

        event = {
            "session_id": session_id,
            "project_id": project,
            "source_tool": "forgememo-scanner",
            "event_type": "scanner_learning",
            "tool_name": None,
            "payload": json.dumps(
                {
                    "content": learning.get("content", ""),
                    "_principle": learning.get("principle"),
                    "_impact_score": learning.get("impact_score", 5),
                    "_tags": learning.get("tags") or [],
                    "_type": learning.get("type", "note"),
                }
            ),
            "seq": int(time.time() * 1000),
        }
        _post_event(event)
        return True
    except Exception as exc:
        log(f"    Daemon post failed: {exc}")
        return False


def save_to_forgemem(project: str, learning: dict):
    content = learning.get("content", "")
    if is_duplicate(content, project):
        log(f"    skip duplicate: {content[:80]}...")
        return
    session_id = f"daily-scan-{datetime.now().strftime('%Y-%m-%d')}"
    if _post_learning_to_daemon(project, learning, session_id):
        log(f"    Saved (v2): {content[:80]}")
        return
    # Fallback: legacy write path if daemon is not running
    tags_list = learning.get("tags")
    args = argparse.Namespace(
        type=learning.get("type", "note"),
        content=content,
        project=project,
        session=session_id,
        score=learning.get("impact_score", 5),
        principle=learning.get("principle") or None,
        tags=",".join(tags_list) if tags_list else None,
        distill=False,
    )
    try:
        core.cmd_save(args)
        log(f"    Saved (legacy fallback): {content[:80]}")
    except Exception as exc:
        log(f"    Save failed: {exc}")


def main():
    log("=== Forgememo Daily Scan ===")

    provider = cfg.get_provider()
    if provider != "ollama" and not cfg.get_api_key(provider):
        log(
            f"ERROR: No API key configured for provider '{provider}'.\n"
            f"  Run: forgememo config {provider} --key <your-key>\n"
            "  Or use local Ollama (free): forgememo config ollama"
        )
        sys.exit(1)

    repos = find_git_repos()
    log(f"Found {len(repos)} git repos in {SCAN_ROOT}")

    total_saved = 0
    for repo in repos:
        project = repo.name
        git_log = git_log_since_24h(repo)

        if not git_log:
            log(f"  {project}: no commits in last 24h, skipping")
            continue

        commit_count = git_log.count("\n") + 1
        log(f"  {project}: {commit_count} commit line(s) — extracting learnings...")

        learnings = extract_learnings(project, git_log)
        if not learnings:
            log("    No meaningful learnings extracted")
            continue

        for learning in learnings:
            save_to_forgemem(project, learning)
            total_saved += 1

    log(f"Pass 1 done. Saved {total_saved} trace(s) from git activity.")

    # Pass 2: scan .md memory files for changed content
    total_saved += scan_memory_docs()

    log(f"=== Done. Total saved: {total_saved} trace(s) to Forgememo ===\n")


# ── Pass 2: memory doc scanning ──────────────────────────────────────────────


def load_hashes() -> dict:
    if HASH_FILE.exists():
        try:
            return json.loads(HASH_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_hashes(hashes: dict):
    HASH_FILE.write_text(json.dumps(hashes, indent=2))


@contextlib.contextmanager
def locked_hashes():
    """Load, yield, and save md_scan_hashes.json with an exclusive file lock.
    Prevents duplicate processing if two daily_scan.py processes run concurrently."""
    HASH_FILE.touch(exist_ok=True)
    with open(HASH_FILE, "r+") as fh:
        if sys.platform != "win32":
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            raw = fh.read().strip()
            hashes = json.loads(raw) if raw else {}
            yield hashes
            fh.seek(0)
            fh.truncate()
            json.dump(hashes, fh, indent=2)
        finally:
            if sys.platform != "win32":
                fcntl.flock(fh, fcntl.LOCK_UN)


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def project_from_md_path(md_path: Path) -> str:
    """Extract project name from ~/.claude/projects/<slug>/memory/file.md.
    Slug format: -Users-kalinovdameus-Developer[-project-name]
    """
    slug = md_path.parts[-3]  # grandparent dir = project slug
    parts = slug.lstrip("-").split("-")
    try:
        idx = next(i for i, p in enumerate(parts) if p.lower() == "developer")
        remainder = "-".join(parts[idx + 1 :])
        return remainder if remainder else "global"
    except StopIteration:
        return parts[-1] if parts else "global"


def extract_md_learnings(project: str, filename: str, content: str) -> list[dict]:
    """Extract durable learnings from a memory .md file using the configured provider."""
    provider = cfg.get_provider()
    prompt = (
        f'Read memory file "{filename}" for project "{project}". '
        f"Extract 1-4 durable, actionable learnings worth preserving long-term. "
        f"Focus on: technical gotchas/failures/fixes, successful patterns, non-obvious API behaviors. "
        f"Skip: obvious facts, in-progress TODOs, things that will change soon.\n\n"
        f"Memory file content:\n{content[:MAX_MD_CHARS]}"
    )

    if provider == "anthropic":
        try:
            import anthropic
        except ImportError:
            return []
        api_key = cfg.get_api_key("anthropic")
        if not api_key:
            return []
        client = anthropic.Anthropic(api_key=api_key)
        try:
            return call_haiku_tool(client, prompt, max_tokens=1200)
        except Exception as e:
            log(f"  Claude error for {filename}: {e}")
            return []
    else:
        return _extract_via_inference(prompt)


def scan_memory_docs() -> int:
    """Pass 2: scan changed .md memory files and save learnings to Forgememo."""
    log("")
    log("=== Pass 2: Scanning .md memory files ===")

    if not MEMORY_ROOT.exists():
        log("  No ~/.claude/projects/ directory found, skipping.")
        return 0

    total_saved = 0
    files_checked = 0

    with locked_hashes() as hashes:
        for md_file in sorted(MEMORY_ROOT.glob("*/memory/*.md")):
            if md_file.name in SKIP_MD_FILES:
                continue

            files_checked += 1
            content = md_file.read_text(errors="replace").strip()
            if not content:
                continue

            file_key = str(md_file)
            current_hash = md5(content)

            if hashes.get(file_key) == current_hash:
                log(f"  {md_file.name} unchanged, skipping")
                continue

            project = project_from_md_path(md_file)
            log(
                f"  {md_file.name} changed — extracting learnings for project={project}..."
            )

            learnings = extract_md_learnings(project, md_file.name, content)
            if not learnings:
                log("    No durable learnings found")
            else:
                for learning in learnings:
                    save_to_forgemem(project, learning)
                    total_saved += 1

            # Update hash regardless (even if no learnings — don't re-process)
            hashes[file_key] = current_hash
        # Lock released here — hashes written atomically

    log(
        f"Pass 2 done. Checked {files_checked} .md files, saved {total_saved} trace(s)."
    )
    return total_saved


if __name__ == "__main__":
    main()
