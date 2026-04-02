#!/usr/bin/env python3
"""
Forgememo hook adapter — normalize tool events and POST to daemon.

Usage:
  echo '{...}' | python forgememo/hook.py post_tool_use
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

import requests

from forgememo.port import read_port

DAEMON_URL = os.environ.get("FORGEMEMO_DAEMON_URL")
SOCKET_PATH = os.environ.get(
    "FORGEMEMO_SOCKET", os.path.join(tempfile.gettempdir(), "forgememo.sock")
)
SOURCE_TOOL = os.environ.get("FORGEMEMO_SOURCE_TOOL", "unknown")


def _http_port() -> str:
    """Return the current daemon port as a string, using the discovery chain."""
    return str(read_port())

_PRIVATE_RE = None


def _ensure_daemon() -> bool:
    """Check daemon health; auto-restart if unreachable. Returns True if alive."""
    from forgememo.daemon import wait_for_port

    port = _http_port()
    url = f"http://127.0.0.1:{port}/health"
    try:
        requests.get(url, timeout=1).raise_for_status()
        return True
    except Exception:
        pass
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "forgememo.daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if wait_for_port("127.0.0.1", int(port), timeout=10, proc=proc):
            try:
                requests.get(url, timeout=2).raise_for_status()
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _compile_private_re():
    import re

    return re.compile(r"<private>.*?</private>", re.DOTALL | re.IGNORECASE)


def strip_private(obj: Any):
    """Recursively strip <private>...</private> from any string in a dict/list."""
    global _PRIVATE_RE
    if _PRIVATE_RE is None:
        _PRIVATE_RE = _compile_private_re()
    if isinstance(obj, str):
        return _PRIVATE_RE.sub("", obj).strip()
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def _read_stdin_json() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _resolve_project_id(payload: dict) -> str:
    override = os.environ.get("FORGEMEMO_PROJECT_ID")
    if override:
        return override
    if payload.get("project_id"):
        return str(payload["project_id"])
    if payload.get("cwd"):
        return os.path.realpath(str(payload["cwd"]))
    return os.path.realpath(os.getcwd())


def _normalize_event(event_name: str, payload: dict) -> dict:
    session_id = payload.get("session_id") or payload.get("session") or "unknown"
    project_id = _resolve_project_id(payload)
    event_type = payload.get("hook_event_name") or event_name
    tool_name = payload.get("tool_name")
    source_tool = payload.get("source_tool") or SOURCE_TOOL
    seq = payload.get("seq") or payload.get("sequence")
    if seq is None:
        seq = int(time.time() * 1000)

    event = {
        "session_id": session_id,
        "project_id": project_id,
        "source_tool": source_tool,
        "event_type": event_type,
        "tool_name": tool_name,
        "payload": payload,
        "seq": int(seq),
    }
    return strip_private(event)


def _post_event(event: dict) -> None:
    # Daemon expects payload as JSON string
    event = dict(event)
    event["payload"] = json.dumps(event["payload"])

    # Socket-first (requests-unixsocket if available, POSIX only)
    if not DAEMON_URL and sys.platform != "win32":
        try:
            import requests_unixsocket

            session = requests_unixsocket.Session()
            socket_url = "http+unix://" + SOCKET_PATH.replace("/", "%2F")
            session.post(f"{socket_url}/events", json=event, timeout=1.5)
            return
        except Exception:
            pass

    # Fallback HTTP
    try:
        url = DAEMON_URL.rstrip("/") if DAEMON_URL else f"http://127.0.0.1:{_http_port()}"
        requests.post(f"{url}/events", json=event, timeout=1.5)
    except Exception:
        # Hook must never crash the host process
        pass


def _daemon_get(path: str, params: dict | None = None) -> dict:
    """GET from daemon — never raises (hook must not crash host process)."""
    if not DAEMON_URL and sys.platform != "win32":
        try:
            import requests_unixsocket

            session = requests_unixsocket.Session()
            socket_url = "http+unix://" + SOCKET_PATH.replace("/", "%2F")
            resp = session.get(f"{socket_url}{path}", params=params, timeout=3)
            if resp.ok:
                return resp.json()
        except Exception:
            pass
    try:
        url = DAEMON_URL.rstrip("/") if DAEMON_URL else f"http://127.0.0.1:{_http_port()}"
        resp = requests.get(f"{url}{path}", params=params, timeout=3)
        return resp.json() if resp.ok else {}
    except Exception:
        return {}


def _daemon_post(path: str, data: dict) -> dict:
    """POST to daemon — never raises (hook must not crash host process)."""
    if not DAEMON_URL and sys.platform != "win32":
        try:
            import requests_unixsocket

            session = requests_unixsocket.Session()
            socket_url = "http+unix://" + SOCKET_PATH.replace("/", "%2F")
            resp = session.post(f"{socket_url}{path}", json=data, timeout=3)
            if resp.ok:
                return resp.json()
        except Exception:
            pass
    try:
        url = DAEMON_URL.rstrip("/") if DAEMON_URL else f"http://127.0.0.1:{_http_port()}"
        resp = requests.post(f"{url}{path}", json=data, timeout=3)
        return resp.json() if resp.ok else {}
    except Exception:
        return {}


_EXIT_CODE_SIGNALS = frozenset(
    {"SIGINT", "SIGTERM", "SIGKILL", "SIGQUIT", "SIGABRT", "SIGHUP"}
)

_EXIT_CODE_NUMERIC = re.compile(r"^-?\d+$")


def _is_signal(exit_code: str) -> bool:
    """Check if exit_code is a signal name like SIGINT, SIGTERM."""
    return exit_code.upper() in _EXIT_CODE_SIGNALS


def _is_cancelled_signal(exit_code: str) -> bool:
    """Check if exit_code indicates user cancellation."""
    lower = str(exit_code).lower()
    return lower in {
        "sigint",
        "cancelled",
        "canceled",
        "keyboard interrupt",
        "keyboardinterrupt",
        "user cancelled",
        "user canceled",
    }


def _parse_exit_code(exit_code: Any) -> tuple[int | None, bool, bool]:
    """Parse exit code and return (numeric_value, is_error, is_cancelled).

    Returns:
        - numeric_value: int if parseable, None otherwise
        - is_error: True if non-zero or signal
        - is_cancelled: True if user-initiated cancellation
    """
    if exit_code is None:
        return None, False, False

    code_str = str(exit_code).strip()

    if _is_signal(code_str):
        return None, True, code_str.upper() == "SIGINT"

    if _is_cancelled_signal(code_str):
        return None, True, True

    if _EXIT_CODE_NUMERIC.match(code_str):
        try:
            val = int(code_str)
            # Negative values are POSIX signal codes (e.g. -2=SIGINT, -15=SIGTERM).
            # -2 (SIGINT) is user cancellation; others are errors.
            if val < 0:
                is_cancelled = val == -2  # -2 = SIGINT on POSIX
                return val, True, is_cancelled
            return val, val != 0, False
        except ValueError:
            pass

    return None, True, False


_ERROR_PATTERNS = re.compile(
    r"(?:"
    r"Traceback \(most recent call last\)"
    r"|(?:^|\n)\s*(?:Error|ERROR|error)[\s:[]"
    r"|(?:^|\n)\s*(?:FAILED|FAIL)\b"
    r"|exit (?:code|status)\s*[1-9]"
    r"|CalledProcessError"
    r"|ModuleNotFoundError"
    r"|ImportError"
    r"|SyntaxError"
    r"|TypeError"
    r"|ValueError"
    r"|KeyError"
    r"|AttributeError"
    r"|NameError"
    r"|FileNotFoundError"
    r"|PermissionError"
    r"|RuntimeError"
    r"|OSError"
    r"|ConnectionError"
    r"|TimeoutError"
    r"|command not found"
    r"|No such file or directory"
    r"|npm ERR!"
    r"|Cannot find module"
    r"|Compilation failed"
    r"|Build failed"
    r"|undefined is not"
    r"|is not defined"
    r"|segmentation fault"
    r"|panic:"
    r"|fatal:"
    r"|command interrupted"
    r")",
    re.IGNORECASE,
)

_FINGERPRINT_NOISE = re.compile(
    r"(?:"
    r"0x[0-9a-fA-F]+"
    r"|line \d+"
    r"|:\d+(?::\d+)?"
    r"|/[\w./-]+"
    r"|[A-Za-z]:\\[\w.\\-]+"
    r"|\.{1,2}/[\w./-]+"
    r"|<private>.*?</private>"
    r"|\b\d{10,}\b"
    r"|\b[0-9a-f]{8,}\b"
    r"|\bat \w+\s*\(.*?\)"
    r")",
    re.DOTALL,
)


def _extract_error_text(payload: dict) -> str | None:
    result = (
        payload.get("tool_response")
        or payload.get("tool_output")
        or payload.get("tool_result")
        or payload.get("toolResult")
        or ""
    )
    if isinstance(result, dict):
        parts = []
        has_explicit_error = False
        is_cancelled = False
        if result.get("error"):
            parts.append(str(result["error"]))
            has_explicit_error = True
        if result.get("stderr"):
            parts.append(str(result["stderr"]))
        if result.get("stdout"):
            parts.append(str(result["stdout"]))
        if result.get("content"):
            parts.append(str(result["content"]))
        if result.get("output"):
            parts.append(str(result["output"]))
        exit_code = result.get("exitCode") or result.get("exit_code")
        if exit_code:
            exit_code_int, is_error, is_cancelled = _parse_exit_code(exit_code)
            if exit_code_int is not None and exit_code_int != 0:
                parts.append(f"exit code {exit_code_int}")
                has_explicit_error = True
            elif is_error:
                if is_cancelled:
                    parts.append("command cancelled")
                else:
                    parts.append(f"exit code {exit_code}")
                    has_explicit_error = True
        rci = result.get("returnCodeInterpretation")
        if rci and isinstance(rci, str) and "error" in rci.lower():
            parts.append(rci)
        if result.get("interrupted"):
            parts.append("command interrupted")
            has_explicit_error = True
            is_cancelled = True
        if exit_code:
            _, is_error, was_cancelled = _parse_exit_code(exit_code)
            if is_error and not was_cancelled:
                has_explicit_error = True
        result = "\n".join(parts)
        if has_explicit_error and result:
            return result
    elif not isinstance(result, str):
        result = str(result)
    if not result:
        return None
    if _ERROR_PATTERNS.search(result):
        return result
    return None


def _error_fingerprint(error_text: str) -> str:
    lines = error_text.strip().splitlines()
    key_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _ERROR_PATTERNS.search(stripped):
            key_lines.append(stripped)
        if len(key_lines) >= 3:
            break
    if not key_lines:
        key_lines = [lines[0]] if lines else ["unknown"]
    core = "\n".join(key_lines)
    core = _FINGERPRINT_NOISE.sub("", core)
    core = re.sub(r"\s+", " ", core).strip().lower()
    return hashlib.sha256(core.encode()).hexdigest()[:16]


def _extract_error_keywords(error_text: str) -> str:
    lines = error_text.strip().splitlines()
    key_lines = []
    for line in lines:
        stripped = line.strip()
        if _ERROR_PATTERNS.search(stripped):
            key_lines.append(stripped)
        if len(key_lines) >= 3:
            break
    if not key_lines:
        key_lines = lines[:2]
    text = " ".join(key_lines)
    text = _FINGERPRINT_NOISE.sub("", text)
    words = [w for w in re.findall(r"[a-zA-Z_]\w{2,}", text)]
    seen = set()
    unique = []
    for w in words:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            unique.append(w)
    return " ".join(unique[:12])


_ERROR_RECALL_DEBOUNCE_SECS = int(
    os.environ.get("FORGEMEMO_ERROR_DEBOUNCE_SECS", "300")
)


def _is_within_debounce(last_ts: str | None) -> bool:
    if not last_ts:
        return False
    try:
        from datetime import datetime, timezone

        last_dt = datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return elapsed < _ERROR_RECALL_DEBOUNCE_SECS
    except Exception:
        return False


def _format_context_json(text: str, event_name: str) -> str:
    """Return platform-appropriate JSON for context injection."""
    if SOURCE_TOOL in ("claude-code", "gemini"):
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": text,
                }
            }
        )
    return json.dumps({"systemMessage": text})


def _handle_session_recall(payload: dict, event_name: str) -> int:
    """Fetch recent memories and inject them as context on session start."""
    if not _ensure_daemon():
        print(
            _format_context_json(
                "Forgememo daemon unreachable — run: forgememo start", event_name
            )
        )
        return 0
    project_id = _resolve_project_id(payload)
    summaries = _daemon_get("/session_summaries", {"project_id": project_id, "k": 2})
    recent = _daemon_get("/recent", {"project_id": project_id, "k": 5})

    parts = []
    for s in summaries.get("results", []):
        ts = (s.get("ts") or "")[:10]
        parts.append(
            f"[Session {ts}] {s.get('request', '')} — {s.get('learnings', '')}"
        )
    for r in recent.get("results", []):
        narrative = (r.get("narrative") or r.get("excerpt") or "")[:120]
        parts.append(f"[Memory] {r.get('title', '')}: {narrative}")

    if not parts:
        print(_format_context_json("", event_name))
        return 0

    context = "Forgememo context from previous sessions:\n" + "\n".join(parts)
    print(_format_context_json(context, event_name))
    return 0


def _handle_session_end(payload: dict) -> int:
    """Spawn background end-session synthesis; return immediately."""
    _ensure_daemon()  # best-effort; background subprocess needs daemon up
    session_id = payload.get("session_id") or ""
    cwd = payload.get("cwd") or os.getcwd()
    import shutil as _shutil

    forgememo_bin = _shutil.which("forgememo")
    if not forgememo_bin:
        print(json.dumps({}))
        return 0
    cmd = [
        forgememo_bin,
        "end-session",
        "--session-id",
        session_id,
        "--project-dir",
        cwd,
    ]
    try:
        if sys.platform == "win32":
            env = {**os.environ, "FORGEMEMO_HTTP_PORT": _http_port()}
            subprocess.Popen(
                cmd,
                env=env,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass
    print(json.dumps({}))
    return 0


# ---------------------------------------------------------------------------
# Event dispatch tables
# ---------------------------------------------------------------------------

_SESSION_RECALL_EVENTS = {
    "UserPromptSubmit",  # Claude Code, Codex
    "BeforeAgent",  # Gemini
    "sessionStart",  # Copilot
    "session.created",  # OpenCode
    "SessionStart",  # generic
}

_SESSION_END_EVENTS = {
    "Stop",  # Claude Code, Codex
    "SessionEnd",  # Claude Code, Gemini
    "AfterAgent",  # Gemini (per-turn fallback)
    "agentStop",  # Copilot
    "session.idle",  # OpenCode (agent finished)
    "session.deleted",  # OpenCode (session closed)
}

_WRITE_TOOL_NAMES = {"Edit", "Write", "Bash", "NotebookEdit", "MultiEdit"}

_POST_TOOL_USE_EVENTS = {
    "PostToolUse",  # Claude Code, Codex
    "AfterTool",  # Gemini
    "tool.done",  # OpenCode
}


def _extract_tool_content(tool_name: str, payload: dict) -> str:
    """Build a human-readable content string from a tool payload."""
    ti = payload.get("tool_input") or {}
    tr = payload.get("tool_response") or {}
    out = (tr.get("output") or "").strip()[:400]
    if tool_name == "Bash":
        cmd = (ti.get("command") or "").strip()
        return f"$ {cmd}\n{out}" if out else f"$ {cmd}"
    if tool_name in ("Edit", "MultiEdit"):
        path = ti.get("file_path") or ti.get("path") or ""
        old = (ti.get("old_string") or "")[:80].replace("\n", " ")
        new = (ti.get("new_string") or "")[:80].replace("\n", " ")
        return f"Edit {path}: -{old!r} +{new!r}" if old else f"Edit {path}"
    if tool_name == "Write":
        path = ti.get("file_path") or ""
        content = (ti.get("content") or "")[:300]
        return f"Write {path}: {content}"
    if tool_name == "NotebookEdit":
        path = ti.get("notebook_path") or ""
        return f"NotebookEdit {path}: {out}"
    return out or tool_name


def _handle_post_tool_use(payload: dict, event_name: str) -> int:
    """Post write-op tool events to daemon; silently skip read-only tools."""
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    if tool_name not in _WRITE_TOOL_NAMES:
        return 0
    # Enrich payload with extracted content so FTS and recall have something useful
    enriched = dict(payload)
    enriched.setdefault("content", _extract_tool_content(tool_name, payload))
    event = _normalize_event(event_name, enriched)
    _post_event(event)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python forgememo/hook.py <event_name>", file=sys.stderr)
        return 2
    event_name = sys.argv[1]
    try:
        payload = _read_stdin_json()
    except Exception as e:
        print(f"Invalid JSON payload: {e}", file=sys.stderr)
        return 1

    if event_name in _SESSION_RECALL_EVENTS:
        return _handle_session_recall(payload, event_name)
    if event_name in _SESSION_END_EVENTS:
        return _handle_session_end(payload)
    if event_name in _POST_TOOL_USE_EVENTS:
        return _handle_post_tool_use(payload, event_name)

    event = _normalize_event(event_name, payload)
    _post_event(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
