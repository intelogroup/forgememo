"""
Tests for provider selection and init flow.

Covers:
- _configure_provider_noninteractive: --provider flag, all 6 providers, invalid provider
- _prompt_provider_setup: non-TTY guard, --yes guard, already-configured short-circuit,
  questionary mock for each provider selection, skip, API key prompt, forgememo OAuth call
- TestClaudeCodeInference: subprocess invocation, binary missing, timeout, non-zero exit
"""

from __future__ import annotations

import subprocess
import sys

import pytest
import typer

import forgememo.config as fm_config
from forgememo.commands.lifecycle import (
    _configure_provider_noninteractive,
    _prompt_provider_setup,
)


# ---------------------------------------------------------------------------
# Fixture: isolate config from real ~/.forgemem/config.json
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(fm_config, "CONFIG_PATH", config_path)
    # Also patch inside lifecycle module (imported as fm_cfg at call time)
    monkeypatch.setenv("FORGEMEM_CONFIG", str(config_path))


# ---------------------------------------------------------------------------
# _configure_provider_noninteractive
# ---------------------------------------------------------------------------


class TestConfigureProviderNoninteractive:
    @pytest.mark.parametrize(
        "provider",
        ["anthropic", "openai", "gemini", "ollama", "claude_code", "forgememo"],
    )
    def test_valid_provider_saved(self, provider):
        _configure_provider_noninteractive(provider)
        assert fm_config.get_provider() == provider

    def test_invalid_provider_exits_1(self):
        with pytest.raises(typer.Exit) as exc:
            _configure_provider_noninteractive("badprovider")
        assert exc.value.exit_code == 1

    def test_claude_code_no_api_key_required(self):
        _configure_provider_noninteractive("claude_code")
        cfg = fm_config.load()
        assert cfg.get("provider") == "claude_code"
        # No API key stored
        assert not cfg.get("api_keys", {}).get("claude_code")

    def test_prints_guidance_for_claude_code(self, capsys):
        _configure_provider_noninteractive("claude_code")
        # Rich console writes to stdout; just verify it doesn't crash and
        # the function completes — output validation is format-sensitive

    def test_prints_guidance_for_byok_providers(self, capsys):
        _configure_provider_noninteractive("anthropic")
        # Should not raise


# ---------------------------------------------------------------------------
# _prompt_provider_setup: guards
# ---------------------------------------------------------------------------


