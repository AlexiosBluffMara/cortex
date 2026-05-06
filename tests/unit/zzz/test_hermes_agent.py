"""Tests for hermes.agent — CortexAgent and tool registry."""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Inject stubs for heavy dependencies before importing hermes.agent.
#
# Strategy:
#   - cortex, cortex.config, cortex.prompts  — use the real modules.
#   - cortex.ollama_client                   — fake (no Ollama server).
#   - cortex.gpu_scheduler, cortex.model_manager — fake (no GPU).
#   - cortex.logger                          — fake (silence logging).
#   - hermes.tools.*                         — fake minimal tool modules.
# ---------------------------------------------------------------------------

# Pre-load real hermes package BEFORE patching any dependencies.
# This ensures sys.modules["hermes"] and sys.modules["hermes.tools"] are the
# real package objects; subsequent imports of hermes.agent succeed.
import hermes          # real hermes package
import hermes.tools    # real hermes.tools package

import cortex  # real cortex package

# cortex.ollama_client fake
_fake_oc = types.ModuleType("cortex.ollama_client")
_fake_oc.generate = MagicMock(return_value="test response")
sys.modules["cortex.ollama_client"] = _fake_oc
cortex.ollama_client = _fake_oc  # type: ignore[attr-defined]

# cortex.logger fake
_fake_logger = types.ModuleType("cortex.logger")
_fake_logger.log = MagicMock()
sys.modules["cortex.logger"] = _fake_logger
cortex.logger = _fake_logger  # type: ignore[attr-defined]

# cortex.model_manager fake (needed by gpu_scheduler)
_fake_mm = types.ModuleType("cortex.model_manager")
_fake_mm.ModelTier  = MagicMock()
_fake_mm.get_manager = MagicMock(return_value=MagicMock())
sys.modules["cortex.model_manager"] = _fake_mm
cortex.model_manager = _fake_mm  # type: ignore[attr-defined]

# Build a fake scheduler instance — reused across tests
_FAKE_SCHEDULER = MagicMock()
_FAKE_SCHEDULER.ensure_gemma = AsyncMock(return_value=None)
_FAKE_SCHEDULER.vram_report  = MagicMock(return_value={"free_gb": 20.0, "state": "gemma_active"})
_FAKE_SCHEDULER.state        = MagicMock()
_FAKE_SCHEDULER.state.value  = "gemma_active"

# cortex.gpu_scheduler fake
# Include all attributes that conftest.py's mock_model_manager fixture uses via
# monkeypatch.setattr("cortex.gpu_scheduler.get_manager", ...) so that setattr
# does not raise AttributeError when this fake is live in sys.modules.
_fake_gs = types.ModuleType("cortex.gpu_scheduler")
_fake_gs.get_scheduler = MagicMock(return_value=_FAKE_SCHEDULER)
_fake_gs.get_manager = MagicMock()
_fake_gs.GPUScheduler = MagicMock()
_fake_gs.GPUState = MagicMock()
_fake_gs.SwapMetrics = MagicMock()
sys.modules["cortex.gpu_scheduler"] = _fake_gs
cortex.gpu_scheduler = _fake_gs  # type: ignore[attr-defined]


