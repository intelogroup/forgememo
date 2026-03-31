"""
Forgemem inference abstraction.
Routes LLM calls to the configured provider (BYOK or managed).
"""

import os
import sys
import time
import functools
from forgemem import config as cfg


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class ForgememoInferenceError(Exception):
    """Base exception for all inference-layer errors."""


class ProviderNotFoundError(ForgememoInferenceError):
    """Unknown provider name."""


class MissingAPIKeyError(ForgememoInferenceError):
    """API key / auth token not configured."""


class MissingDependencyError(ForgememoInferenceError):
    """Required SDK package not installed."""


class RateLimitError(ForgememoInferenceError):
    """Provider returned 429 / rate-limit."""


class InsufficientCreditsError(ForgememoInferenceError):
    """Forgemem managed: insufficient credits (402)."""


class ModelNotFoundError(ForgememoInferenceError):
    """Requested model does not exist on the provider."""


class ProviderConnectionError(ForgememoInferenceError):
    """Could not reach the provider endpoint."""


class ProviderAPIError(ForgememoInferenceError):
    """Catch-all for non-transient provider HTTP errors."""


class EmptyResponseError(ForgememoInferenceError):
    """Provider returned an empty / blank response."""


class DistillParseError(ForgememoInferenceError):
    """JSON parsing failed on the distill response."""


# ---------------------------------------------------------------------------
# Retry decorator for transient errors
# ---------------------------------------------------------------------------

_TRANSIENT_STATUS_CODES = {429, 500, 502, 503}