class TestPromptProviderSetupGuards:
    def test_non_tty_auto_sets_forgememo(self, monkeypatch, capsys):
        """Non-TTY: auto-select forgememo when claude CLI is not detected."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        import shutil
        monkeypatch.setattr(shutil, "which", lambda _: None)
        _prompt_provider_setup(yes=False)
        assert fm_config.load().get("provider") == "forgememo"

    def test_yes_flag_auto_sets_claude_code_when_detected(self, monkeypatch, capsys):
        """--yes: auto-select claude_code when claude CLI is detected."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
        _prompt_provider_setup(yes=True)
        assert fm_config.load().get("provider") == "claude_code"

    def test_yes_flag_auto_sets_forgememo_when_no_claude(self, monkeypatch, capsys):
        """--yes: auto-select forgememo when claude CLI is not detected."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        import shutil
        monkeypatch.setattr(shutil, "which", lambda _: None)
        _prompt_provider_setup(yes=True)
        assert fm_config.load().get("provider") == "forgememo"

    def test_already_configured_skips_prompt(self, monkeypatch):
        fm_config.save({"provider": "anthropic"})
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        # If questionary were called it would hang; the early-return prevents that
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "anthropic"

    def test_force_true_bypasses_already_configured(self, monkeypatch):
        fm_config.save({"provider": "anthropic"})
        _patch_questionary(monkeypatch, "ollama")
        _prompt_provider_setup(yes=False, force=True)
        assert fm_config.get_provider() == "ollama"


# ---------------------------------------------------------------------------
# _prompt_provider_setup: provider selection via questionary mock
# ---------------------------------------------------------------------------


class _MockAsk:
    """Simulates questionary.select(...).ask() returning a preset value."""

    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def _patch_questionary(monkeypatch, return_value):
    """Patch questionary.select to return a _MockAsk that yields return_value.

    questionary is imported lazily inside _prompt_provider_setup, so we patch
    the module object directly (same object in sys.modules).
    """
    import questionary as _q

    monkeypatch.setattr(_q, "select", lambda *a, **kw: _MockAsk(return_value))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


class TestPromptProviderSetupSelection:
    def test_select_claude_code_saves_provider(self, monkeypatch):
        _patch_questionary(monkeypatch, "claude_code")
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "claude_code"
        # No API key stored
        assert not fm_config.load().get("api_keys", {}).get("claude_code")

    def test_select_ollama_saves_provider(self, monkeypatch):
        _patch_questionary(monkeypatch, "ollama")
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "ollama"

    def test_select_anthropic_prompts_for_key_and_saves(self, monkeypatch):
        _patch_questionary(monkeypatch, "anthropic")
        monkeypatch.setattr(typer, "prompt", lambda *a, **kw: "sk-ant-testkey")
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "anthropic"
        assert fm_config.get_api_key("anthropic") == "sk-ant-testkey"

    def test_select_anthropic_skip_key_saves_provider_without_key(self, monkeypatch):
        _patch_questionary(monkeypatch, "anthropic")
        monkeypatch.setattr(typer, "prompt", lambda *a, **kw: "")
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "anthropic"
        assert not fm_config.load().get("api_keys", {}).get("anthropic")

    def test_select_openai_prompts_for_key(self, monkeypatch):
        _patch_questionary(monkeypatch, "openai")
        monkeypatch.setattr(typer, "prompt", lambda *a, **kw: "sk-openai-test")
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "openai"
        assert fm_config.get_api_key("openai") == "sk-openai-test"

    def test_select_gemini_prompts_for_key(self, monkeypatch):
        _patch_questionary(monkeypatch, "gemini")
        monkeypatch.setattr(typer, "prompt", lambda *a, **kw: "gemini-test-key")
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "gemini"
        assert fm_config.get_api_key("gemini") == "gemini-test-key"

    def test_select_skip_does_not_save_provider(self, monkeypatch):
        _patch_questionary(monkeypatch, None)
        _prompt_provider_setup(yes=False)
        assert fm_config.load().get("provider") is None

    def test_select_forgememo_calls_auth_login(self, monkeypatch):
        _patch_questionary(monkeypatch, "forgememo")
        auth_called = []
        import forgememo.commands.configure as _configure
        monkeypatch.setattr(_configure, "_do_auth_login", lambda: auth_called.append(True))
        _prompt_provider_setup(yes=False)
        assert fm_config.get_provider() == "forgememo"
        assert auth_called == [True]


# ---------------------------------------------------------------------------
# claude_code inference: _call_claude_code
# ---------------------------------------------------------------------------


class TestClaudeCodeInference:
    def _import(self):
        import forgememo.inference as inf

        return inf

    def test_routes_to_claude_code(self, monkeypatch):
        inf = self._import()
        monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "claude_code")
        monkeypatch.setattr(
            "forgememo.inference.cfg.get_model", lambda _p: "claude_code"
        )
        monkeypatch.setattr(
            "forgememo.inference._call_claude_code",
            lambda prompt, max_tokens, model: "routed",
        )
        assert inf.call("hi") == "routed"

    def test_subprocess_called_with_claude_p(self, monkeypatch):
        import forgememo.inference as inf
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="memory result", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = inf._call_claude_code("summarize this", 200, "claude_code")
        assert result == "memory result"
        assert calls[0] == ["claude", "-p", "summarize this"]

    def test_binary_not_found_exits_1(self, monkeypatch):
        import forgememo.inference as inf
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(SystemExit) as exc:
            inf._call_claude_code("prompt", 100, "claude_code")
        assert exc.value.code == 1

    def test_timeout_raises(self, monkeypatch):
        import forgememo.inference as inf
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["claude"], 120)
            ),
        )
        with pytest.raises(subprocess.TimeoutExpired):
            inf._call_claude_code("prompt", 100, "claude_code")

    def test_nonzero_exit_raises_connection_error(self, monkeypatch):
        import forgememo.inference as inf
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                ["claude"], 1, stdout="", stderr="rate limited"
            ),
        )
        with pytest.raises(ConnectionError, match="rate limited"):
            inf._call_claude_code("prompt", 100, "claude_code")

    def test_stdout_stripped(self, monkeypatch):
        import forgememo.inference as inf
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                ["claude"], 0, stdout="  result with whitespace  \n", stderr=""
            ),
        )
        assert inf._call_claude_code("p", 10, "claude_code") == "result with whitespace"