def _make_tool_mod(name: str) -> types.ModuleType:
    mod = types.ModuleType(f"hermes.tools.{name}")
    mod.TOOL_SCHEMA = {
        "name": name,
        "description": f"Fake {name} tool",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    async def execute(**kwargs):
        return {"ok": True, "tool": name, "kwargs": kwargs}

    mod.execute = execute
    return mod


# Replace the individual tool submodules with lightweight fakes.
# The real hermes.tools package object stays; only the 4 tool submodules are swapped.
for _tname in ("brain_scan", "narrate", "visualize", "describe_input"):
    _tm = _make_tool_mod(_tname)
    setattr(hermes.tools, _tname, _tm)
    sys.modules[f"hermes.tools.{_tname}"] = _tm

# Now safe to import
import hermes.agent as agent_module  # noqa: E402
from hermes.agent import CortexAgent, execute_tool, get_tool_schemas  # noqa: E402


def _clear_registry():
    agent_module._TOOL_REGISTRY.clear()


# ---------------------------------------------------------------------------
# get_tool_schemas
# ---------------------------------------------------------------------------

class TestGetToolSchemas:
    def setup_method(self):
        _clear_registry()

    def test_returns_list(self):
        assert isinstance(get_tool_schemas(), list)

    def test_returns_four_schemas(self):
        assert len(get_tool_schemas()) == 4

    def test_each_schema_has_name_and_description(self):
        for s in get_tool_schemas():
            assert "name" in s
            assert "description" in s

    def test_expected_tool_names(self):
        names = {s["name"] for s in get_tool_schemas()}
        assert names == {"brain_scan", "narrate", "visualize", "describe_input"}

    def test_idempotent_on_second_call(self):
        s1 = {s["name"] for s in get_tool_schemas()}
        s2 = {s["name"] for s in get_tool_schemas()}
        assert s1 == s2


# ---------------------------------------------------------------------------
# execute_tool
# ---------------------------------------------------------------------------

class TestExecuteTool:
    def setup_method(self):
        _clear_registry()

    def test_unknown_tool_returns_false_ok(self):
        result = _run(execute_tool("no_such_tool"))
        assert result["ok"] is False

    def test_unknown_tool_error_is_unknown_tool(self):
        result = _run(execute_tool("ghost"))
        assert result["error"] == "unknown_tool"

    def test_unknown_tool_message_contains_name(self):
        result = _run(execute_tool("ghost_tool"))
        assert "ghost_tool" in result.get("message", "")

    def test_brain_scan_execute_called_with_kwargs(self):
        get_tool_schemas()
        captured: list = []

        async def _fake(**kwargs):
            captured.append(kwargs)
            return {"ok": True}

        agent_module._TOOL_REGISTRY["brain_scan"]["execute"] = _fake
        _run(execute_tool("brain_scan", media_path="/tmp/v.mp4"))
        assert captured[0]["media_path"] == "/tmp/v.mp4"

    def test_tool_exception_returns_tool_error(self):
        get_tool_schemas()

        async def _bad(**kwargs):
            raise ValueError("boom")

        agent_module._TOOL_REGISTRY["narrate"]["execute"] = _bad
        result = _run(execute_tool("narrate"))
        assert result["ok"] is False
        assert result["error"] == "tool_error"
        assert "boom" in result.get("message", "")

    def test_execute_narrate_happy_path(self):
        get_tool_schemas()

        async def _ok(**kwargs):
            return {"ok": True, "narration": "great story"}

        agent_module._TOOL_REGISTRY["narrate"]["execute"] = _ok
        result = _run(execute_tool("narrate"))
        assert result["ok"] is True

    def test_execute_visualize_happy_path(self):
        get_tool_schemas()
        result = _run(execute_tool("visualize"))
        assert result.get("ok") is True

    def test_execute_describe_input_happy_path(self):
        get_tool_schemas()
        result = _run(execute_tool("describe_input"))
        assert result.get("ok") is True


# ---------------------------------------------------------------------------
# CortexAgent.__init__
# ---------------------------------------------------------------------------

class TestCortexAgentInit:
    def setup_method(self):
        _clear_registry()
        _FAKE_SCHEDULER.ensure_gemma.reset_mock()

    def test_init_stores_fake_scheduler(self):
        a = CortexAgent()
        assert a._scheduler is _FAKE_SCHEDULER

    def test_init_loads_tools_into_registry(self):
        CortexAgent()
        assert len(agent_module._TOOL_REGISTRY) == 4

    def test_init_uses_config_ollama_url(self):
        from cortex import config
        a = CortexAgent()
        assert a._ollama_url == config.OLLAMA_URL

    def test_init_accepts_custom_url(self):
        a = CortexAgent(ollama_url="http://custom:9999")
        assert a._ollama_url == "http://custom:9999"

    def test_history_starts_empty(self):
        assert CortexAgent()._history == []


# ---------------------------------------------------------------------------
# CortexAgent.tools property
# ---------------------------------------------------------------------------

class TestCortexAgentTools:
    def setup_method(self):
        _clear_registry()

    def test_tools_returns_list(self):
        assert isinstance(CortexAgent().tools, list)

    def test_tools_has_four_entries(self):
        assert len(CortexAgent().tools) == 4

    def test_tools_names_match_get_tool_schemas(self):
        a = CortexAgent()
        assert {s["name"] for s in a.tools} == {s["name"] for s in get_tool_schemas()}


# ---------------------------------------------------------------------------
# CortexAgent.startup
# ---------------------------------------------------------------------------

class TestCortexAgentStartup:
    def setup_method(self):
        _clear_registry()
        _FAKE_SCHEDULER.ensure_gemma.reset_mock()

    def test_startup_calls_ensure_gemma(self):
        _run(CortexAgent().startup())
        _FAKE_SCHEDULER.ensure_gemma.assert_called_once()

    def test_startup_does_not_raise(self):
        _run(CortexAgent().startup())   # should not throw


# ---------------------------------------------------------------------------
# CortexAgent.run_tool
# ---------------------------------------------------------------------------

class TestCortexAgentRunTool:
    def setup_method(self):
        _clear_registry()

    def test_run_tool_delegates_to_execute_tool(self):
        a = CortexAgent()
        get_tool_schemas()
        captured: list = []

        async def _fake(**kwargs):
            captured.append(kwargs)
            return {"ok": True}

        agent_module._TOOL_REGISTRY["brain_scan"]["execute"] = _fake
        result = _run(a.run_tool("brain_scan", media_path="/f.mp4"))
        assert result["ok"] is True
        assert captured[0]["media_path"] == "/f.mp4"

    def test_run_tool_unknown_returns_error(self):
        result = _run(CortexAgent().run_tool("nonexistent"))
        assert result["ok"] is False
        assert result["error"] == "unknown_tool"

    def test_run_tool_exception_returns_error(self):
        get_tool_schemas()

        async def _raises(**kwargs):
            raise RuntimeError("run_tool failure")

        agent_module._TOOL_REGISTRY["visualize"]["execute"] = _raises
        result = _run(CortexAgent().run_tool("visualize"))
        assert result["ok"] is False
        assert result["error"] == "tool_error"


# ---------------------------------------------------------------------------
# CortexAgent.chat
# ---------------------------------------------------------------------------

class TestCortexAgentChat:
    def setup_method(self):
        _clear_registry()
        _fake_oc.generate.reset_mock()
        _fake_oc.generate.return_value = "test response"
        # Re-install our fake; other zzz test modules may have swapped
        # sys.modules["cortex.ollama_client"] during collection.  chat() uses
        # a local import so it re-reads sys.modules on every call.
        sys.modules["cortex.ollama_client"] = _fake_oc

    def test_chat_returns_string(self):
        assert isinstance(_run(CortexAgent().chat("Hello")), str)

    def test_chat_returns_generate_output(self):
        _fake_oc.generate.return_value = "cortex reply"
        assert _run(CortexAgent().chat("analyze this")) == "cortex reply"

    def test_chat_calls_generate_exactly_once(self):
        _run(CortexAgent().chat("hello"))
        assert _fake_oc.generate.call_count == 1

    def test_chat_uses_fast_model(self):
        from cortex import config
        _run(CortexAgent().chat("hello"))
        assert _fake_oc.generate.call_args.kwargs.get("model") == config.OLLAMA_MODEL_FAST

    def test_chat_appends_user_and_assistant_to_history(self):
        a = CortexAgent()
        _run(a.chat("first message"))
        assert len(a._history) == 2
        assert a._history[0]["role"] == "user"
        assert a._history[1]["role"] == "assistant"

    def test_chat_stores_user_message(self):
        a = CortexAgent()
        _run(a.chat("unique user query xyz"))
        assert a._history[0]["content"] == "unique user query xyz"

    def test_chat_stores_assistant_response(self):
        _fake_oc.generate.return_value = "assistant reply abc"
        a = CortexAgent()
        _run(a.chat("hello"))
        assert a._history[1]["content"] == "assistant reply abc"

    def test_multiple_turns_accumulate_history(self):
        a = CortexAgent()
        _run(a.chat("first"))
        _run(a.chat("second"))
        assert len(a._history) == 4


# ---------------------------------------------------------------------------
# CortexAgent.gpu_status
# ---------------------------------------------------------------------------

class TestCortexAgentGpuStatus:
    def setup_method(self):
        _clear_registry()
        _FAKE_SCHEDULER.vram_report.reset_mock()
        _FAKE_SCHEDULER.vram_report.return_value = {"free_gb": 20.0, "state": "gemma_active"}

    def test_gpu_status_returns_dict(self):
        assert isinstance(CortexAgent().gpu_status(), dict)

    def test_gpu_status_calls_vram_report_once(self):
        CortexAgent().gpu_status()
        _FAKE_SCHEDULER.vram_report.assert_called_once()

    def test_gpu_status_returns_vram_report_value(self):
        assert CortexAgent().gpu_status() == {"free_gb": 20.0, "state": "gemma_active"}
