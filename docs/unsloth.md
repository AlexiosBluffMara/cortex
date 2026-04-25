# Unsloth — references and integration notes

This file is the canonical landing for Unsloth-related material in Cortex.
The hackathon offers an Unsloth track (`$10K`), so we plan a fine-tune of
Gemma 4 E4B → `RedTeamKitchen/cortex-gemma-4-e4b` specialized for
neuroscience interpretation, Three.js visualization code, and agentic tool
use (see [SPEC.md §9](../SPEC.md#9-unsloth-fine-tuning)).

## Required reading

### Official Unsloth resources

- **Gemma 4 training guide** — <https://unsloth.ai/docs/models/gemma-4/train>
- **Quickstart** — <https://unsloth.ai/docs/models/gemma-4/train#quickstart>
- **Bug fixes & tips** — <https://unsloth.ai/docs/models/gemma-4/train#bug-fixes--tips>
- **Unsloth GitHub** — <https://github.com/unslothai/unsloth>
- **Unsloth Studio Colab** — <https://colab.research.google.com/github/unslothai/unsloth/blob/main/studio/Unsloth_Studio_Colab.ipynb>

### Reference notebooks (open these in-browser; Kaggle is JS-gated)

| Notebook                       | Link                                                                                                                  | Variant      | Modality                      |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------- | ------------ | ----------------------------- |
| **Gemma 4 E4B Vision**         | <https://colab.research.google.com/github/unslothai/notebooks/blob/main/nb/Gemma4_(E4B)-Vision.ipynb>                 | E4B          | Vision (closest to our path)  |
| **Gemma 4 E4B Audio**          | <https://colab.research.google.com/github/unslothai/notebooks/blob/main/nb/Gemma4_(E4B)-Audio.ipynb>                  | E4B          | Audio                         |
| **Gemma 4 E2B Reinforcement Learning (Sudoku)** | <https://colab.research.google.com/github/unslothai/notebooks/blob/main/nb/Gemma4_(E2B)_Reinforcement_Learning_Sudoku_Game.ipynb> | E2B          | RL (smallest variant)         |
| **Gemma 4 31B (Daniel Han-Chen)** — Kaggle gated copy | <https://www.kaggle.com/code/danielhanchen/gemma4-31b-unsloth>                              | 31B Dense    | Vision                        |
| **Gemma 4 31B (raw GitHub copy)** — verified source for the configs below | <https://raw.githubusercontent.com/unslothai/notebooks/main/nb/Gemma4_(31B)-Vision.ipynb>   | 31B Dense    | Vision                        |
| **Hackathon Unsloth-track discussion** — Kaggle gated | <https://www.kaggle.com/competitions/gemma-4-good-hackathon/discussion/690070> | All          | (Unsloth team guidance)       |

The Kaggle pages render client-side and are gated to scrapers — open them in
the browser while logged in. The Colab + GitHub links are open and viewable
without auth.

## Verified Unsloth Gemma 4 31B config (from the GitHub notebook)

`Gemma4_(31B)-Vision.ipynb` is the same code as the gated Kaggle notebook.
These values are **verified** (not provisional):

| Setting                          | Value                                                            |
| -------------------------------- | ---------------------------------------------------------------- |
| Base model                       | `unsloth/gemma-4-31B-it`                                         |
| Loader class                     | `FastVisionModel` (text-only fine-tunes use `FastModel`/`FastLanguageModel`) |
| Quantization                     | 4-bit base via bitsandbytes (`load_in_4bit=True`)                |
| Gradient checkpointing           | `"unsloth"` — long-context-aware                                 |
| `max_length`                     | 2048                                                             |
| LoRA `r`                         | **32**                                                           |
| LoRA `lora_alpha`                | 32                                                               |
| LoRA `lora_dropout`              | 0                                                                |
| `bias`                           | `"none"`                                                         |
| Target modules                   | `"all-linear"` (vision + language + attention + MLP)             |
| `random_state`                   | 3407                                                             |
| `use_rslora`                     | False                                                            |
| `loftq_config`                   | None                                                             |
| `per_device_train_batch_size`    | 1                                                                |
| `gradient_accumulation_steps`    | 4 (effective batch 4)                                            |
| `max_grad_norm`                  | **0.3** — load-bearing for vision stability                      |
| `weight_decay`                   | **0.001**                                                        |
| `warmup_ratio`                   | 0.03                                                             |
| Learning rate                    | 2e-4                                                             |
| `lr_scheduler_type`              | cosine                                                           |
| Optimizer                        | `adamw_8bit`                                                     |
| `max_steps` (demo)               | 60 — replace with `num_train_epochs=1` for a full run            |
| Inference sampling               | temperature 1.0, top_p 0.95, top_k 64                            |
| Hardware                         | A100-SXM4-40GB (Colab)                                           |
| Peak VRAM during training        | **21.36 GB** (54.1% of A100-40GB)                                |
| Wall-clock for 60 steps          | **6.84 minutes**                                                 |
| Chat template                    | `unsloth.get_chat_template("gemma-4-thinking")`                  |
| Dataset                          | `unsloth/LaTeX_OCR` (68,686 vision rows; demo only)              |

Pinned dependencies in the notebook: `transformers==5.5.0`, `datasets==4.3.0`,
`huggingface_hub>=0.34.0`, `peft trl triton bitsandbytes accelerate xformers timm`.
Tested against Unsloth `2026.4.4`+ (earlier versions had a Gemma-4 31B/26B KV-cache
bug — see *Bug fixes* below).

### Bug fixes & gotchas (from Unsloth docs + Daniel's X posts)

1. **31B / 26B inference crash** — `num_kv_shared_layers=0` collapses
   `layer_types[:-0]` to `[]`. Patched in Unsloth ≥ `2026.4.4`. Always update
   before fine-tuning.
2. **Grad-accumulation loss explosion** (was 300–400, should be ~10–15) —
   patched in the same release.
3. **`use_cache=False`** previously produced gibberish for E2B/E4B — patched.
4. **Audio overflow** issue noted on E4B-Audio — see the audio notebook for
   the fix.
5. **Gemma chat-template gotcha**: assistant role in raw chat templates is
   `"model"`, not `"assistant"`. Use `unsloth.get_chat_template("gemma-4-thinking")`
   rather than hand-rolling roles. Daniel's notebook uses `"assistant"` because
   the helper handles the mapping.
6. **For 26B-A4B (MoE), avoid QLoRA**, use 16-bit LoRA. Does **not** apply to
   31B Dense or E4B.

### Throughput claims (from Unsloth docs)

- "1.5× faster, ~60% less VRAM than FlashAttention 2"
- "8 GB VRAM minimum to train Gemma 4 locally" (E2B/E4B-class, not 31B)
- 31B QLoRA fits in 22 GB total VRAM (matches our measured 21.36 GB peak)

### Export paths in the 31B notebook

- **LoRA adapters**: `model.save_pretrained("gemma_4_lora")` + `processor.save_pretrained(...)` and optional `model.push_to_hub("RedTeamKitchen/cortex-gemma-4-e4b", token=...)`
- **16-bit merged** (vLLM-ready): `model.save_pretrained_merged(...)` / `model.push_to_hub_merged(...)`
- **GGUF**: not in this notebook. Use the doc-page recipe:
  `model.save_pretrained_gguf("dir", tokenizer, quantization_method="q4_k_m")`
  and `model.push_to_hub_gguf(...)`.
- **Ollama Modelfile**: assemble manually after GGUF export — see [SPEC.md §9](../SPEC.md#9-unsloth-fine-tuning).

## Cortex's planned `cortex-gemma-4-e4b` config

Inherited from Daniel's 31B notebook with E4B-appropriate adjustments. To be
**verified against `Gemma4_(E4B)-Vision.ipynb` and `Gemma4_(E4B)-Audio.ipynb`** before the actual training run on
~May 4 (sprint Day 11):

| Setting                          | E4B target                                                       | Why differs from 31B                                       |
| -------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------- |
| Base model                       | `unsloth/gemma-4-E4B-it` (or `google/gemma-4-e4b`)               | Smaller variant for our 5090 + Ollama path                 |
| Loader class                     | `FastModel` (text + tools, possibly multimodal)                  | We need vision+audio+text; Unsloth E4B-Vision/Audio show how |
| Quantization                     | 4-bit base (QLoRA), keep BF16 LoRA adapters                      | Same as 31B                                                |
| `max_length`                     | 8192                                                             | Longer context for narration tasks                         |
| LoRA `r` / `lora_alpha`          | **32 / 32** — match Daniel's 31B                                 | Daniel's "all-linear" + r=32 wins over our prior r=16      |
| LoRA `lora_dropout`              | 0                                                                | Daniel uses 0; we previously planned 0.05                  |
| Target modules                   | `"all-linear"`                                                   | Cleaner than the explicit q/k/v/o/gate/up/down list        |
| `per_device_train_batch_size`    | 4 (E4B is small enough)                                          | 31B used 1                                                 |
| `gradient_accumulation_steps`    | 4 (effective batch 16)                                           | More throughput on a 5090 vs A100                          |
| `max_grad_norm`                  | 0.3                                                              | Same                                                       |
| `weight_decay`                   | 0.001                                                            | Same                                                       |
| `warmup_ratio`                   | 0.03                                                             | Same                                                       |
| Learning rate                    | 2e-4                                                             | Same                                                       |
| Optimizer                        | `adamw_8bit`                                                     | Same                                                       |
| Precision                        | BF16 (Blackwell sm_120 native)                                   | Same                                                       |
| `random_state`                   | 3407                                                             | Match Unsloth signature                                    |
| Chat template                    | `get_chat_template("gemma-4-thinking")`                          | Same                                                       |
| Export                           | LoRA → merged BF16 → GGUF Q4_K_M → Ollama Modelfile              | Drop merged step if disk-constrained                       |
| Target VRAM during training      | ≤ 16 GB on a 32 GB RTX 5090                                      | 31B used 21 GB on A100-40GB                                |

Hard invariants (do not violate):

- **Gemma 4 only** — no Gemma 3 fallback anywhere in the training loop or
  inference path (see `D:\cortex\CLAUDE.md`).
- **Naming convention**: `RedTeamKitchen/cortex-gemma-4-e4b`. Per Google's
  Gemma naming guidelines, "Gemma" appears only in the *path*, not in the
  product name. The HF model card must include "Gemma is a trademark of
  Google LLC."
- TRIBE v2 weights remain CC-BY-NC 4.0; the fine-tune itself is on Gemma
  (Apache 2.0), so the training artifact and adapters can ship under
  Apache 2.0 without inheriting TRIBE's non-commercial restriction.
- Always pin `unsloth>=2026.4.4` to avoid the 31B/26B KV-cache crash and the
  grad-accumulation loss explosion.

## Dataset mix (planned, ~40-50K examples)

Per SPEC §9. The synthetic neuroscience portion (5K) is generated by
`scripts/generate_neuro_dataset.py` (this commit). The remaining sets are
public HuggingFace datasets:

| Dataset                          | Domain                              | Examples | License     |
| -------------------------------- | ----------------------------------- | -------- | ----------- |
| Synthetic neuro QA               | Brain region interpretation         | 5,000    | Self-owned  |
| NVIDIA Nemotron Tool-Use         | Agentic / function calling          | 15,000   | CC-BY 4.0   |
| TokenBender Code Instructions    | Python / JS / Three.js coding       | 15,000   | Apache 2.0  |
| OpenHermes 2.5 (sampled)         | General instruction following       | 10,000   | MIT         |
| Synthetic Three.js               | 3D visualization code               | 2,000    | Self-owned  |

## When this file should be updated

- After verifying `Gemma4_(E4B)-Vision.ipynb` and `Gemma4_(E4B)-Audio.ipynb`
  in-browser — replace any "verify against the E4B notebook" caveats with the
  actual values they use.
- After the Kaggle Unsloth-track discussion thread reveals an
  Unsloth-track-specific scoring rubric, dataset format requirement, or
  eligibility fine print.
- After the actual training run on ~May 4 — replace planned numbers with
  measured (VRAM peak, training time, eval scores).

## Sources of every claim above

- `Gemma4_(31B)-Vision.ipynb` (raw GitHub) — every code value in the
  "verified" config table.
- <https://unsloth.ai/docs/models/gemma-4/train> — VRAM and throughput claims.
- <https://x.com/danielhanchen/status/2041516671119327590> — bug-fix notes
  (KV-cache crash, grad-accum loss, `use_cache`).
- <https://huggingface.co/unsloth/gemma-4-31B> — model architecture (60 layers,
  256K ctx, BF16 base).
- <https://huggingface.co/unsloth/gemma-4-31B-it-GGUF> — full Q-ladder.
- <https://github.com/unslothai/unsloth/releases/tag/v0.1.36-beta> — Gemma 4
  release notes.
