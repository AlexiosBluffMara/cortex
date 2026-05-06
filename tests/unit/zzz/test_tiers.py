"""Tests for cortex.tiers — seven-tier Gemma narration routing."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Patch only the callables / attributes that tiers.py reaches at runtime.
# We do NOT replace the top-level packages — cortex and its sub-packages are
# real; we just swap out the leaf callables.
# ---------------------------------------------------------------------------

# Ensure real cortex package is loaded so sys.modules["cortex"] is the
# real package object, not a stub.
import cortex  # noqa: E402 — real package
import cortex.config as _real_config  # noqa: E402
import cortex.prompts as _real_prompts  # noqa: E402

# Inject a fake ollama_client submodule so tiers.py's `from . import ollama_client`
# resolves without a real Ollama server.
_fake_oc = types.ModuleType("cortex.ollama_client")
_fake_oc.generate = MagicMock(return_value="mocked narration")
sys.modules["cortex.ollama_client"] = _fake_oc
# Also attach it to the package so `from cortex import ollama_client` works.
cortex.ollama_client = _fake_oc  # type: ignore[attr-defined]

# Inject a fake pipeline submodule so `from .pipeline import InferenceResult` works.
_fake_pipeline = types.ModuleType("cortex.pipeline")


class _FakeInferenceResult:
    def __init__(self, preds, roi_df, top_rois, peak_t, seconds_elapsed=1.0):
        self.preds            = preds
        self.roi_df           = roi_df
        self.top_rois         = top_rois
        self.peak_t           = peak_t
        self.seconds_elapsed  = seconds_elapsed
        self.model_dtype      = "bf16"


_fake_pipeline.InferenceResult = _FakeInferenceResult
sys.modules["cortex.pipeline"] = _fake_pipeline
cortex.pipeline = _fake_pipeline  # type: ignore[attr-defined]

# Now tiers.py can be imported
from cortex import tiers  # noqa: E402
from cortex.tiers import (  # noqa: E402
    TieredNarration,
    _build_user_prompt,
    _legacy_user_prompt,
    _roi_lines,
    narrate_all_tiers,
    narrate_quick,
    narrate_tier,
    narrate_tiered,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(n_timepoints: int = 20, n_rois: int = 5, peak_t: int = 10):
    top_rois = [f"roi_{i}" for i in range(n_rois)]
    data = {roi: np.abs(np.random.default_rng(0).standard_normal(n_timepoints))
            for roi in top_rois}
    roi_df = pd.DataFrame(data)
    preds  = np.zeros((n_timepoints, 20484), dtype=np.float32)
    return _FakeInferenceResult(preds=preds, roi_df=roi_df, top_rois=top_rois, peak_t=peak_t)


def _reset_gen(return_value: str = "mocked narration"):
    _fake_oc.generate.reset_mock()
    _fake_oc.generate.return_value = return_value
    return _fake_oc.generate


# ---------------------------------------------------------------------------
# TieredNarration dataclass
# ---------------------------------------------------------------------------

class TestTieredNarration:
    def test_as_dict_returns_correct_keys(self):
        tn = TieredNarration(layperson="lay", clinician="clin", researcher="res")
        assert set(tn.as_dict().keys()) == {"layperson", "clinician", "researcher"}

    def test_as_dict_values_match_fields(self):
        tn = TieredNarration(layperson="L", clinician="C", researcher="R")
        d = tn.as_dict()
        assert d["layperson"] == "L"
        assert d["clinician"] == "C"
        assert d["researcher"] == "R"

    def test_field_direct_access(self):
        tn = TieredNarration(layperson="a", clinician="b", researcher="c")
        assert tn.layperson == "a"
        assert tn.clinician == "b"
        assert tn.researcher == "c"

    def test_as_dict_returns_dict_type(self):
        tn = TieredNarration(layperson="x", clinician="y", researcher="z")
        assert isinstance(tn.as_dict(), dict)


# ---------------------------------------------------------------------------
# _roi_lines
# ---------------------------------------------------------------------------

class TestRoiLines:
    def test_returns_string(self):
        result = _make_result(n_rois=5)
        assert isinstance(_roi_lines(result), str)

    def test_contains_roi_names(self):
        result = _make_result(n_rois=3)
        out = _roi_lines(result)
        for roi in result.top_rois:
            assert roi in out

    def test_default_n_limits_to_8(self):
        result = _make_result(n_rois=12)
        lines = [l for l in _roi_lines(result).split("\n") if l.strip()]
        assert len(lines) == 8

    def test_custom_n_parameter(self):
        result = _make_result(n_rois=5)
        lines = [l for l in _roi_lines(result, n=3).split("\n") if l.strip()]
        assert len(lines) == 3

    def test_contains_mean_z_label(self):
        result = _make_result(n_rois=2)
        assert "mean |z|" in _roi_lines(result)

    def test_fewer_rois_than_n(self):
        result = _make_result(n_rois=2)
        lines = [l for l in _roi_lines(result, n=8).split("\n") if l.strip()]
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# _build_user_prompt
# ---------------------------------------------------------------------------

class TestBuildUserPrompt:
    def test_label_in_prompt(self):
        assert "test_label" in _build_user_prompt("test_label", "ctx")

    def test_brain_context_in_prompt(self):
        assert "ctx_value" in _build_user_prompt("lbl", "ctx_value")

    def test_uses_tier_user_template_format(self):
        original = _real_prompts.TIER_USER_TEMPLATE
        _real_prompts.TIER_USER_TEMPLATE = "L={label} C={brain_context}"
        try:
            out = _build_user_prompt("A", "B")
            assert "L=A" in out
            assert "C=B" in out
        finally:
            _real_prompts.TIER_USER_TEMPLATE = original


# ---------------------------------------------------------------------------
# _legacy_user_prompt
# ---------------------------------------------------------------------------

class TestLegacyUserPrompt:
    def test_label_in_prompt(self):
        result = _make_result()
        assert "my_label" in _legacy_user_prompt(result, "my_label")

    def test_peak_time_in_prompt(self):
        result = _make_result(n_timepoints=20, peak_t=10)
        # peak_s = 10/2.0 = 5.0
        assert "5.0s" in _legacy_user_prompt(result, "lbl")

    def test_duration_in_prompt(self):
        result = _make_result(n_timepoints=20)
        # duration_s = 20/2.0 = 10.0
        assert "10.0s" in _legacy_user_prompt(result, "lbl")

    def test_roi_lines_included(self):
        result = _make_result(n_rois=3)
        out = _legacy_user_prompt(result, "lbl")
        for roi in result.top_rois:
            assert roi in out


# ---------------------------------------------------------------------------
# narrate_tier
# ---------------------------------------------------------------------------

class TestNarrateTier:
    def setup_method(self):
        self.gen = _reset_gen("tier narration")
        self.result = _make_result()

    def _model_from_call(self):
        return self.gen.call_args.kwargs.get("model", "")

    def test_tier_0_uses_fast_model(self):
        narrate_tier(self.result, "label", tier=0)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_FAST

    def test_tier_1_uses_fast_model(self):
        narrate_tier(self.result, "label", tier=1)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_FAST

    def test_tier_2_uses_deep_model(self):
        narrate_tier(self.result, "label", tier=2)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_DEEP

    def test_tier_4_uses_deep_model(self):
        narrate_tier(self.result, "label", tier=4)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_DEEP

    def test_tier_5_uses_expert_model(self):
        narrate_tier(self.result, "label", tier=5)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_EXPERT

    def test_tier_6_uses_expert_model(self):
        narrate_tier(self.result, "label", tier=6)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_EXPERT

    def test_clamps_tier_below_zero(self):
        narrate_tier(self.result, "label", tier=-3)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_FAST

    def test_clamps_tier_above_six(self):
        narrate_tier(self.result, "label", tier=99)
        assert self._model_from_call() == _real_config.OLLAMA_MODEL_EXPERT

    def test_returns_generate_response(self):
        self.gen.return_value = "unique_xyz"
        assert narrate_tier(self.result, "label", tier=0) == "unique_xyz"

    def test_with_brain_context_uses_build_prompt(self):
        narrate_tier(self.result, "label", tier=1, brain_context="ctx_abc")
        assert "ctx_abc" in self.gen.call_args.kwargs.get("prompt", "")

    def test_without_brain_context_uses_legacy_prompt(self):
        result = _make_result(n_rois=2)
        narrate_tier(result, "label", tier=1, brain_context=None)
        prompt = self.gen.call_args.kwargs.get("prompt", "")
        for roi in result.top_rois:
            assert roi in prompt

    def test_think_false_passed(self):
        narrate_tier(self.result, "label", tier=0)
        assert self.gen.call_args.kwargs.get("think") is False

    def test_calls_generate_exactly_once(self):
        narrate_tier(self.result, "label", tier=0)
        assert self.gen.call_count == 1


# ---------------------------------------------------------------------------
# narrate_tiered
# ---------------------------------------------------------------------------

class TestNarrateTiered:
    def setup_method(self):
        self.gen = _reset_gen()
        self.result = _make_result()

    def test_returns_tiered_narration_instance(self):
        assert isinstance(narrate_tiered(self.result, "lbl"), TieredNarration)

    def test_generate_called_three_times(self):
        narrate_tiered(self.result, "lbl")
        assert self.gen.call_count == 3

    def test_fields_populated_with_generate_return(self):
        self.gen.return_value = "fixed_text"
        tn = narrate_tiered(self.result, "lbl")
        assert tn.layperson  == "fixed_text"
        assert tn.clinician  == "fixed_text"
        assert tn.researcher == "fixed_text"

    def test_layperson_uses_deep_model(self):
        narrate_tiered(self.result, "lbl")
        models = [c.kwargs.get("model") for c in self.gen.call_args_list]
        assert _real_config.OLLAMA_MODEL_DEEP in models

    def test_clinician_and_researcher_use_expert_model(self):
        narrate_tiered(self.result, "lbl")
        models = [c.kwargs.get("model") for c in self.gen.call_args_list]
        assert models.count(_real_config.OLLAMA_MODEL_EXPERT) == 2

    def test_with_brain_context(self):
        narrate_tiered(self.result, "lbl", brain_context="brain_ctx_test")
        for c in self.gen.call_args_list:
            assert "brain_ctx_test" in c.kwargs.get("prompt", "")


# ---------------------------------------------------------------------------
# narrate_quick
# ---------------------------------------------------------------------------

class TestNarrateQuick:
    def setup_method(self):
        self.gen = _reset_gen("quick narration")
        self.result = _make_result(n_rois=5)

    def test_uses_fast_model(self):
        narrate_quick(self.result, "desc")
        assert self.gen.call_args.kwargs.get("model") == _real_config.OLLAMA_MODEL_FAST

    def test_returns_generate_value(self):
        self.gen.return_value = "quickval"
        assert narrate_quick(self.result, "desc") == "quickval"

    def test_description_in_prompt(self):
        narrate_quick(self.result, "my unique description")
        assert "my unique description" in self.gen.call_args.kwargs.get("prompt", "")

    def test_uses_quick_narration_system(self):
        narrate_quick(self.result, "desc")
        system = self.gen.call_args.kwargs.get("system", "")
        assert system == _real_prompts.QUICK_NARRATION_SYSTEM

    def test_calls_generate_exactly_once(self):
        narrate_quick(self.result, "desc")
        assert self.gen.call_count == 1

    def test_think_false_passed(self):
        narrate_quick(self.result, "desc")
        assert self.gen.call_args.kwargs.get("think") is False

    def test_peak_time_in_prompt(self):
        result = _make_result(n_timepoints=20, peak_t=10)
        narrate_quick(result, "desc")
        # peak_s = 10/2.0 = 5.0
        assert "5.0" in self.gen.call_args.kwargs.get("prompt", "")


# ---------------------------------------------------------------------------
# narrate_all_tiers
# ---------------------------------------------------------------------------

class TestNarrateAllTiers:
    def setup_method(self):
        self.gen = _reset_gen()
        self.result = _make_result()

    def test_returns_dict(self):
        assert isinstance(narrate_all_tiers(self.result, "lbl"), dict)

    def test_dict_has_keys_0_through_6(self):
        out = narrate_all_tiers(self.result, "lbl")
        assert set(out.keys()) == {0, 1, 2, 3, 4, 5, 6}

    def test_generate_called_seven_times(self):
        narrate_all_tiers(self.result, "lbl")
        assert self.gen.call_count == 7

    def test_values_are_strings(self):
        self.gen.return_value = "txt"
        for v in narrate_all_tiers(self.result, "lbl").values():
            assert isinstance(v, str)

    def test_brain_context_passed_through(self):
        narrate_all_tiers(self.result, "lbl", brain_context="all_ctx")
        for c in self.gen.call_args_list:
            assert "all_ctx" in c.kwargs.get("prompt", "")
