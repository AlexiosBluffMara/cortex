"""Tests for scripts/train_cortex.py — Unsloth fine-tune pipeline.

We don't actually run Unsloth here (it requires GPU + heavy imports). What we
*do* test:
  - The TrainConfig defaults match the verified hyperparameters from
    `docs/unsloth.md`. If someone silently changes `lora_r=32` → `16`, this
    test fails.
  - Hard invariants reject Gemma 3 base models and alpha < r.
  - ShareGPT-JSONL loading enforces the schema and surfaces malformed lines.
  - The Gemma chat-format mapper handles the assistant↔model role swap.
  - The Ollama Modelfile renderer includes the trademark line and our
    sampling defaults.
  - --dry-run runs the pipeline without ever importing torch/unsloth.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train_cortex import (
    CORTEX_SYSTEM_PROMPT,
    DEFAULT_BASE_MODEL,
    DEFAULT_FINETUNED_REPO,
    TrainConfig,
    load_sharegpt_jsonl,
    main,
    render_modelfile,
    run,
    to_gemma_chat_format,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Config defaults are pinned to docs/unsloth.md
# ---------------------------------------------------------------------------

class TestVerifiedDefaults:
    """If these fail, docs/unsloth.md is out of sync with the code."""

    def test_lora_matches_daniel_31b_notebook(self):
        c = TrainConfig()
        assert c.lora_r == 32
        assert c.lora_alpha == 32
        assert c.lora_dropout == 0.0
        # Hard pin: must be the explicit module-name list, NOT "all-linear".
        # In QLoRA mode bnb.Linear4bit shadows nn.Linear so peft's
        # "all-linear" sentinel finds zero modules → 0 trainable params →
        # silent no-op training. Production attempt #2 burned ~4 minutes
        # on this exact bug; this test exists to keep us out of that ditch.
        assert tuple(c.target_modules) == (
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        )
        assert c.bias == "none"
        assert c.random_state == 3407  # Unsloth signature seed

    def test_training_matches_daniel_31b_notebook(self):
        c = TrainConfig()
        assert c.max_grad_norm == 0.3
        assert c.weight_decay == 0.001
        assert c.warmup_ratio == 0.03
        assert c.optim == "adamw_8bit"
        assert c.lr_scheduler_type == "cosine"
        # Pinned to True (vanilla pytorch GC), NOT "unsloth" (smart offload).
        # The smart offload mode uses global state in unsloth_zoo that
        # silently zeroes LoRA gradients on first SFTTrainer step. Production
        # attempt #4 burned ~3 min on this exact bug; we pay ~2-3 GB extra
        # VRAM to keep training reliable.
        assert c.use_gradient_checkpointing is True

    def test_e4b_5090_safe_overrides(self):
        # Updated after measuring real VRAM peaks on the 5090 in smoke
        # test 11 (commit + log linked in docs/unsloth.md):
        #   batch=2 + max_seq=2048 → peak 31.7 / 31.8 GB (nearly OOM)
        # batch=1 + max_seq=2048 leaves ~10 GB of headroom for safety.
        c = TrainConfig()
        assert c.per_device_train_batch_size == 1
        assert c.max_seq_length == 2048
        # Effective batch held constant at 4 via gradient accumulation
        assert c.gradient_accumulation_steps == 4
        assert c.load_in_4bit is True  # QLoRA

    def test_chat_template(self):
        assert TrainConfig().chat_template == "gemma-4-thinking"

    def test_finetuned_repo_follows_naming_guideline(self):
        # "Gemma" appears in the path, not in the product name.
        assert DEFAULT_FINETUNED_REPO == "RedTeamKitchen/cortex-gemma-4-e4b"
        # Cortex is the product, not "Cortex Gemma" or "Gemma Cortex"
        assert "cortex" in DEFAULT_FINETUNED_REPO.lower()

    def test_base_model_is_gemma_4(self):
        # Hard invariant: never Gemma 3
        assert "gemma-4" in DEFAULT_BASE_MODEL.lower()
        assert "gemma-3" not in DEFAULT_BASE_MODEL.lower()


# ---------------------------------------------------------------------------
# Hard invariants
# ---------------------------------------------------------------------------

class TestHardInvariants:
    def test_rejects_gemma_3(self):
        with pytest.raises(ValueError, match="Gemma 4"):
            TrainConfig(base_model="google/gemma-3-7b")

    def test_accepts_underscore_variant(self):
        # Unsloth sometimes ships with underscores
        TrainConfig(base_model="unsloth/gemma_4_E4B")  # must not raise

    def test_rejects_alpha_lower_than_r(self):
        with pytest.raises(ValueError, match="lora_alpha"):
            TrainConfig(lora_r=32, lora_alpha=16)

    def test_accepts_alpha_equal_to_r(self):
        # Daniel's signature setting
        TrainConfig(lora_r=32, lora_alpha=32)


# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


class TestLoadShareGPT:
    def test_loads_valid_jsonl(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_jsonl(path, [
            {"id": "x", "conversations": [
                {"from": "system", "value": "s"},
                {"from": "user", "value": "u"},
                {"from": "assistant", "value": "a"},
            ]},
        ])
        out = load_sharegpt_jsonl(path)
        assert len(out) == 1
        assert out[0]["conversations"][2]["value"] == "a"

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_sharegpt_jsonl(tmp_path / "missing.jsonl")

    def test_empty_file_raises(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="no examples"):
            load_sharegpt_jsonl(path)

    def test_malformed_json_raises_with_line_number(self, tmp_path: Path):
        path = tmp_path / "broken.jsonl"
        path.write_text('{"id": "ok", "conversations": [{"from":"u","value":"v"}]}\n{not json\n')
        with pytest.raises(ValueError, match=":2 malformed JSON"):
            load_sharegpt_jsonl(path)

    def test_missing_conversations_key_raises(self, tmp_path: Path):
        path = tmp_path / "bad.jsonl"
        _write_jsonl(path, [{"id": "x"}])
        with pytest.raises(ValueError, match="missing 'conversations'"):
            load_sharegpt_jsonl(path)

    def test_blank_lines_skipped(self, tmp_path: Path):
        path = tmp_path / "with_blanks.jsonl"
        path.write_text(
            '\n'
            '{"id":"x","conversations":[{"from":"user","value":"u"},{"from":"assistant","value":"a"}]}\n'
            '\n',
            encoding="utf-8",
        )
        out = load_sharegpt_jsonl(path)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Chat format mapping
# ---------------------------------------------------------------------------

class TestChatFormat:
    def test_passes_through_standard_roles(self):
        ex = {"conversations": [
            {"from": "system", "value": "s"},
            {"from": "user", "value": "u"},
            {"from": "assistant", "value": "a"},
        ]}
        out = to_gemma_chat_format(ex)
        assert [m["role"] for m in out] == ["system", "user", "assistant"]
        assert [m["content"] for m in out] == ["s", "u", "a"]

    def test_maps_model_to_assistant(self):
        # Some upstream Gemma datasets use "model" instead of "assistant"
        ex = {"conversations": [
            {"from": "user", "value": "u"},
            {"from": "model", "value": "answer"},
        ]}
        out = to_gemma_chat_format(ex)
        assert out[1]["role"] == "assistant"
        assert out[1]["content"] == "answer"


# ---------------------------------------------------------------------------
# Modelfile rendering
# ---------------------------------------------------------------------------

class TestModelfile:
    def test_includes_gguf_filename(self):
        m = render_modelfile(gguf_filename="cortex-gemma-4-e4b.Q4_K_M.gguf")
        assert "FROM ./cortex-gemma-4-e4b.Q4_K_M.gguf" in m

    def test_includes_sampling_parameters(self):
        m = render_modelfile(gguf_filename="x.gguf")
        assert "PARAMETER temperature 0.4" in m
        assert "PARAMETER num_ctx 8192" in m
        assert "PARAMETER top_p 0.9" in m
        assert "PARAMETER num_predict 512" in m

    def test_includes_cortex_system_prompt(self):
        m = render_modelfile(gguf_filename="x.gguf")
        # System block uses triple-quoted Modelfile syntax
        assert 'SYSTEM """' in m
        assert "Cortex" in m
        assert "TRIBE v2" in m

    def test_includes_gemma_trademark_line(self):
        # Hackathon rule + Google's Gemma naming guidelines.
        m = render_modelfile(gguf_filename="x.gguf")
        assert "Gemma is a trademark of Google LLC." in m

    def test_default_system_prompt_used(self):
        m = render_modelfile(gguf_filename="x.gguf")
        # Default constant is embedded
        assert CORTEX_SYSTEM_PROMPT in m

    def test_custom_system_prompt_respected(self):
        m = render_modelfile(gguf_filename="x.gguf", system_prompt="custom")
        assert 'SYSTEM """custom"""' in m


