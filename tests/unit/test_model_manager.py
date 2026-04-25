"""Tests for cortex.model_manager — Gemma 4 tier swapping in Ollama."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cortex.model_manager import ModelManager, ModelTier, get_manager

pytestmark = pytest.mark.unit


class TestModelTierEnum:
    def test_ordering_by_size(self):
        # Lower int = lighter model. FAST is the smallest, EXPERT the largest.
        assert ModelTier.FAST < ModelTier.DEEP < ModelTier.EXPERT


class TestModelManager:
    def test_singleton_is_idempotent(self):
        a = get_manager()
        b = get_manager()
        assert a is b

    def test_model_name_lookup(self):
        m = ModelManager()
        assert m.fast_model().startswith("gemma4:")
        assert m.deep_model().startswith("gemma4:")
        assert m.expert_model().startswith("gemma4:")

    def test_tier_to_model_resolution(self):
        m = ModelManager()
        assert m.model_name(ModelTier.FAST) == m.fast_model()
        assert m.model_name(ModelTier.DEEP) == m.deep_model()
        assert m.model_name(ModelTier.EXPERT) == m.expert_model()

    def test_warm_fast_model_calls_ollama_with_permanent_keep_alive(self, monkeypatch):
        m = ModelManager()
        captured: list[dict] = []

        def _fake_post(url, json=None, **kwargs):
            captured.append({"url": url, "json": json})
            r = MagicMock()
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", _fake_post)
        m.warm_fast_model()
        assert m._status[ModelTier.FAST].loaded is True
        assert len(captured) == 1
        body = captured[0]["json"]
        assert body["model"] == m.fast_model()
        # 60-minute keep_alive is the "permanent" knob in this codebase.
        assert body["keep_alive"] == "60m"


class TestSwapInOut:
    @pytest.mark.asyncio
    async def test_fast_tier_is_already_warm(self, monkeypatch):
        m = ModelManager()
        captured: list[dict] = []
        monkeypatch.setattr(
            "requests.post",
            lambda *a, json=None, **kw: (captured.append(json), MagicMock(raise_for_status=MagicMock()))[1],
        )
        result = await m.swap_in(ModelTier.FAST)
        assert result == m.fast_model()
        # FAST tier never triggers a load
        assert captured == []

    @pytest.mark.asyncio
    async def test_swap_in_deep_unloads_other_active(self, monkeypatch):
        m = ModelManager()
        captured: list[dict] = []

        def _fake_post(url, json=None, **kwargs):
            captured.append(json)
            r = MagicMock()
            r.raise_for_status = MagicMock()
            return r

        monkeypatch.setattr("requests.post", _fake_post)

        # Pretend EXPERT is currently loaded
        m._active_tier = ModelTier.EXPERT
        m._status[ModelTier.EXPERT].loaded = True

        await m.swap_in(ModelTier.DEEP)
        # We expect: 1 unload (EXPERT keep_alive=0s) + 1 preload (DEEP)
        assert len(captured) == 2
        unload, preload = captured
        assert unload["model"] == m.expert_model()
        assert unload["keep_alive"] == "0s"
        assert preload["model"] == m.deep_model()
        assert preload["keep_alive"] != "0s"

    @pytest.mark.asyncio
    async def test_swap_out_fast_is_noop(self, monkeypatch):
        m = ModelManager()
        captured: list[dict] = []
        monkeypatch.setattr(
            "requests.post",
            lambda *a, json=None, **kw: (captured.append(json), MagicMock(raise_for_status=MagicMock()))[1],
        )
        await m.swap_out(ModelTier.FAST)
        assert captured == []  # Never unloads FAST


class TestStatusReport:
    def test_status_report_lists_each_tier(self):
        m = ModelManager()
        line = m.status_report()
        assert m.fast_model() in line
        assert m.deep_model() in line
        assert m.expert_model() in line


class TestTrackCall:
    def test_first_call_sets_baseline(self):
        m = ModelManager()
        m.track_call(ModelTier.FAST, 100.0)
        assert m._status[ModelTier.FAST].tok_per_s == 100.0
        assert m._status[ModelTier.FAST].total_calls == 1

    def test_subsequent_calls_use_ema(self):
        m = ModelManager()
        m.track_call(ModelTier.FAST, 100.0)
        m.track_call(ModelTier.FAST, 200.0)
        # 0.9 * 100 + 0.1 * 200 = 110
        assert m._status[ModelTier.FAST].tok_per_s == pytest.approx(110.0)
        assert m._status[ModelTier.FAST].total_calls == 2
