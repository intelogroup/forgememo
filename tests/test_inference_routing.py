from __future__ import annotations

import forgememo.inference as inference


def test_routes_to_openai(monkeypatch):
    monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "openai")
    monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _p: "gpt-test")
    monkeypatch.setattr("forgememo.inference._call_openai", lambda prompt, max_tokens, model: f"{model}:{prompt}")

    out = inference.call("hello", max_tokens=5)
    assert out == "gpt-test:hello"


def test_routes_to_ollama(monkeypatch):
    monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "ollama")
    monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _p: "llama3")
    monkeypatch.setattr("forgememo.inference._call_ollama", lambda prompt, max_tokens, model: f"{model}:{max_tokens}")

    out = inference.call("hi", max_tokens=7)
    assert out == "llama3:7"


def test_routes_to_gemini(monkeypatch):
    monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "gemini")
    monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _p: "gemini-test")
    monkeypatch.setattr("forgememo.inference._call_gemini", lambda prompt, max_tokens, model: f"{model}:{prompt}")

    out = inference.call("ping", max_tokens=3)
    assert out == "gemini-test:ping"


def test_routes_to_anthropic(monkeypatch):
    monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "anthropic")
    monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _p: "claude-test")
    monkeypatch.setattr("forgememo.inference._call_anthropic", lambda prompt, max_tokens, model: f"{model}:{max_tokens}")

    out = inference.call("hello", max_tokens=9)
    assert out == "claude-test:9"


def test_routes_to_forgememo_managed(monkeypatch):
    monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "forgememo")
    monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _p: "managed-test")
    monkeypatch.setattr("forgememo.inference._call_forgemem_managed", lambda prompt, max_tokens, model: "ok")

    out = inference.call("hi", max_tokens=1)
    assert out == "ok"


def test_unknown_provider_exits(monkeypatch):
    monkeypatch.setattr("forgememo.inference.cfg.get_provider", lambda: "unknown")
    monkeypatch.setattr("forgememo.inference.cfg.get_model", lambda _p: "x")

    try:
        inference.call("hi")
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("Expected SystemExit for unknown provider")