# ---------------------------------------------------------------------------
# Dry-run pipeline
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_import_torch_or_unsloth(self, tmp_path: Path, monkeypatch):
        # If --dry-run accidentally pulls in torch, this test catches it.
        # We block imports of those modules at the sys.modules level.
        import sys

        blocked = {"torch", "unsloth", "trl", "datasets", "peft"}

        def _meta_finder(name, *args, **kwargs):
            if name.split(".")[0] in blocked:
                raise ImportError(f"Blocked import in --dry-run: {name}")
            return None

        # Insert a meta_path finder that raises on blocked names
        class _Blocker:
            def find_module(self, name, path=None):
                if name.split(".")[0] in blocked:
                    raise ImportError(f"Blocked import in --dry-run: {name}")
                return None

            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in blocked:
                    raise ImportError(f"Blocked import in --dry-run: {name}")
                return None

        # Stage the dataset
        dataset = tmp_path / "ds.jsonl"
        _write_jsonl(dataset, [
            {"id": "x", "conversations": [
                {"from": "user", "value": "u"},
                {"from": "assistant", "value": "a"},
            ]},
        ])

        config = TrainConfig(
            dataset_path=dataset,
            output_dir=tmp_path / "out",
            dry_run=True,
        )

        sys.meta_path.insert(0, _Blocker())
        try:
            result = run(config)
        finally:
            sys.meta_path.pop(0)

        assert result["dry_run"] is True
        assert result["n_examples"] == 1
        assert "config" in result

    def test_dry_run_creates_output_dir(self, tmp_path: Path):
        dataset = tmp_path / "ds.jsonl"
        _write_jsonl(dataset, [
            {"id": "x", "conversations": [{"from": "user", "value": "u"}]},
        ])
        out = tmp_path / "deeply" / "nested" / "out"
        config = TrainConfig(dataset_path=dataset, output_dir=out, dry_run=True)
        run(config)
        assert out.exists()


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

class TestCLI:
    def test_dry_run_flag(self, tmp_path: Path):
        ds = tmp_path / "ds.jsonl"
        _write_jsonl(ds, [{"id": "x", "conversations": [{"from": "user", "value": "u"}]}])
        out = tmp_path / "out"
        rc = main([
            "--dataset", str(ds),
            "--output-dir", str(out),
            "--dry-run",
        ])
        assert rc == 0

    def test_unknown_flag_errors(self):
        with pytest.raises(SystemExit):
            main(["--this-does-not-exist"])

    def test_invalid_gguf_choice_errors(self):
        with pytest.raises(SystemExit):
            main(["--gguf", "q12_silly", "--dry-run"])
