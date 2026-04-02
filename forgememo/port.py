"""Port discovery for the Forgememo daemon.

Precedence (highest to lowest):
  1. FORGEMEMO_HTTP_PORT environment variable
  2. ~/.forgememo/daemon.port lockfile (written by the daemon on startup)
  3. Default: 5555

The daemon writes the lockfile after binding, and removes it on clean shutdown.
Clients read the lockfile at call time (not module load) so they always see the
current port even if the daemon restarted on a different port.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

_DEFAULT_PORT = 5555
_FORGEMEMO_DIR = Path.home() / ".forgememo"
PORT_FILE = _FORGEMEMO_DIR / "daemon.port"


def read_port() -> int:
    """Return the daemon port using the precedence chain.

    Falls back to the next tier if a value is invalid or the port is not
    actually listening (stale lockfile from a crashed daemon).
    """
    # Tier 1: explicit env var — always trusted even if port is not listening yet
    env_val = os.environ.get("FORGEMEMO_HTTP_PORT", "").strip()
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass

    # Tier 2: lockfile written by the running daemon
    try:
        raw = PORT_FILE.read_text().strip()
        port = int(raw)
        if _port_listening(port):
            return port
    except (FileNotFoundError, ValueError, OSError):
        pass

    # Tier 3: hardcoded default
    return _DEFAULT_PORT


def write_port(port: int) -> None:
    """Write the bound port to the lockfile atomically."""
    _FORGEMEMO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PORT_FILE.with_suffix(".port.tmp")
    tmp.write_text(str(port))
    tmp.replace(PORT_FILE)


def delete_port() -> None:
    """Remove the lockfile on clean daemon shutdown."""
    try:
        PORT_FILE.unlink()
    except FileNotFoundError:
        pass


def _port_listening(port: int) -> bool:
    """Return True if something is accepting connections on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False
