"""Tests for scripts/regions, scripts/templates, scripts/backends, scripts/generate_neuro_dataset."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from scripts.backends import (
    AnthropicBackend,
    OllamaBackend,
    StubBackend,
    make_backend,
)
from scripts.generate_neuro_dataset import (
    DatasetExample,
    GeneratorConfig,
    build_example,
    generate,
)
from scripts.regions import (
    REGIONS,
    Network,
    all_networks_covered,
    by_abbreviation,
    by_network,
)
from scripts.templates import (
    SYSTEM_PROMPT,
    TemplateFamily,
    per_region_examples,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

class TestRegions:
    def test_at_least_one_per_network(self):
        # The dataset must span every Yeo network so the fine-tuned model
        # generalizes across functional families.
        assert all_networks_covered() is True

    def test_abbreviations_unique(self):
        abbrs = [r.abbreviation for r in REGIONS]
        assert len(abbrs) == len(set(abbrs)), f"duplicate abbreviation in {abbrs}"

    def test_each_region_has_required_data(self):
        for r in REGIONS:
            assert r.name, f"empty name on {r.abbreviation}"
            assert r.brodmann, f"empty brodmann on {r.abbreviation}"
            assert len(r.functions) >= 3, f"too few functions on {r.abbreviation}: {r.functions}"
            assert len(r.stimuli) >= 3, f"too few stimuli on {r.abbreviation}: {r.stimuli}"

    def test_by_abbreviation_lookup(self):
        v1 = by_abbreviation("V1")
        assert v1.network is Network.VISUAL
        assert v1.name.startswith("Primary Visual")

    def test_by_abbreviation_is_case_insensitive(self):
        assert by_abbreviation("ffa") is by_abbreviation("FFA")

    def test_by_abbreviation_raises_on_unknown(self):
        with pytest.raises(KeyError):
            by_abbreviation("DOES_NOT_EXIST")

    def test_by_network_returns_only_that_network(self):
        visual = by_network(Network.VISUAL)
        assert all(r.network is Network.VISUAL for r in visual)
        assert len(visual) >= 4  # V1, V2, V4, MT/V5, FFA, PPA at minimum

    def test_region_slug_is_filesystem_safe(self):
        for r in REGIONS:
            slug = r.slug()
            assert "/" not in slug
            assert " " not in slug
            assert slug == slug.lower()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

class TestTemplates:
    def test_system_prompt_brand(self):
        assert "Cortex" in SYSTEM_PROMPT
        assert "TRIBE v2" in SYSTEM_PROMPT
        # Hard-rule guarantees we test for in test_prompts.py too:
        assert "diagnos" in SYSTEM_PROMPT.lower()

    def test_per_region_yields_five_families_each(self):
        rng = random.Random(0)
        v1 = by_abbreviation("V1")
        instances = list(
            per_region_examples(v1, n_per_family=3, rng=rng, partner_pool=REGIONS)
        )
        # 5 families × 3 each = 15
        assert len(instances) == 15
        family_counts: dict[TemplateFamily, int] = {}
        for inst in instances:
            family_counts[inst.family] = family_counts.get(inst.family, 0) + 1
        for fam in TemplateFamily:
            assert family_counts[fam] == 3, f"family {fam} got {family_counts[fam]}"

    def test_zero_per_family_yields_nothing(self):
        rng = random.Random(0)
        v1 = by_abbreviation("V1")
        assert list(per_region_examples(v1, n_per_family=0, rng=rng, partner_pool=REGIONS)) == []

    def test_user_prompt_mentions_region(self):
        rng = random.Random(0)
        ffa = by_abbreviation("FFA")
        instances = list(per_region_examples(ffa, n_per_family=1, rng=rng, partner_pool=REGIONS))
        for inst in instances:
            assert "FFA" in inst.user_prompt or "Fusiform" in inst.user_prompt

    def test_metadata_includes_region_and_network(self):
        rng = random.Random(0)
        v1 = by_abbreviation("V1")
        for inst in per_region_examples(v1, n_per_family=1, rng=rng, partner_pool=REGIONS):
            assert inst.metadata["region"]
            assert inst.metadata["network"] == Network.VISUAL.value

    def test_seed_makes_generation_deterministic(self):
        v1 = by_abbreviation("V1")
        a = list(per_region_examples(v1, n_per_family=2, rng=random.Random(42), partner_pool=REGIONS))
        b = list(per_region_examples(v1, n_per_family=2, rng=random.Random(42), partner_pool=REGIONS))
        assert [x.user_prompt for x in a] == [x.user_prompt for x in b]


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class TestBackends:
    def test_stub_backend_returns_string(self):
        b = StubBackend()
        out = b.generate("system", "user prompt about Primary Visual Cortex (V1)")
        assert isinstance(out, str) and out
        # Stub answers reference the abbreviation in parens
        assert "V1" in out

    def test_make_backend_dispatch(self):
        assert isinstance(make_backend("stub"), StubBackend)
        assert isinstance(make_backend("ollama"), OllamaBackend)
        assert isinstance(make_backend("ollama:gemma4:e4b"), OllamaBackend)
        assert isinstance(make_backend("anthropic"), AnthropicBackend)
        assert isinstance(make_backend("anthropic:claude-opus-4-7"), AnthropicBackend)

    def test_make_backend_rejects_unknown(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            make_backend("magic")

    def test_ollama_backend_uses_specified_model(self):
        b = OllamaBackend(model="gemma4:e4b")
        assert b.model == "gemma4:e4b"
        assert b.name == "ollama:gemma4:e4b"

    def test_anthropic_backend_default_model(self):
        b = AnthropicBackend()
        assert "claude" in b.model
        assert b.name.startswith("anthropic:")


# ---------------------------------------------------------------------------
# Dataset shape
# ---------------------------------------------------------------------------

class TestDatasetShape:
    def test_build_example_produces_sharegpt_three_turns(self):
        rng = random.Random(0)
        v1 = by_abbreviation("V1")
        instance = next(per_region_examples(v1, n_per_family=1, rng=rng, partner_pool=REGIONS))
        ex = build_example(instance, "answer", family_index=0)

        assert isinstance(ex, DatasetExample)
        assert len(ex.conversations) == 3
        roles = [m["from"] for m in ex.conversations]
        assert roles == ["system", "user", "assistant"]
        assert ex.conversations[0]["value"] == SYSTEM_PROMPT
        assert ex.conversations[2]["value"] == "answer"

    def test_id_is_stable_for_same_inputs(self):
        rng = random.Random(0)
        v1 = by_abbreviation("V1")
        instance = next(per_region_examples(v1, n_per_family=1, rng=rng, partner_pool=REGIONS))
        a = build_example(instance, "x", family_index=7)
        b = build_example(instance, "x", family_index=7)
        assert a.id == b.id
        assert "0007" in a.id

    def test_jsonl_roundtrip(self):
        rng = random.Random(0)
        v1 = by_abbreviation("V1")
        instance = next(per_region_examples(v1, n_per_family=1, rng=rng, partner_pool=REGIONS))
        ex = build_example(instance, "round-trip", family_index=0)
        line = ex.to_jsonl()
        loaded = json.loads(line)
        assert loaded["id"] == ex.id
        assert loaded["conversations"][2]["value"] == "round-trip"
        assert loaded["metadata"]["family"]


# ---------------------------------------------------------------------------
# End-to-end generator
# ---------------------------------------------------------------------------

class TestGenerator:
    def test_generates_full_dataset_with_stub_backend(self, tmp_path: Path):
        out = tmp_path / "neuro.jsonl"
        config = GeneratorConfig(
            backend=StubBackend(),
            n_per_family=2,
            seed=42,
            output_path=out,
        )
        written = generate(config)
        # 5 families × 2 per family × N regions = 10 × N
        n_regions = len(REGIONS)
        assert written == 10 * n_regions

        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == written
        # Each line is valid JSON with the required keys
        for line in lines:
            obj = json.loads(line)
            assert {"id", "conversations", "metadata"} <= obj.keys()
            assert len(obj["conversations"]) == 3

    def test_region_filter_restricts_output(self, tmp_path: Path):
        out = tmp_path / "neuro_v1_only.jsonl"
        config = GeneratorConfig(
            backend=StubBackend(),
            n_per_family=1,
            seed=42,
            output_path=out,
            region_filter=("V1",),
        )
        written = generate(config)
        assert written == 5  # 5 families × 1 each
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
            obj = json.loads(line)
            assert obj["metadata"]["region"].startswith("Primary Visual")

    def test_unknown_region_filter_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="No regions matched"):
            generate(
                GeneratorConfig(
                    backend=StubBackend(),
                    n_per_family=1,
                    seed=42,
                    output_path=tmp_path / "x.jsonl",
                    region_filter=("DOES_NOT_EXIST",),
                )
            )

    def test_progress_callback_fires(self, tmp_path: Path):
        seen: list[tuple[int, int, str]] = []
        config = GeneratorConfig(
            backend=StubBackend(),
            n_per_family=1,
            seed=42,
            output_path=tmp_path / "p.jsonl",
            region_filter=("V1",),
            on_progress=lambda i, t, n: seen.append((i, t, n)),
        )
        generate(config)
        assert len(seen) == 5
        # Should hit (1,5,_), (2,5,_), ..., (5,5,_)
        assert seen[0][0] == 1
        assert seen[-1] == (5, 5, seen[-1][2])

    def test_seed_makes_dataset_reproducible(self, tmp_path: Path):
        out_a = tmp_path / "a.jsonl"
        out_b = tmp_path / "b.jsonl"
        for path in (out_a, out_b):
            generate(
                GeneratorConfig(
                    backend=StubBackend(),
                    n_per_family=1,
                    seed=123,
                    output_path=path,
                    region_filter=("V1",),
                )
            )
        assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")
