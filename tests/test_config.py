"""
Tests for provider configuration (forgememo/config.py).

Covers:
- load/save roundtrip
- get/set provider
- API key resolution (config vs env var)
- claude_code provider constraints
- model resolution
- device_id stability
- credits flag set/get/clear
"""

from __future__ import annotations

import json

import pytest

import forgememo.config as cfg_module
from forgememo import config as cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    credits_flag = tmp_path / ".credits_exhausted"
    monkeypatch.setattr(cfg_module, "CONFIG_PATH", config_file)
    monkeypatch.setattr(cfg_module, "CREDITS_FLAG_PATH", credits_flag)
    yield config_file


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------

class TestLoadSave:
    def test_load_returns_empty_dict_when_no_file(self, isolated_config):
        assert cfg.load() == {}

    def test_save_creates_file(self, isolated_config):
        cfg.save({"key": "value"})
        assert isolated_config.exists()

    def test_save_roundtrip(self, isolated_config):
        data = {"provider": "openai", "model": "gpt-4o"}
        cfg.save(data)
        assert cfg.load() == data

    def test_load_returns_empty_on_corrupt_file(self, isolated_config):
        isolated_config.write_text("not json {{{{")
        assert cfg.load() == {}

    def test_saved_file_is_valid_json(self, isolated_config):
        cfg.save({"a": 1})
        parsed = json.loads(isolated_config.read_text())
        assert parsed == {"a": 1}


# ---------------------------------------------------------------------------
# get_provider / set_provider
# ---------------------------------------------------------------------------

class TestProvider:
    def test_default_provider_is_anthropic(self):
        assert cfg.get_provider() == "anthropic"

    def test_set_and_get_provider(self):
        cfg.set_provider("openai")
        assert cfg.get_provider() == "openai"

    def test_set_provider_invalid_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            cfg.set_provider("nonexistent")

    def test_all_supported_providers_accepted(self):
        for provider in cfg_module.SUPPORTED_PROVIDERS:
            cfg.set_provider(provider)
            assert cfg.get_provider() == provider

    def test_claude_code_rejects_api_key(self):
        with pytest.raises(ValueError, match="no API key needed"):
            cfg.set_provider("claude_code", api_key="sk-ant-abc")

    def test_claude_code_without_key_accepted(self):
        cfg.set_provider("claude_code")
        assert cfg.get_provider() == "claude_code"

    def test_set_provider_stores_api_key(self):
        cfg.set_provider("anthropic", api_key="sk-ant-test")
        loaded = cfg.load()
        assert loaded["api_keys"]["anthropic"] == "sk-ant-test"

    def test_set_provider_without_key_does_not_store_key(self):
        cfg.set_provider("anthropic")
        loaded = cfg.load()
        assert "api_keys" not in loaded or "anthropic" not in loaded.get("api_keys", {})


# ---------------------------------------------------------------------------
# get_api_key
# ---------------------------------------------------------------------------

class TestGetApiKey:
    def test_ollama_always_returns_none(self):
        assert cfg.get_api_key("ollama") is None

    def test_reads_key_from_config(self):
        cfg.set_provider("anthropic", api_key="sk-ant-config")
        assert cfg.get_api_key("anthropic") == "sk-ant-config"

    def test_reads_anthropic_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
        assert cfg.get_api_key("anthropic") == "sk-ant-env"

    def test_reads_openai_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-env")
        assert cfg.get_api_key("openai") == "sk-openai-env"

    def test_reads_gemini_key_from_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-env")
        assert cfg.get_api_key("gemini") == "AIza-env"

    def test_config_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        cfg.set_provider("anthropic", api_key="sk-config")
        assert cfg.get_api_key("anthropic") == "sk-config"

    def test_returns_none_when_no_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert cfg.get_api_key("anthropic") is None

    def test_unknown_provider_returns_none(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert cfg.get_api_key("unknown_provider") is None


# ---------------------------------------------------------------------------
# set_api_key
# ---------------------------------------------------------------------------

class TestSetApiKey:
    def test_stores_key(self):
        cfg.set_api_key("openai", "sk-openai-stored")
        assert cfg.load()["api_keys"]["openai"] == "sk-openai-stored"

    def test_does_not_overwrite_other_keys(self):
        cfg.set_api_key("anthropic", "sk-ant")
        cfg.set_api_key("openai", "sk-oai")
        loaded = cfg.load()
        assert loaded["api_keys"]["anthropic"] == "sk-ant"
        assert loaded["api_keys"]["openai"] == "sk-oai"


# ---------------------------------------------------------------------------
# get_model
# ---------------------------------------------------------------------------

class TestGetModel:
    def test_default_model_for_anthropic(self):
        assert cfg.get_model("anthropic") == cfg_module.DEFAULT_MODELS["anthropic"]

    def test_default_model_for_openai(self):
        assert cfg.get_model("openai") == cfg_module.DEFAULT_MODELS["openai"]

    def test_default_model_for_ollama(self):
        assert cfg.get_model("ollama") == cfg_module.DEFAULT_MODELS["ollama"]

    def test_default_model_for_claude_code(self):
        assert cfg.get_model("claude_code") == "claude_code"

    def test_config_model_overrides_default(self):
        data = cfg.load()
        data["model"] = "claude-opus-4-6"
        cfg.save(data)
        assert cfg.get_model("anthropic") == "claude-opus-4-6"

    def test_unknown_provider_falls_back_to_anthropic_default(self):
        result = cfg.get_model("nonexistent")
        assert result == cfg_module.DEFAULT_MODELS["anthropic"]


# ---------------------------------------------------------------------------
# get_device_id
# ---------------------------------------------------------------------------

class TestDeviceId:
    def test_returns_string(self):
        assert isinstance(cfg.get_device_id(), str)

    def test_stable_across_calls(self):
        id1 = cfg.get_device_id()
        id2 = cfg.get_device_id()
        assert id1 == id2

    def test_persisted_to_config(self):
        device_id = cfg.get_device_id()
        assert cfg.load().get("device_id") == device_id

    def test_uuid_format(self):
        import uuid
        device_id = cfg.get_device_id()
        # Should not raise
        uuid.UUID(device_id)


# ---------------------------------------------------------------------------
# Credits flag
# ---------------------------------------------------------------------------

class TestCreditsFlag:
    def test_no_flag_returns_none(self):
        assert cfg.get_credits_flag() is None

    def test_set_flag_creates_file(self):
        cfg.set_credits_flag(0.50)
        assert cfg_module.CREDITS_FLAG_PATH.exists()

    def test_get_flag_returns_balance(self):
        cfg.set_credits_flag(1.23)
        flag = cfg.get_credits_flag()
        assert flag is not None
        assert flag["balance_usd"] == 1.23

    def test_get_flag_has_timestamp(self):
        cfg.set_credits_flag(0.0)
        flag = cfg.get_credits_flag()
        assert "ts" in flag

    def test_clear_flag_removes_file(self):
        cfg.set_credits_flag(0.0)
        cfg.clear_credits_flag()
        assert not cfg_module.CREDITS_FLAG_PATH.exists()

    def test_get_flag_after_clear_returns_none(self):
        cfg.set_credits_flag(5.0)
        cfg.clear_credits_flag()
        assert cfg.get_credits_flag() is None

    def test_clear_flag_safe_when_not_set(self):
        cfg.clear_credits_flag()  # must not raise

    def test_get_flag_returns_none_on_corrupt_file(self):
        cfg_module.CREDITS_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg_module.CREDITS_FLAG_PATH.write_text("not json")
        assert cfg.get_credits_flag() is None
