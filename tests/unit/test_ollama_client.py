"""Tests for cortex.ollama_client — wrapping Ollama's /api/generate."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cortex.ollama_client import generate

pytestmark = pytest.mark.unit


class TestGenerate:
    def test_basic_call_includes_prompt_and_system(self, monkeypatch):
        captured: list[dict] = []

        def _fake_post(url, json=None, **kwargs):
            captured.append({"url": url, "json": json})
            r = MagicMock()
            r.json.return_value = {"response": "hello world"}
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", _fake_post)
        text = generate("user prompt", "system prompt", model="gemma4:e4b")

        assert text == "hello world"
        assert len(captured) == 1
        body = captured[0]["json"]
        assert body["prompt"] == "user prompt"
        assert body["system"] == "system prompt"
        assert body["model"] == "gemma4:e4b"
        # think=False is the load-bearing default that prevents empty responses.
        assert body["think"] is False

    def test_images_b64_passed_through(self, monkeypatch):
        captured: list[dict] = []

        def _fake_post(url, json=None, **kwargs):
            captured.append({"url": url, "json": json})
            r = MagicMock()
            r.json.return_value = {"response": "vision response"}
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", _fake_post)
        generate("describe", "be brief", model="gemma4:e4b", images_b64=["abc=="])
        assert captured[0]["json"]["images"] == ["abc=="]

    def test_json_mode_sets_format_json(self, monkeypatch):
        captured: list[dict] = []

        def _fake_post(url, json=None, **kwargs):
            captured.append({"url": url, "json": json})
            r = MagicMock()
            r.json.return_value = {"response": '{"ok": true}'}
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", _fake_post)
        generate("p", "s", model="gemma4:e4b", json_mode=True)
        body = captured[0]["json"]
        assert body.get("format") == "json"

    def test_options_include_temperature_and_num_predict(self, monkeypatch):
        captured: list[dict] = []

        def _fake_post(url, json=None, **kwargs):
            captured.append({"url": url, "json": json})
            r = MagicMock()
            r.json.return_value = {"response": "x"}
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", _fake_post)
        generate("p", "s", model="gemma4:e4b", temperature=0.2, num_predict=500, num_ctx=16384)
        opts = captured[0]["json"]["options"]
        assert opts["temperature"] == 0.2
        assert opts["num_predict"] == 500
        assert opts["num_ctx"] == 16384