def _retry_transient(func):
    """Retry once (2 attempts total) with 2 s backoff on transient errors."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(2):
            try:
                return func(*args, **kwargs)
            except (RateLimitError, ProviderAPIError) as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise
            except Exception as exc:
                # Check for provider SDK transient HTTP errors
                status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
                if status in _TRANSIENT_STATUS_CODES and attempt == 0:
                    last_exc = exc
                    time.sleep(2)
                    continue
                raise
        raise last_exc  # pragma: no cover

    return wrapper


# ---------------------------------------------------------------------------
# Timeout constants (seconds)
# ---------------------------------------------------------------------------

_ANTHROPIC_TIMEOUT = 60
_OPENAI_TIMEOUT = 60
_GEMINI_TIMEOUT = 60
_OLLAMA_TIMEOUT = 60
_MANAGED_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def call(prompt: str, max_tokens: int = 300, model: str | None = None) -> str:
    """Call the configured provider and return the raw text response."""
    provider = cfg.get_provider()
    model = model or cfg.get_model(provider)

    if provider == "anthropic":
        return _call_anthropic(prompt, max_tokens, model)
    elif provider == "openai":
        return _call_openai(prompt, max_tokens, model)
    elif provider == "gemini":
        return _call_gemini(prompt, max_tokens, model)
    elif provider == "ollama":
        return _call_ollama(prompt, max_tokens, model)
    elif provider == "forgememo":
        return _call_forgemem_managed(prompt, max_tokens, model)
    else:
        raise ProviderNotFoundError(
            f"Unknown provider '{provider}'. Run: forgemem config provider anthropic"
        )


def _check_empty(text: str) -> str:
    """Raise EmptyResponseError when the provider gives back nothing useful."""
    if not text or not text.strip():
        raise EmptyResponseError("Provider returned an empty response")
    return text.strip()


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

@_retry_transient
def _call_anthropic(prompt: str, max_tokens: int, model: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise MissingDependencyError("pip install anthropic")

    api_key = cfg.get_api_key("anthropic")
    if not api_key:
        raise MissingAPIKeyError(
            "No Anthropic API key found.\n"
            "  Set it with: forgemem config provider anthropic --key sk-ant-...\n"
            "  Or export ANTHROPIC_API_KEY=sk-ant-..."
        )

    client = anthropic.Anthropic(api_key=api_key, timeout=_ANTHROPIC_TIMEOUT)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return _check_empty(response.content[0].text)


@_retry_transient
def _call_openai(prompt: str, max_tokens: int, model: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise MissingDependencyError("pip install openai")

    api_key = cfg.get_api_key("openai")
    if not api_key:
        raise MissingAPIKeyError(
            "No OpenAI API key found.\n"
            "  Set it with: forgemem config provider openai --key sk-...\n"
            "  Or export OPENAI_API_KEY=sk-..."
        )

    client = OpenAI(api_key=api_key, timeout=_OPENAI_TIMEOUT)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return _check_empty(response.choices[0].message.content)


@_retry_transient
def _call_gemini(prompt: str, max_tokens: int, model: str) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        raise MissingDependencyError("pip install google-generativeai")

    api_key = cfg.get_api_key("gemini")
    if not api_key:
        raise MissingAPIKeyError(
            "No Gemini API key found.\n"
            "  Set it with: forgemem config provider gemini --key AIza-...\n"
            "  Or export GEMINI_API_KEY=AIza-..."
        )

    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model)
    request_options = {"timeout": _GEMINI_TIMEOUT}
    response = gemini_model.generate_content(
        prompt,
        generation_config={"max_output_tokens": max_tokens},
        request_options=request_options,
    )
    return _check_empty(response.text)


@_retry_transient
def _call_ollama(prompt: str, max_tokens: int, model: str) -> str:
    try:
        import requests as req
    except ImportError:
        raise MissingDependencyError("pip install requests")

    base_url = cfg.get_ollama_url()
    try:
        resp = req.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": max_tokens}},
            timeout=_OLLAMA_TIMEOUT,
        )
    except req.exceptions.ConnectionError:
        raise ProviderConnectionError(
            f"Could not reach Ollama at {base_url}.\n"
            "  Make sure Ollama is running: ollama serve\n"
            "  Or switch provider: forgemem config provider anthropic --key sk-ant-..."
        )

    if resp.status_code == 404:
        raise ModelNotFoundError(
            f"Model '{model}' not found in Ollama.\n"
            f"  Pull it with: ollama pull {model}\n"
            "  Or set a different model: forgemem config model llama3.2"
        )
    if resp.status_code in _TRANSIENT_STATUS_CODES:
        if resp.status_code == 429:
            raise RateLimitError(f"Ollama rate limit: {resp.status_code}")
        raise ProviderAPIError(f"Ollama error {resp.status_code}: {resp.text[:200]}")
    if not resp.ok:
        raise ProviderAPIError(f"Ollama error {resp.status_code}: {resp.text[:200]}")

    return _check_empty(resp.json().get("response", ""))


MANAGED_API_URL = os.environ.get("FORGEMEM_API_URL", "https://api.forgememo.com") + "/v1/inference"


@_retry_transient
def _call_forgemem_managed(prompt: str, max_tokens: int, model: str) -> str:
    """Call Forgemem managed inference. Requires `forgemem auth login`."""
    try:
        import requests as req
    except ImportError:
        raise MissingDependencyError("pip install requests")

    token = cfg.load().get("forgemem_token")
    if not token:
        raise MissingAPIKeyError(
            "Not authenticated with Forgemem.\n"
            "  Run: forgemem auth login"
        )

    try:
        resp = req.post(
            MANAGED_API_URL,
            json={"prompt": prompt, "max_tokens": max_tokens, "model": model},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_MANAGED_TIMEOUT,
        )
    except req.exceptions.ConnectionError:
        raise ProviderConnectionError("Could not reach api.forgememo.com. Check your connection.")

    if resp.status_code == 401:
        raise MissingAPIKeyError("Session expired. Run: forgemem auth login")
    if resp.status_code == 402:
        balance = resp.json().get("balance_usd", "0.00")
        # Persist flag so `forgemem status` shows a warning until resolved.
        try:
            _flag_already_set = cfg.get_credits_flag() is not None
            cfg.set_credits_flag(float(balance))
        except Exception:
            _flag_already_set = True
        if sys.platform == "darwin" and not _flag_already_set:
            import subprocess
            subprocess.run(
                [
                    "osascript", "-e",
                    'display notification "Scheduled memory runs paused \u2014 add credits to continue" '
                    'with title "Forgemem" subtitle "Run: forgemem status"',
                ],
                check=False,
                capture_output=True,
            )
        raise InsufficientCreditsError(
            f"Insufficient credits (balance: ${balance}).\n"
            "  Add credits: https://app.forgememo.com/billing\n"
            "  Or switch to BYOK: forgemem config provider anthropic --key sk-ant-..."
        )
    if resp.status_code == 429:
        raise RateLimitError("Rate limit hit. Wait a moment and retry.")
    if resp.status_code in _TRANSIENT_STATUS_CODES:
        raise ProviderAPIError(f"Forgemem API error {resp.status_code}: {resp.text[:200]}")
    if not resp.ok:
        raise ProviderAPIError(f"Forgemem API error {resp.status_code}: {resp.text[:200]}")

    # Auto-clear the credits flag on success
    try:
        cfg.clear_credits_flag()
    except Exception:
        pass
    return _check_empty(resp.json().get("text", ""))
