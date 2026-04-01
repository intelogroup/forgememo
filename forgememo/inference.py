"""
Forgememo inference abstraction.
Routes LLM calls to the configured provider (BYOK or managed).
"""

import os
import sys
from forgememo import config as cfg


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
    elif provider == "claude_code":
        return _call_claude_code(prompt, max_tokens, model)
    elif provider == "forgememo":
        return _call_forgemem_managed(prompt, max_tokens, model)
    else:
        print(f"ERROR: Unknown provider '{provider}'. Run: forgememo config anthropic --key sk-ant-...", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _call_anthropic(prompt: str, max_tokens: int, model: str) -> str:
    try:
        import anthropic
    except ImportError:
        print("ERROR: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    api_key = cfg.get_api_key("anthropic")
    if not api_key:
        print(
            "ERROR: No Anthropic API key found.\n"
            "  Set it with: forgememo config anthropic --key sk-ant-...\n"
            "  Or export ANTHROPIC_API_KEY=sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _call_openai(prompt: str, max_tokens: int, model: str) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: pip install openai", file=sys.stderr)
        sys.exit(1)

    api_key = cfg.get_api_key("openai")
    if not api_key:
        print(
            "ERROR: No OpenAI API key found.\n"
            "  Set it with: forgememo config openai --key sk-...\n"
            "  Or export OPENAI_API_KEY=sk-...",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def _call_gemini(prompt: str, max_tokens: int, model: str) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        print("ERROR: pip install google-generativeai", file=sys.stderr)
        sys.exit(1)

    api_key = cfg.get_api_key("gemini")
    if not api_key:
        print(
            "ERROR: No Gemini API key found.\n"
            "  Set it with: forgememo config gemini --key AIza...\n"
            "  Or export GEMINI_API_KEY=AIza...",
            file=sys.stderr,
        )
        sys.exit(1)

    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model)
    response = gemini_model.generate_content(
        prompt,
        generation_config={"max_output_tokens": max_tokens},
    )
    return response.text.strip()


def _call_ollama(prompt: str, max_tokens: int, model: str) -> str:
    try:
        import requests as req
    except ImportError:
        print("ERROR: pip install requests", file=sys.stderr)
        sys.exit(1)

    base_url = cfg.get_ollama_url()
    try:
        resp = req.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": max_tokens}},
            timeout=60,
        )
    except req.exceptions.ConnectionError:
        print(
            f"ERROR: Could not reach Ollama at {base_url}.\n"
            "  Make sure Ollama is running: ollama serve\n"
            "  Or switch provider: forgememo config anthropic --key sk-ant-...",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code == 404:
        print(
            f"ERROR: Model '{model}' not found in Ollama.\n"
            f"  Pull it with: ollama pull {model}\n"
            "  Or set a different model: forgememo config --model llama3.2",
            file=sys.stderr,
        )
        sys.exit(1)
    if not resp.ok:
        print(f"ERROR: Ollama error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)

    return resp.json().get("response", "").strip()


def _call_claude_code(prompt: str, max_tokens: int, model: str) -> str:
    """Call the local `claude` CLI in non-interactive mode (uses the user's Claude subscription)."""
    import shutil
    import subprocess

    if not shutil.which("claude"):
        print(
            "ERROR: 'claude' CLI not found.\n"
            "  Install Claude Code: https://claude.ai/code\n"
            "  Then log in: claude login",
            file=sys.stderr,
        )
        sys.exit(1)

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or f"claude CLI exited {result.returncode}"
        raise ConnectionError(err)
    return result.stdout.strip()


MANAGED_API_URL = os.environ.get("FORGEMEM_API_URL", "https://api.forgememo.com") + "/v1/inference"


def _call_forgemem_managed(prompt: str, max_tokens: int, model: str) -> str:
    """Call Forgememo managed inference. Requires `forgememo auth login`."""
    try:
        import requests as req
    except ImportError:
        print("ERROR: pip install requests", file=sys.stderr)
        sys.exit(1)

    token = cfg.load().get("forgemem_token")
    if not token:
        print(
            "ERROR: Not authenticated with Forgememo.\n"
            "  Run: forgememo auth login",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        resp = req.post(
            MANAGED_API_URL,
            json={"prompt": prompt, "max_tokens": max_tokens, "model": model},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    except req.exceptions.ConnectionError:
        print("ERROR: Could not reach api.forgememo.com. Check your connection.", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 401:
        print("ERROR: Session expired. Run: forgememo auth login", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 402:
        balance = resp.json().get("balance_usd", "0.00")
        print(
            f"ERROR: Insufficient credits (balance: ${balance}).\n"
            "  Add credits: https://app.forgememo.com/billing\n"
            "  Or switch to BYOK: forgememo config anthropic --key sk-ant-...",
            file=sys.stderr,
        )
        # Persist flag so `forgemem status` shows a warning until resolved.
        # Only notify once (when flag transitions from absent to present).
        try:
            _flag_already_set = cfg.get_credits_flag() is not None
            cfg.set_credits_flag(float(balance))
        except Exception:
            _flag_already_set = True  # safe default: skip notification on error
        if sys.platform == "darwin" and not _flag_already_set:
            import subprocess
            subprocess.run(
                [
                    "osascript", "-e",
                    'display notification "Scheduled memory runs paused — add credits to continue" '
                    'with title "Forgememo" subtitle "Run: forgememo status"',
                ],
                check=False,
                capture_output=True,
            )
        sys.exit(1)
    if resp.status_code == 429:
        print("ERROR: Rate limit hit. Wait a moment and retry.", file=sys.stderr)
        sys.exit(1)
    if not resp.ok:
        print(f"ERROR: Forgememo API error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)

    # Auto-clear the credits flag on success — user topped up, runs resume.
    try:
        cfg.clear_credits_flag()
    except Exception:
        pass
    return resp.json()["text"]
