"""Tests for cortex.gpu_scheduler — exclusive GPU access state machine."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cortex.gpu_scheduler import GPUScheduler, GPUState, SwapMetrics

pytestmark = pytest.mark.unit


@pytest.fixture
def scheduler(mock_model_manager):
    s = GPUScheduler()
    return s


class TestSwapMetrics:
    def test_avg_swap_time_handles_zero_swaps(self):
        m = SwapMetrics()
        assert m.avg_swap_time_s == 0.0

    def test_avg_swap_time_after_swaps(self):
        m = SwapMetrics(total_swaps=2, total_swap_time_s=20.0)
        assert m.avg_swap_time_s == 10.0


class TestVramReport:
    def test_initial_state_is_idle(self, scheduler):
        assert scheduler.state is GPUState.IDLE

    def test_vram_report_shape(self, scheduler, mock_nvidia_smi):
        mock_nvidia_smi(free_mb=30000, used_mb=2000)
        report = scheduler.vram_report()
        assert report["state"] == "idle"
        assert report["total_gb"] == 32.0
        assert report["free_gb"] == pytest.approx(29.30, abs=0.1)
        assert report["used_gb"] == pytest.approx(1.95, abs=0.1)
        assert report["tribe_fits"] is True
        assert report["gemma_e4b_fits"] is True
        assert "swap_metrics" in report

    def test_vram_report_when_full(self, scheduler, mock_nvidia_smi):
        mock_nvidia_smi(free_mb=8000, used_mb=24000)
        report = scheduler.vram_report()
        assert report["tribe_fits"] is False
        assert report["gemma_e4b_fits"] is False  # 7.8GB free < 10GB threshold

    def test_get_free_vram_returns_zero_on_error(self, scheduler, monkeypatch):
        def _fail(*args, **kwargs):
            raise OSError("nvidia-smi not found")
        monkeypatch.setattr("subprocess.run", _fail)
        assert scheduler.get_free_vram_gb() == 0.0
        assert scheduler.get_used_vram_gb() == 0.0


class TestStateChangeListeners:
    def test_listener_registered_and_fired(self, scheduler):
        observed = []
        scheduler.on_state_change(lambda s: observed.append(s))
        scheduler._notify_state(GPUState.GEMMA_ACTIVE)
        assert observed == [GPUState.GEMMA_ACTIVE]

    def test_listener_exception_is_swallowed(self, scheduler):
        # A buggy listener must not break the scheduler.
        scheduler.on_state_change(lambda s: 1 / 0)
        scheduler._notify_state(GPUState.GEMMA_ACTIVE)
        assert scheduler.state is GPUState.GEMMA_ACTIVE


class TestEnsureGemma:
    @pytest.mark.asyncio
    async def test_idle_to_gemma(self, scheduler, mock_nvidia_smi):
        mock_nvidia_smi(free_mb=30000, used_mb=2000)
        await scheduler.ensure_gemma()
        assert scheduler.state is GPUState.GEMMA_ACTIVE
        scheduler._mm.warm_fast_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_gemma_to_gemma_is_idempotent(self, scheduler):
        scheduler._state = GPUState.GEMMA_ACTIVE
        await scheduler.ensure_gemma()
        scheduler._mm.warm_fast_model.assert_not_called()


class TestEnsureTribe:
    @pytest.mark.asyncio
    async def test_idle_to_tribe(self, scheduler, mock_pipeline, mock_nvidia_smi):
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        await scheduler.ensure_tribe()
        assert scheduler.state is GPUState.TRIBE_ACTIVE
        mock_pipeline.load_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_gemma_to_tribe_unloads_then_loads(
        self, scheduler, mock_pipeline, mock_nvidia_smi, mock_requests_post
    ):
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        scheduler._state = GPUState.GEMMA_ACTIVE
        await scheduler.ensure_tribe()
        # All Gemma tags should have been issued an unload (keep_alive=0s)
        unloaded = [p["json"]["model"] for p in mock_requests_post]
        for tag in ("gemma4:e4b", "gemma4:26b", "gemma4:31b"):
            assert tag in unloaded
        # Every unload payload was 0s
        for p in mock_requests_post:
            assert p["json"]["keep_alive"] == "0s"
        mock_pipeline.load_model.assert_called_once()
        assert scheduler.metrics.total_swaps == 1
        assert scheduler.metrics.last_swap_time_s >= 0.0

    @pytest.mark.asyncio
    async def test_tribe_to_tribe_idempotent(self, scheduler, mock_pipeline):
        scheduler._state = GPUState.TRIBE_ACTIVE
        await scheduler.ensure_tribe()
        mock_pipeline.load_model.assert_not_called()


class TestSwapTimeout:
    @pytest.mark.asyncio
    async def test_vram_timeout_raises_runtime_error(
        self, scheduler, mock_pipeline, monkeypatch
    ):
        # Free VRAM never recovers — every nvidia-smi reads 1GB free
        def _fake_run(cmd, *args, **kwargs):
            r = MagicMock()
            if cmd and cmd[0] == "nvidia-smi" and "free" in " ".join(cmd):
                r.stdout = "1024\n"
            elif cmd and cmd[0] == "nvidia-smi":
                r.stdout = "31000\n"
            else:
                r.stdout = ""
            return r
        monkeypatch.setattr("subprocess.run", _fake_run)

        # Mock requests.post for unload calls (avoid network)
        def fake_post(*args, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            return r
        monkeypatch.setattr("requests.post", fake_post)

        # Compress the polling loop so the test is fast
        scheduler.VRAM_POLL_MAX_ATTEMPTS = 2
        scheduler.VRAM_POLL_INTERVAL_S = 0.01

        scheduler._state = GPUState.GEMMA_ACTIVE
        with pytest.raises(RuntimeError, match="VRAM stuck"):
            await scheduler.ensure_tribe()
        assert scheduler.metrics.failed_swaps >= 1


class TestStatusLine:
    def test_status_line_includes_state_and_vram(self, scheduler, mock_nvidia_smi):
        mock_nvidia_smi(free_mb=20480, used_mb=11520)
        line = scheduler.status_line()
        assert "idle" in line
        assert "VRAM" in line
        assert "Swaps" in line


class TestRunBrainScanLifecycle:
    @pytest.mark.asyncio
    async def test_run_brain_scan_swaps_back_to_gemma_by_default(
        self, scheduler, mock_pipeline, mock_nvidia_smi
    ):
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        result = await scheduler.run_brain_scan("/tmp/x.mp4")
        assert result is mock_pipeline.run_inference.return_value
        assert scheduler.state is GPUState.GEMMA_ACTIVE
        scheduler._mm.warm_fast_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_brain_scan_can_keep_tribe_loaded(
        self, scheduler, mock_pipeline, mock_nvidia_smi
    ):
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        result = await scheduler.run_brain_scan("/tmp/x.txt", keep_tribe_loaded=True)
        assert result is mock_pipeline.run_inference.return_value
        assert scheduler.state is GPUState.TRIBE_ACTIVE
        scheduler._mm.warm_fast_model.assert_not_called()


# ---------------------------------------------------------------------------
# OOM fallback
# ---------------------------------------------------------------------------

class TestOOMFallback:
    """OOM during local inference should route to a configured fallback,
    falling back gracefully to re-raising when no fallback is set.

    All tests in this class use `mock_torch_cuda` because the OOM cleanup
    path does a lazy `import torch` inside `_unload_tribe_sync` — without
    the stub the test crashes during cleanup, before we observe the
    behavior under test.
    """

    @pytest.mark.asyncio
    async def test_oom_with_no_fallback_reraises_runtime_error(
        self, scheduler, mock_pipeline, mock_nvidia_smi, mock_requests_post,
        mock_torch_cuda,
    ):
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        mock_pipeline.run_inference.side_effect = RuntimeError(
            "CUDA out of memory. Tried to allocate 22 GB"
        )
        # No fallback configured (the default)
        with pytest.raises(RuntimeError, match="out of memory"):
            await scheduler.run_brain_scan("/tmp/x.mp4")
        assert scheduler.metrics.oom_recoveries >= 1
        assert scheduler.state is GPUState.IDLE

    @pytest.mark.asyncio
    async def test_oom_with_unavailable_fallback_still_reraises(
        self, scheduler, mock_pipeline, mock_nvidia_smi, mock_requests_post,
        mock_torch_cuda,
    ):
        from unittest.mock import AsyncMock as _AM
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        mock_pipeline.run_inference.side_effect = RuntimeError(
            "CUDA out of memory."
        )
        # Fallback exists but unavailable (e.g. NullFallback) — preserve legacy
        # behavior of re-raising the original RuntimeError.
        fallback = MagicMock()
        fallback.available = MagicMock(return_value=False)
        fallback.submit = _AM()
        scheduler.set_inference_fallback(fallback)
        with pytest.raises(RuntimeError, match="out of memory"):
            await scheduler.run_brain_scan("/tmp/x.mp4")
        fallback.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_oom_with_available_fallback_returns_remote_result(
        self, scheduler, mock_pipeline, mock_nvidia_smi, mock_requests_post,
        mock_torch_cuda,
    ):
        from unittest.mock import AsyncMock as _AM

        from cortex.gcp_inference import RemoteInferenceResult
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        mock_pipeline.run_inference.side_effect = RuntimeError(
            "CUDA out of memory."
        )
        remote = RemoteInferenceResult(
            preds_url="gs://b/x.npy",
            top_rois=["V1", "FFA"],
            peak_t=8,
            seconds_elapsed=4.2,
            job_id="JOB-001",
        )
        fallback = MagicMock()
        fallback.name = "gcp_a100"
        fallback.available = MagicMock(return_value=True)
        fallback.submit = _AM(return_value=remote)
        scheduler.set_inference_fallback(fallback)

        result = await scheduler.run_brain_scan("/tmp/x.mp4")
        assert result is remote
        fallback.submit.assert_awaited_once_with("/tmp/x.mp4")
        # OOM recovery counter still bumps because we did detect+clean
        assert scheduler.metrics.oom_recoveries >= 1
        # Scheduler state was reset to IDLE before the fallback call
        assert scheduler.state is GPUState.IDLE

    @pytest.mark.asyncio
    async def test_oom_with_failing_fallback_raises_cortex_exception(
        self, scheduler, mock_pipeline, mock_nvidia_smi, mock_requests_post,
        mock_torch_cuda,
    ):
        from unittest.mock import AsyncMock as _AM

        from cortex.errors import CortexError, CortexException, ErrorCode
        mock_nvidia_smi(free_mb=28000, used_mb=4000)
        mock_pipeline.run_inference.side_effect = RuntimeError("CUDA out of memory.")
        err = CortexError(
            code=ErrorCode.GCP_NETWORK_TIMEOUT,
            message="GCP unreachable",
            component="gcp_inference",
        )
        fallback = MagicMock()
        fallback.name = "gcp_a100"
        fallback.available = MagicMock(return_value=True)
        fallback.submit = _AM(return_value=err)
        scheduler.set_inference_fallback(fallback)

        with pytest.raises(CortexException) as ei:
            await scheduler.run_brain_scan("/tmp/x.mp4")
        assert ei.value.error.code is ErrorCode.GCP_NETWORK_TIMEOUT
