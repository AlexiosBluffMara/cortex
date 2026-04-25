"""Tests for cortex.tiers — narration tier → model routing."""
from __future__ import annotations

import pytest

from cortex import config
from cortex.tiers import (
    _TIER_KEEP_ALIVE,
    _TIER_MODEL_MAP,
    _TIER_NUM_CTX,
    _TIER_NUM_PREDICT,
    _TIER_TEMPERATURE,
    TieredNarration,
)

pytestmark = pytest.mark.unit


class TestTierToModelMapping:
    def test_tiers_0_and_1_use_fast_model(self):
        assert _TIER_MODEL_MAP[0] == config.OLLAMA_MODEL_FAST
        assert _TIER_MODEL_MAP[1] == config.OLLAMA_MODEL_FAST

    def test_tiers_2_to_4_use_deep_model(self):
        for t in (2, 3, 4):
            assert _TIER_MODEL_MAP[t] == config.OLLAMA_MODEL_DEEP

    def test_tiers_5_and_6_use_expert_model(self):
        assert _TIER_MODEL_MAP[5] == config.OLLAMA_MODEL_EXPERT
        assert _TIER_MODEL_MAP[6] == config.OLLAMA_MODEL_EXPERT

    def test_seven_tiers_total(self):
        # The system is 7-tier (0..6), not 6 or 8.
        assert set(_TIER_MODEL_MAP.keys()) == {0, 1, 2, 3, 4, 5, 6}


class TestPerTierBudgets:
    def test_num_predict_increases_with_tier(self):
        # Higher tier = more depth = more tokens budgeted.
        for i in range(len(_TIER_NUM_PREDICT) - 1):
            assert _TIER_NUM_PREDICT[i] <= _TIER_NUM_PREDICT[i + 1]

    def test_temperature_decreases_with_tier(self):
        # Higher tier = more factual = lower temperature.
        for i in range(len(_TIER_TEMPERATURE) - 1):
            assert _TIER_TEMPERATURE[i] >= _TIER_TEMPERATURE[i + 1]

    def test_num_ctx_non_decreasing(self):
        for i in range(len(_TIER_NUM_CTX) - 1):
            assert _TIER_NUM_CTX[i] <= _TIER_NUM_CTX[i + 1]

    def test_seven_values_per_array(self):
        for arr in (_TIER_NUM_PREDICT, _TIER_TEMPERATURE, _TIER_NUM_CTX, _TIER_KEEP_ALIVE):
            assert len(arr) == 7


class TestTieredNarrationDataclass:
    def test_as_dict_has_three_keys(self):
        tn = TieredNarration(layperson="lay", clinician="clin", researcher="res")
        d = tn.as_dict()
        assert set(d.keys()) == {"layperson", "clinician", "researcher"}
        assert d["clinician"] == "clin"

    def test_field_access(self):
        tn = TieredNarration(layperson="a", clinician="b", researcher="c")
        assert tn.layperson == "a"
        assert tn.clinician == "b"
        assert tn.researcher == "c"
