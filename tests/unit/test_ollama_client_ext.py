"""Extended tests for cortex.ollama_client — covers paths not reached by
test_ollama_client.py (generate_json, generate_stream, tier wrappers, exceptions).
"""
from __future__ import annotations

import json as _json
from unittest.mock import MagicMock, patch
import pytest

from cortex.ollama_client import (
    generate,
    generate_deep,
    generate_expert,
    generate_fast,
    generate_json,
    generate_stream,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_post(response_text: str = "hello", extra: dict | None = None):
    """Return a requests.post replacement that yields a successful response."""
    def _fake_post(url, json=None, stream=False, timeout=None, **kw):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.status_code = 200
        body = {"response": response_text, "done": True,
                "eval_count": 50, "eval_duration": 500_000_000}
        if extra:
            body.update(extra)
        r.json.return_value = body
        return r
    return _fake_post


def _mock_post_raises(exc: Exception):
    def _fake(url, **kw):
        raise exc
    return _fake


# ---------------------------------------------------------------------------
# generate — exception paths
# ---------------------------------------------------------------------------

class TestGenerateExceptions:
    def test_requests_exception_returns_error_string(self, monkeypatch):
        import requests
        monkeypatch.setattr(
            "requests.post",
            _mock_post_raises(requests.ConnectionError("refused")),
        )
        result = generate("p", "s", model="gemma4:e4b")
        assert "Ollama error" in result
        assert "refused" in result

    def test_timeout_returns_error_string(self, monkeypatch):
        import requests
        monkeypatch.setattr(
            "requests.post",
            _mock_post_raises(requests.Timeout("timed out")),
        )
        result = generate("p", "s")
        assert "Ollama error" in result

    def test_http_error_returns_error_string(self, monkeypatch):
        import requests
        err = requests.HTTPError("500 Server Error")
        monkeypatch.setattr("requests.post", _mock_post_raises(err))
        result = generate("p", "s")
        assert "Ollama error" in result

    def test_response_with_no_response_key_returns_empty(self, monkeypatch):
        def _fake(url, json=None, **kw):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"done": True}  # no 'response' key
            return r
        monkeypatch.setattr("requests.post", _fake)
        result = generate("p", "s")
        assert result == ""

    def test_keep_alive_passed_in_payload(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate("p", "s", keep_alive="10m")
        assert captured[0]["keep_alive"] == "10m"

    def test_think_false_by_default(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate("p", "s")
        assert captured[0]["think"] is False

    def test_think_true_when_requested(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate("p", "s", think=True)
        assert captured[0]["think"] is True


# ---------------------------------------------------------------------------
# generate_json
# ---------------------------------------------------------------------------

class TestGenerateJson:
    def test_happy_path_returns_dict(self, monkeypatch):
        monkeypatch.setattr("requests.post", _mock_post('{"score": 0.9}'))
        result = generate_json("classify this", "system")
        assert isinstance(result, dict)
        assert result["score"] == pytest.approx(0.9)

    def test_json_in_leading_fence_lstripped(self, monkeypatch):
        # The stripper does lstrip('`') which removes leading backticks.
        # A fully-fenced block leaves trailing backticks, so parse fails → None.
        # A response with ONLY leading ``` and a json prefix IS parseable:
        raw = "```json\n{\"ok\": true}"  # no closing fence
        monkeypatch.setattr("requests.post", _mock_post(raw))
        result = generate_json("p", "s")
        assert result is not None
        assert result["ok"] is True

    def test_plain_json_with_leading_fence_parsed(self, monkeypatch):
        # Leading fence only (no trailing) — lstrip removes it successfully
        raw = "```\n{\"value\": 42}"  # no closing fence
        monkeypatch.setattr("requests.post", _mock_post(raw))
        result = generate_json("p", "s")
        assert result is not None
        assert result["value"] == 42

    def test_malformed_json_returns_none(self, monkeypatch):
        monkeypatch.setattr("requests.post", _mock_post("this is not json at all"))
        result = generate_json("p", "s")
        assert result is None

    def test_uses_json_mode_format(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": '{"x":1}', "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate_json("p", "s")
        assert captured[0].get("format") == "json"

    def test_empty_response_returns_none(self, monkeypatch):
        monkeypatch.setattr("requests.post", _mock_post(""))
        result = generate_json("p", "s")
        assert result is None

    def test_ollama_error_propagates(self, monkeypatch):
        """If generate() returns an error string, generate_json returns None."""
        import requests
        monkeypatch.setattr(
            "requests.post",
            _mock_post_raises(requests.ConnectionError("down")),
        )
        result = generate_json("p", "s")
        assert result is None


# ---------------------------------------------------------------------------
# generate_stream
# ---------------------------------------------------------------------------

class TestGenerateStream:
    def _build_stream_mock(self, lines: list[dict]):
        """Build a requests.post replacement that yields streaming line data."""
        encoded = [_json.dumps(ln).encode() for ln in lines]

        def _fake_post(url, json=None, stream=False, timeout=None, **kw):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.status_code = 200
            r.iter_lines.return_value = iter(encoded)
            r.__enter__ = lambda s: s
            r.__exit__ = MagicMock(return_value=False)
            return r

        return _fake_post

    def test_yields_token_and_thinking_flag(self, monkeypatch):
        lines = [
            {"response": "Hello", "thinking": False, "done": False},
            {"response": " world", "thinking": False, "done": True},
        ]
        monkeypatch.setattr("requests.post", self._build_stream_mock(lines))

        tokens = list(generate_stream("p", "s", model="gemma4:e4b"))
        assert len(tokens) == 2
        assert tokens[0] == ("Hello", False)
        assert tokens[1] == (" world", False)

    def test_thinking_flag_propagated(self, monkeypatch):
        lines = [
            {"response": "thinking...", "thinking": True, "done": False},
            {"response": "answer", "thinking": False, "done": True},
        ]
        monkeypatch.setattr("requests.post", self._build_stream_mock(lines))

        tokens = list(generate_stream("p", "s", think=True))
        assert tokens[0][1] is True
        assert tokens[1][1] is False

    def test_done_flag_stops_iteration(self, monkeypatch):
        lines = [
            {"response": "A", "thinking": False, "done": False},
            {"response": "B", "thinking": False, "done": True},
            {"response": "C", "thinking": False, "done": False},  # should not appear
        ]
        monkeypatch.setattr("requests.post", self._build_stream_mock(lines))

        tokens = list(generate_stream("p", "s"))
        assert len(tokens) == 2

    def test_empty_lines_skipped(self, monkeypatch):
        def _fake_post(url, json=None, stream=False, timeout=None, **kw):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.iter_lines.return_value = iter([b"", b"", _json.dumps(
                {"response": "hi", "thinking": False, "done": True}
            ).encode()])
            r.__enter__ = lambda s: s
            r.__exit__ = MagicMock(return_value=False)
            return r

        monkeypatch.setattr("requests.post", _fake_post)
        tokens = list(generate_stream("p", "s"))
        assert tokens == [("hi", False)]

    def test_malformed_json_lines_skipped(self, monkeypatch):
        def _fake_post(url, json=None, stream=False, timeout=None, **kw):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.iter_lines.return_value = iter([
                b"not json",
                _json.dumps({"response": "ok", "thinking": False, "done": True}).encode(),
            ])
            r.__enter__ = lambda s: s
            r.__exit__ = MagicMock(return_value=False)
            return r

        monkeypatch.setattr("requests.post", _fake_post)
        tokens = list(generate_stream("p", "s"))
        assert tokens == [("ok", False)]

    def test_stream_exception_yields_error_tuple(self, monkeypatch):
        import requests
        monkeypatch.setattr(
            "requests.post",
            _mock_post_raises(requests.ConnectionError("stream down")),
        )
        tokens = list(generate_stream("p", "s"))
        assert len(tokens) == 1
        text, is_thinking = tokens[0]
        assert "Stream error" in text
        assert is_thinking is False


# ---------------------------------------------------------------------------
# Tier-aware convenience wrappers
# ---------------------------------------------------------------------------

class TestGenerateFast:
    def test_uses_fast_model_by_default(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "fast", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        from cortex import config
        result = generate_fast("prompt", "system")
        assert captured[0]["model"] == config.OLLAMA_MODEL_FAST
        assert result == "fast"

    def test_temperature_uses_fast_tier_default(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate_fast("p", "s")
        assert captured[0]["options"]["temperature"] == 0.4

    def test_custom_model_override(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate_fast("p", "s", model="gemma4:e2b")
        assert captured[0]["model"] == "gemma4:e2b"


class TestGenerateDeep:
    def test_uses_deep_model_by_default(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "deep", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        from cortex import config
        result = generate_deep("p", "s")
        assert captured[0]["model"] == config.OLLAMA_MODEL_DEEP
        assert result == "deep"

    def test_temperature_uses_deep_tier_default(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate_deep("p", "s")
        assert captured[0]["options"]["temperature"] == 0.35

    def test_custom_num_predict_override(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate_deep("p", "s", num_predict=1000)
        assert captured[0]["options"]["num_predict"] == 1000


class TestGenerateExpert:
    def test_uses_expert_model_by_default(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "expert", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        from cortex import config
        result = generate_expert("p", "s")
        assert captured[0]["model"] == config.OLLAMA_MODEL_EXPERT
        assert result == "expert"

    def test_temperature_uses_expert_tier_default(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate_expert("p", "s")
        assert captured[0]["options"]["temperature"] == 0.3

    def test_custom_temperature_override(self, monkeypatch):
        captured: list[dict] = []

        def _fake(url, json=None, **kw):
            captured.append(json or {})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {"response": "x", "eval_count": 1, "eval_duration": 1e8}
            return r

        monkeypatch.setattr("requests.post", _fake)
        generate_expert("p", "s", temperature=0.1)
        assert captured[0]["options"]["temperature"] == 0.1
