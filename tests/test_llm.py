from __future__ import annotations

from types import SimpleNamespace

import llm_analyzer


def test_llm_status_requires_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_ENABLED", raising=False)

    result = llm_analyzer.status()

    assert result["available"] is False
    assert result["provider"] == "Google Gemini"
    assert "GEMINI_API_KEY" in result["reason"]


def test_llm_status_available_with_gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    result = llm_analyzer.status()

    assert result["available"] is True
    assert result["provider"] == "Google Gemini"
    assert result["model"] == "gemini-2.5-flash"
    assert result["reason"] == ""


def test_call_json_uses_gemini_generate_content(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                text='{"ok": true}',
                usage_metadata=SimpleNamespace(prompt_token_count=11, candidates_token_count=7),
                model_version="gemini-test",
            )

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(llm_analyzer, "_client", lambda: FakeClient())
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")

    data, usage = llm_analyzer._call_json("system prompt", "user prompt", max_tokens=123)

    assert data == {"ok": True}
    assert captured["model"] == "gemini-test"
    assert captured["contents"] == "user prompt"
    assert captured["config"].system_instruction == "system prompt"
    assert captured["config"].max_output_tokens == 123
    assert captured["config"].response_mime_type == "application/json"
    assert usage == {
        "provider": "google-gemini",
        "model": "gemini-test",
        "input_tokens": 11,
        "output_tokens": 7,
    }
