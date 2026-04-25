# TurboQuant — research report and Cortex viability assessment

**Date:** 2026-04-25  
**Status:** Spike candidate — half-day evaluation recommended before committing.

## 1. What TurboQuant is

TurboQuant is a training-free, **calibration-free** online vector quantization
algorithm for the **KV cache** (not the model weights). It rotates each
high-dimensional KV vector with a Fast Walsh-Hadamard Transform so coordinates
become near-Gaussian, then applies optimal scalar quantizers per coordinate. A
second 1-bit Quantized JL pass on the residual gives an unbiased inner-product
estimate. Net result: KV cache compressed to **~3 bits per channel** with no
measured perplexity loss and roughly **6-8× memory reduction** over FP32.
([arXiv:2504.19874](https://arxiv.org/abs/2504.19874),
[Google Research blog](https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression/))

- **Paper:** Zandieh, Daliri, Hadian, Mirrokni — *TurboQuant: Online Vector
  Quantization with Near-optimal Distortion Rate* (arXiv:2504.19874, Apr 2025;
  accepted as poster at ICLR 2026).
- **Authors:** Google Research (Mirrokni's group + collaborators).
- **License:** Apache 2.0 (Google paper); community ports vary —
  [`0xSero/turboquant`](https://github.com/0xSero/turboquant) is GPL-3.0.
- **Upstream landings:**
  - vLLM: **merged** ([PR #38479](https://github.com/vllm-project/vllm/issues/38171), 2026-04-15) → flag `--kv-cache-dtype turboquant_3bit_nc`.
  - llama.cpp: PR open, **not merged** ([#20977](https://github.com/ggml-org/llama.cpp/issues/20977) / [#21089](https://github.com/ggml-org/llama.cpp/issues/20977)).
  - Ollama: **not supported** (waits on llama.cpp).
  - Hugging Face transformers: usable via `TurboQuantCache(bits=4)` as the
    `past_key_values` implementation.
  - MLX (Apple): community ports exist.

## 2. Architecture & technique

**Crucial framing:** GPTQ / AWQ / bnb-4bit / GGUF Q4_K_M all quantize **weights**
with calibration. TurboQuant quantizes the **KV cache only**, online, with no
calibration and no fine-tuning. The two are **complementary, not competing**.

| Property                | Value                                                                  |
| ----------------------- | ---------------------------------------------------------------------- |
| What it quantizes       | KV cache (keys + values)                                                |
| Bit-widths              | 2.5 / 3.0 / 3.5 / 4.0 bits per channel                                  |
| Quality-neutral point   | 3.5 bits                                                                |
| Calibration             | none                                                                    |
| Time-to-quantize        | zero — runs online during inference                                     |
| Weights left at         | BF16 (or whatever weight quantizer you stack with — GGUF/AWQ/MLX)       |
| Reference hardware      | H100 (Google's benchmarks)                                              |
| Blackwell sm_120 kernel | **none from Google** — Triton fallback only (community 0xSero port)     |
| Multimodal              | preserved — vision/audio encoders untouched                             |
| Memory delta            | ~6× KV-cache shrink vs FP16 (bigger vs FP32, but FP32 is straw-man)     |
| Speed delta             | up to 8× attention-logit compute vs FP32 on H100 — much smaller end-to-end on consumer GPUs |

The follow-up paper **ITQ3_S** (Yoon, Mar 2026) explicitly notes "TurboQuant
lacks a native CUDA kernel, precluding direct deployment" — and that's the
paper with sm_100 Blackwell-tuned 3-bit *weight* kernels. The original
TurboQuant paper relies on Triton + PyTorch.

## 3. Ecosystem fit

| Stack                | TurboQuant support                                                  |
| -------------------- | -------------------------------------------------------------------- |
| HF `transformers`    | ✅ via `TurboQuantCache(bits=4)` past_key_values                     |
| **vLLM**             | ✅ **upstream merged** (2026-04-15)                                  |
| llama.cpp            | ⚠️ PR open, not merged                                               |
| **Ollama**           | ❌ not supported (inherits from llama.cpp)                           |
| MLX (Apple)          | ✅ community ports (alexcovo, majentik)                              |
| TGI / ExLlamaV2 / MLC-LLM | ❌ none found                                                  |

### Pre-built Gemma 4 + TurboQuant on Hugging Face

- [`majentik/gemma-4-E4B-turboquant`](https://huggingface.co/majentik/gemma-4-E4B-turboquant) — Apache 2.0, 555 downloads, 7 likes.
- [`Jonatan-1987-xtv/gemma-4-e4b-turboquant-standard-int4`](https://huggingface.co/Jonatan-1987-xtv/gemma-4-e4b-turboquant-standard-int4) — ONNX, "blackwell-optimized" tag, Gemma license.
- E2B / 31B MLX variants by `majentik` and `alexcovo`.

**Public benchmarks for Gemma under TurboQuant: none.** Both the Google blog
and the [Kaitchup analysis](https://kaitchup.substack.com/p/turboquant-finally-fast-and-widely)
limit evaluation to Llama 3.1 8B and Ministral.

## 4. Cortex viability — concrete answers

> Cortex constraints: Gemma 4 multimodal preserved end-to-end, Ollama-compatible
> or runnable as a sidecar `webapp/server.py` can call, ideally lets us cram a
> smaller-quant Gemma alongside TRIBE in 32 GB to avoid the swap latency.

### Is it doable in 23 days?

**Yes**, *as KV-cache compression on top of weights you've already quantized*.
Path of least resistance:

1. Pull [`majentik/gemma-4-E4B-turboquant`](https://huggingface.co/majentik/gemma-4-E4B-turboquant) from the Hub.
2. Load via HF `transformers` with `TurboQuantCache(bits=4)` as the past-KV.
3. Run as a **FastAPI sidecar** that `webapp/server.py` calls over HTTP — same
   pattern as our Anthropic backend in `scripts/backends.py`.

No new inference server, no Ollama hacking, no kernel work.

### Apply to fine-tuned model or skip Unsloth?

**Apply on top of the fine-tuned model.** TurboQuant is orthogonal to training:
it doesn't touch weights. Workflow stays:

```
  Unsloth fine-tune → cortex-gemma-4-e4b (LoRA + merged BF16)
                              ↓
                  TurboQuantCache(bits=4) at serve time
```

Skipping Unsloth gives up the neuroscience specialization; that's a much bigger
loss than the inference cost is a win.

### Realistic tok/s gain on the 5090 (base ~196 tok/s BF16 E4B)?

**Unsubstantiated.** Google's 8× figure is **attention-logit compute on H100 vs FP32**,
not end-to-end decode throughput. The HF model card explicitly caveats
"performance on gemma-4-E4B will differ" and only quotes 1.4-1.7× on Apple
Silicon.

Honest expectation:

- **Short context (≤4k):** ~1.1-1.4× decode speedup. Negligible.
- **Long context (≥16k):** larger gains because KV-cache memory bandwidth
  starts to dominate. We don't currently use long context in Cortex (TRIBE max
  ≈ 50 s × 2 Hz = 100 TRs of conditioning), so this is mostly upside for the
  **research-tier narration** (tier 6) when we feed full BrainAnalysis
  context into Gemma 4 31B at 32k ctx.

### Biggest single risk

**No Ollama support.** `webapp/server.py` and `cortex.gpu_scheduler` currently
talk to Ollama exclusively. To use TurboQuant we'd run a separate vLLM sidecar
on `localhost:8000` and add an `OllamaCompatibleHTTPBackend` wrapper. That's
extra moving parts in the demo, and vLLM doesn't do model swapping the way
Ollama does — it pins a model in VRAM.

Second risk: **no native Blackwell sm_120 kernel** from Google. We'd hit the
Triton fallback on the 5090, which the headline H100 numbers don't transfer to.

### Cortex VRAM math

TurboQuant **does not shrink the weights**. Gemma 4 E4B is still ~10 GB. The
TRIBE-22 GB + Gemma-10 GB = 32 GB co-residence problem **is not solved by
TurboQuant alone**. To cram both in 32 GB you'd need a **weight** quantizer:

- GGUF Q4_K_M (Ollama-friendly, our current path)
- AWQ-W4A16 (vLLM-native)

TurboQuant is the cherry, not the cake.

## 5. Bottom line

- ⚠️ **Go IF** paired with a weight quantizer (AWQ-4bit or GGUF Q4_K_M) —
  alone it won't free enough VRAM for TRIBE + Gemma co-residence.
- ✅ Multimodal Gemma 4 (vision + audio + text) is **preserved** — KV-only.
- ⚠️ Ollama path is a non-starter today; the realistic Cortex sidecar is
  **vLLM** with the upstream `--kv-cache-dtype turboquant_3bit_nc` flag.
- ❌ Don't expect the headline 6-8× speedup; H100/FP32 baselines don't transfer
  to 5090/BF16. Plan for **~1.1-1.4× decode + meaningful long-context relief**.
- ✅ Apply on top of the Unsloth-fine-tuned `cortex-gemma-4-e4b` — TurboQuant
  is orthogonal to training.

## 6. The "simpler, less complex" use you asked about

You said: *"We can use it for a simpler less complex purpose, a more basic one."*

The cleanest fit is **the long-context narration tier (researcher / tier 6)**,
*not* the hot path:

- **Tier 0–4** (toddler → college, the user-facing narrations) → keep on Ollama
  with `gemma4:e4b` BF16. Latency wins for chat-like UX.
- **Tier 5–6** (clinician / researcher, with full 32k-ctx BrainAnalysis input)
  → **TurboQuant sidecar with `gemma4:31b` Q4_K_M weights + 3.5-bit KV**.
  This is where long-context KV-cache shrinkage actually pays off, and where
  the user is willing to wait an extra second for a higher-quality answer.

Implementation effort, ranked smallest to largest:

1. **Smallest** (1-2 hours): use `majentik/gemma-4-E4B-turboquant` directly via
   `transformers`, expose as a `TurboQuantBackend` in `scripts/backends.py`
   (mirrors the existing `AnthropicBackend` / `OllamaBackend` pattern).
   Used **only** by the dataset-generation script for now — proves the
   integration works without touching the request queue.
2. **Medium** (4-6 hours): add a `cortex.turboquant_sidecar` module that runs
   vLLM with `--kv-cache-dtype turboquant_3bit_nc` and `gemma4:31b-awq`. Wire
   it into `cortex.request_queue.RequestQueue` as a new `RequestType.NARRATE_LONG`.
3. **Largest** (1-2 days): kernel work to add Blackwell sm_120 path. Skip for
   the hackathon; revisit post-deadline.

**Recommendation:** do (1) first as a half-day spike. If the dataset
generation latency drops or token throughput materially improves on 5090,
extend to (2). If not, skip and stick with AWQ-4bit weights only.

## 7. References

- Paper — <https://arxiv.org/abs/2504.19874>
- Google Research blog — <https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression/>
- ICLR 2026 OpenReview — <https://openreview.net/forum?id=tO3ASKZlok>
- Community port (Triton kernels) — <https://github.com/0xSero/turboquant>
- vLLM PR #38171 (upstream tracking) — <https://github.com/vllm-project/vllm/issues/38171>
- llama.cpp PR #20977 — <https://github.com/ggml-org/llama.cpp/issues/20977>
- HF model `majentik/gemma-4-E4B-turboquant` — <https://huggingface.co/majentik/gemma-4-E4B-turboquant>
- HF model `Jonatan-1987-xtv/...standard-int4` (Blackwell-tagged) — <https://huggingface.co/Jonatan-1987-xtv/gemma-4-e4b-turboquant-standard-int4>
- Hackaday writeup (skeptical of FP32 baseline) — <https://hackaday.com/2026/04/09/turboquant-reducing-llm-memory-usage-with-vector-quantization/>
- Kaitchup analysis — <https://kaitchup.substack.com/p/turboquant-finally-fast-and-widely>
- Follow-up ITQ3_S (Blackwell weight kernels) — <https://arxiv.org/html/2603.27914>
