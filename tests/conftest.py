"""Shared pytest fixtures for the Cortex test suite.

Most fixtures here mock out hardware and network: nvidia-smi, Ollama,
torch.cuda, subprocess.run for FFmpeg. Tests that need the real
counterparts should use markers (`@pytest.mark.gpu`, `@pytest.mark.ollama`,
`@pytest.mark.tribe`) and be excluded from CI.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Make the project root importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect log output to a temp dir so tests don't pollute logs/."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    return log_dir


@pytest.fixture
def tmp_outputs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setenv("OUTPUTS_DIR", str(out))
    return out


# ---------------------------------------------------------------------------
# Hardware mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_nvidia_smi(monkeypatch: pytest.MonkeyPatch):
    """Patch subprocess.run so nvidia-smi calls return predictable VRAM values.

    Usage::

        def test_x(scheduler, mock_nvidia_smi):
            mock_nvidia_smi(free_mb=20480, used_mb=11264)
            assert scheduler.get_free_vram_gb() == 20.0
    """
    import subprocess as _subprocess

    state = {"free_mb": 30000.0, "used_mb": 2000.0}

    def _fake_run(cmd: list[str], *args: Any, **kwargs: Any):
        result = MagicMock()
        if cmd and cmd[0] == "nvidia-smi":
            joined = " ".join(cmd)
            if "memory.free" in joined:
                result.stdout = f"{state['free_mb']}\n"
            elif "memory.used" in joined:
                result.stdout = f"{state['used_mb']}\n"
            else:
                result.stdout = ""
            result.returncode = 0
            return result
        # Fall through to real subprocess for non-nvidia-smi calls
        return _subprocess._real_run(cmd, *args, **kwargs)

    # Save the real run if not already saved
    if not hasattr(_subprocess, "_real_run"):
        _subprocess._real_run = _subprocess.run

    def _setup(free_mb: float = 30000.0, used_mb: float = 2000.0):
        state["free_mb"] = free_mb
        state["used_mb"] = used_mb
        monkeypatch.setattr("subprocess.run", _fake_run)

    return _setup


@pytest.fixture
def mock_torch_cuda(monkeypatch: pytest.MonkeyPatch):
    """Stub torch.cuda calls used by the GPU scheduler."""
    cuda = MagicMock()
    cuda.empty_cache = MagicMock()
    cuda.is_available = MagicMock(return_value=True)
    cuda.get_device_properties = MagicMock(return_value=MagicMock(total_mem=32 * 1024**3))
    cuda.memory_allocated = MagicMock(return_value=10 * 1024**3)
    cuda.memory_reserved = MagicMock(return_value=12 * 1024**3)
    cuda.max_memory_allocated = MagicMock(return_value=22 * 1024**3)
    cuda.OutOfMemoryError = type("OutOfMemoryError", (RuntimeError,), {})

    fake_torch = MagicMock()
    fake_torch.cuda = cuda
    sys.modules["torch"] = fake_torch
    return cuda


# ---------------------------------------------------------------------------
# Network mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_requests_post(monkeypatch: pytest.MonkeyPatch):
    """Patch requests.post and capture every call.

    Returns the list of captured calls so tests can assert on them.
    Each entry is `{"url": ..., "json": ..., "timeout": ...}`.
    """
    posted: list[dict[str, Any]] = []

    def _fake_post(url: str, json: dict | None = None, timeout: int | None = None, **kwargs: Any):
        posted.append({"url": url, "json": json, "timeout": timeout, "kwargs": kwargs})
        result = MagicMock()
        result.json.return_value = {
            "response": "mocked text",
            "done": True,
            "eval_count": 100,
            "eval_duration": 1_000_000_000,
        }
        result.raise_for_status = MagicMock()
        result.status_code = 200
        return result

    monkeypatch.setattr("requests.post", _fake_post)
    return posted


# ---------------------------------------------------------------------------
# Module-level mocks (cortex internals)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_model_manager(monkeypatch: pytest.MonkeyPatch):
    """Replace the singleton model manager with a MagicMock."""
    fake = MagicMock()
    fake.warm_fast_model = MagicMock()
    fake.fast_model.return_value = "gemma4:e4b"
    fake.deep_model.return_value = "gemma4:26b"
    fake.expert_model.return_value = "gemma4:31b"
    monkeypatch.setattr("cortex.gpu_scheduler.get_manager", lambda: fake)
    return fake


@pytest.fixture
def mock_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Stub out pipeline.load_model and pipeline.run_inference."""
    fake_model = MagicMock(name="fake-tribe-model")
    fake_load = MagicMock(return_value=fake_model)
    fake_run = MagicMock(name="run_inference")

    # The scheduler imports pipeline lazily; patch via sys.modules
    fake_pipeline = MagicMock()
    fake_pipeline.load_model = fake_load
    fake_pipeline.run_inference = fake_run
    fake_pipeline._model = None
    fake_pipeline._compiled = False
    sys.modules["cortex.pipeline"] = fake_pipeline
    return fake_pipeline


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def event_loop_policy():
    """Override pytest-asyncio's event loop policy to a clean one per test."""
    return asyncio.DefaultEventLoopPolicy()


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="e2e requires running server"))
