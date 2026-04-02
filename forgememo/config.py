"""
Forgememo provider configuration.
Stored at ~/.forgemem/config.json — never sent to Forgememo servers.
"""

import datetime
import json
import os
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("FORGEMEM_CONFIG", Path.home() / ".forgemem" / "config.json")
)
CREDITS_FLAG_PATH = CONFIG_PATH.parent / ".credits_exhausted"

SUPPORTED_PROVIDERS = (
    "anthropic",
    "openai",
    "gemini",
    "ollama",
    "claude_code",
    "forgememo",
)

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "ollama": "llama3.2",  # auto-detected from running instance if available
    "claude_code": "claude_code",  # model selected by the claude CLI / user's plan
    "forgememo": "claude-haiku-4-5-20251001",  # managed — model chosen server-side
}

OLLAMA_DEFAULT_URL = "http://localhost:11434"


def load() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(CONFIG_PATH.parent, 0o700)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get_provider() -> str:
    return load().get("provider", "anthropic")


def get_api_key(provider: str) -> str | None:
    """Config file takes precedence over env vars. Ollama needs no key."""
    if provider == "ollama":
        return None
    cfg = load()
    key_in_config = cfg.get("api_keys", {}).get(provider)
    if key_in_config:
        return key_in_config
    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    return os.environ.get(env_map.get(provider, ""))


def get_ollama_url() -> str:
    return load().get("ollama_url") or os.environ.get("OLLAMA_HOST", OLLAMA_DEFAULT_URL)


def detect_ollama() -> dict | None:
    """Probe local (and OLLAMA_HOST) for a running Ollama instance.

    Returns {'url': str, 'models': list[str]} or None if not reachable.
    Uses /api/tags — no auth required, 2s timeout to stay non-blocking.
    """
    import requests

    url = os.environ.get("OLLAMA_HOST", OLLAMA_DEFAULT_URL).rstrip("/")
    try:
        resp = requests.get(f"{url}/api/tags", timeout=2)
        if resp.ok:
            models = [m["name"] for m in resp.json().get("models", [])]
            return {"url": url, "models": models}
    except Exception:
        pass
    return None


def get_model(provider: str) -> str:
    return load().get("model") or DEFAULT_MODELS.get(
        provider, DEFAULT_MODELS["anthropic"]
    )


def set_provider(provider: str, api_key: str | None = None) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unknown provider '{provider}'. Choose: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    if provider == "claude_code" and api_key:
        raise ValueError(
            "claude_code provider uses the `claude` CLI — no API key needed"
        )
    cfg = load()
    cfg["provider"] = provider
    if api_key:
        cfg.setdefault("api_keys", {})[provider] = api_key
    save(cfg)


def set_api_key(provider: str, api_key: str) -> None:
    cfg = load()
    cfg.setdefault("api_keys", {})[provider] = api_key
    save(cfg)


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def get_device_id() -> str:
    """Return this device's UUID, creating and persisting one if needed."""
    import uuid

    cfg = load()
    if "device_id" not in cfg:
        cfg["device_id"] = str(uuid.uuid4())
        save(cfg)
    return cfg["device_id"]


def get_last_sync_ts() -> str:
    """Return ISO timestamp of last successful sync (epoch if never synced)."""
    return load().get("last_sync_ts", "1970-01-01T00:00:00+00:00")


def set_last_sync_ts(ts: str) -> None:
    """Persist the last successful sync timestamp."""
    cfg = load()
    cfg["last_sync_ts"] = ts
    save(cfg)


# ---------------------------------------------------------------------------
# Credits flag helpers
# ---------------------------------------------------------------------------


def set_credits_flag(balance_usd: float) -> None:
    """Write sentinel when forgemem inference hits 402 (credits exhausted)."""
    CREDITS_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDITS_FLAG_PATH.write_text(
        json.dumps(
            {"balance_usd": balance_usd, "ts": datetime.datetime.now().isoformat()}
        )
    )


def clear_credits_flag() -> None:
    """Remove sentinel after user re-authenticates or adds credits."""
    CREDITS_FLAG_PATH.unlink(missing_ok=True)


def get_credits_flag() -> dict | None:
    """Return {'balance_usd': float, 'ts': str} if flag exists, else None."""
    if not CREDITS_FLAG_PATH.exists():
        return None
    try:
        return json.loads(CREDITS_FLAG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
