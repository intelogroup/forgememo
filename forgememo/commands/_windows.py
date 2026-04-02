"""Windows-specific daemon lifecycle helpers.

Extracted from lifecycle.py so the platform-specific logic is isolated,
independently testable, and doesn't clutter the cross-platform flow.

Design decisions:
- No schtasks: PID-based tracking matches the Linux approach.
- log_fd instead of DEVNULL: Flask/Werkzeug writes to stderr on bind errors;
  DEVNULL silently swallows those, making failures invisible.
- CREATE_NO_WINDOW: py.exe launcher bug (cpython#85785) means DETACHED_PROCESS
  alone can still flash a console; CREATE_NO_WINDOW prevents it and also
  ensures the daemon survives the parent PowerShell/CMD window closing.
- CREATE_BREAKAWAY_FROM_JOB: Windows 8+ places child processes in the parent's
  Job Object by default. When the parent exits, all Job Object members are
  terminated — regardless of DETACHED_PROCESS. Breaking out of the Job Object
  prevents this. Falls back silently if the Job Object was created without
  JOB_OBJECT_LIMIT_BREAKAWAY_OK.
- _win_pid_alive uses ctypes.OpenProcess: os.kill(pid, 0) is not supported on
  Windows (Python bug tracker #14480 — no POSIX signal-0 equivalent).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import time
from pathlib import Path

from rich.console import Console

from forgememo.port import delete_pid, read_pid

console = Console()

# PROCESS_QUERY_LIMITED_INFORMATION — enough to check if a PID is alive
# without requiring elevated privileges when querying same-user processes.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# CREATE_BREAKAWAY_FROM_JOB — Windows API flag, not exposed in subprocess module.
# Allows the child process to escape the parent's Job Object so it survives
# after the parent exits. Requires JOB_OBJECT_LIMIT_BREAKAWAY_OK on the Job.
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _win_log_path() -> Path:
    """Return the daemon log path (same location daemon.py uses by default)."""
    return Path.home() / ".forgememo" / "logs" / "forgememo_daemon.log"


def _win_pid_alive(pid: int) -> bool:
    """Return True if the given PID refers to a running process.

    Uses ctypes OpenProcess instead of os.kill(pid, 0) — the latter is not
    supported on Windows (only CTRL_C_EVENT / CTRL_BREAK_EVENT are valid).
    """
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _tail_log(n: int = 50) -> str:
    """Return the last n lines of the daemon log, or an empty string."""
    log = _win_log_path()
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except (FileNotFoundError, OSError):
        return ""


def _print_crash_diagnostic() -> None:
    tail = _tail_log(100)
    console.print("[bold red]Daemon crashed during startup.[/]")
    console.print("-" * 40)
    if tail:
        console.print(tail)
    else:
        console.print("[dim](log file not found or empty)[/]")
    console.print("-" * 40)
    console.print(f"[dim]Full log: {_win_log_path()}[/]")


def _win_start_daemon(http_port: str, py: str) -> subprocess.Popen:
    """Spawn the daemon as a detached Windows process.

    Key differences from the old approach:
    - stdout/stderr → log file (not DEVNULL): Flask writes to stderr on bind
      errors; DEVNULL makes those invisible, causing silent crashes.
    - FORGEMEMO_LOG_STDERR=0: tells daemon.py to skip its StreamHandler so
      there's no double-write attempt to a potentially closed console handle.
    - CREATE_NO_WINDOW: prevents console flash and survives parent window close.
    - close_fds=False: required so the child inherits the open log_fd handle.
      The parent closes log_fd immediately after Popen returns.
    """
    log = _win_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["FORGEMEMO_HTTP_PORT"] = http_port
    env["FORGEMEMO_LOG_STDERR"] = "0"

    log_fd = open(log, "a", encoding="utf-8")
    try:
        _base_flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
        _popen_kwargs: dict = dict(
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            close_fds=False,  # child must inherit log_fd
        )
        try:
            proc = subprocess.Popen(
                [py, "-m", "forgememo", "daemon"],
                creationflags=_base_flags | _CREATE_BREAKAWAY_FROM_JOB,
                **_popen_kwargs,
            )
        except OSError:
            # Job Object was created without JOB_OBJECT_LIMIT_BREAKAWAY_OK;
            # fall back without breakaway — daemon may be killed on parent exit.
            console.print(
                "[yellow]Note: CREATE_BREAKAWAY_FROM_JOB not available, "
                "daemon may be killed when parent exits.[/]"
            )
            proc = subprocess.Popen(
                [py, "-m", "forgememo", "daemon"],
                creationflags=_base_flags,
                **_popen_kwargs,
            )
    finally:
        log_fd.close()  # parent no longer needs the handle

    return proc


def _win_health_check(
    http_port: str,
    proc: subprocess.Popen,
    timeout: int = 20,
) -> bool:
    """Poll /health until the daemon is ready, then run a stabilization window.

    Returns True only after:
    1. The /health endpoint responds with ok=true.
    2. The process remains alive for a 10-second stabilization window.
    3. The port is still listening after the stabilization window.

    Prints a crash diagnostic (log tail) and returns False on any failure.
    """
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{http_port}/health"

    # Phase 1: wait for the first healthy response
    healthy = False
    for _ in range(timeout):
        if proc.poll() is not None:
            console.print(
                f"[red]Daemon process exited (code {proc.returncode}) before becoming healthy.[/]"
            )
            _print_crash_diagnostic()
            return False
        try:
            resp = urllib.request.urlopen(url, timeout=1)
            import json as _json

            data = _json.loads(resp.read())
            if data.get("ok"):
                healthy = True
                break
        except Exception:
            pass
        time.sleep(1)

    if not healthy:
        console.print("[yellow]Daemon health check timed out.[/]")
        _print_crash_diagnostic()
        return False

    # Phase 2: stabilization — confirm the process stays alive for 10 seconds.
    # Longer window catches late Job Object kills after parent exits.
    for _ in range(20):  # 20 × 0.5s = 10s
        time.sleep(0.5)
        if not _win_pid_alive(proc.pid):
            console.print("[red]Daemon exited during stabilization window.[/]")
            _print_crash_diagnostic()
            return False

    # Phase 3: re-verify port is still listening after stabilization.
    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.settimeout(2)
        if s.connect_ex(("127.0.0.1", int(http_port))) != 0:
            console.print(
                "[red]Daemon PID alive but port closed after stabilization.[/]"
            )
            _print_crash_diagnostic()
            return False

    return True


def _win_stop_daemon(http_port: str) -> None:
    """Stop the Windows daemon using the PID lockfile.

    Uses targeted 'taskkill /pid' instead of '/im forgememo.exe' to avoid
    killing unrelated Python processes that happen to share the name.
    """
    pid = read_pid()
    if pid is None:
        console.print("[dim]No PID file found — daemon may not be running.[/]")
        return

    if not _win_pid_alive(pid):
        console.print(f"[dim]PID {pid} not running (stale lockfile).[/]")
        delete_pid()
        return

    result = subprocess.run(
        ["taskkill", "/f", "/pid", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        console.print(f"[green]Daemon stopped[/] (PID {pid}).")
    else:
        console.print(
            f"[yellow]taskkill returned non-zero for PID {pid}:[/] {result.stderr.strip()}"
        )

    delete_pid()
