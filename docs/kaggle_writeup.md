# Cortex — Kaggle Submission Write-Up

> **Status:** Draft 1, ~1,200 words. Revise before final submission.
> Word count is checked at the end. Edit freely; the only fields that *cannot*
> change without breaking SPEC compliance are the trademark line and the
> Apache 2.0 attestation.

---

## Cortex

**Watch a video. See your cortex light up. Hear it explained in plain English.**

Cortex is a multimodal brain-response analysis system that combines **Google's Gemma 4** with Meta's **TRIBE v2** brain foundation model, running locally on a single RTX 5090 via [Ollama](https://ollama.com). It takes a short clip — video, audio, or image — and returns three things at once: a Gemma-narrated description of the content, a TRIBE v2 prediction of the cortical BOLD response, and an interactive 3D visualization of the brain regions that activated.

Built for the **Gemma 4 Good Hackathon**. Targets **Health & Sciences Impact** and the **Ollama track**, with an optional **Unsloth track** entry via `RedTeamKitchen/cortex-gemma-4-e4b` (a fine-tune specialized for neuroscience interpretation).

---

## The problem

TRIBE v2 is a remarkable scientific instrument: feed it a stimulus, and it predicts a 20,484-vertex cortical BOLD response. It collapses several million dollars of fMRI lab time into a 70-second forward pass. But its output is **20,484 floating-point numbers per timepoint**. To a neuroscientist that's tractable. To a teacher, a patient, or a content creator, it's noise.

The same gap exists everywhere brain science meets the public. fMRI imaging is locked behind expensive lab equipment and impenetrable jargon. There is no path from "I made this video" to "here's how a typical viewer's brain processes it" — unless you have a research collaboration and three months.

**Cortex closes that gap with Gemma 4 as the translator.** Gemma 4's multimodal capabilities mean the same model that watches the input video can read the BOLD output and explain it back at any expertise level — toddler to clinician. Native function calling means it can drive the visualization: highlight a network, scrub the timeline, surface a specific region's role.

---

## What Cortex does, end-to-end

A user uploads a 30-second clip — say, a Google I/O keynote opening or a [Year in Search](https://about.google/intl/en-us/stories/year-in-search/) montage. Cortex:

1. **Validates and preprocesses.** FFmpeg trims to 50 s (TRIBE's hard cap), scales to 224×224 for V-JEPA 2 (TRIBE's video encoder), extracts a 16 kHz mono audio track for wav2vec-BERT, and pulls 1 fps keyframes for Gemma's vision gate.
2. **Vision gate (Gemma 4 E4B, multimodal).** Gemma classifies each keyframe — scene type, subject, mood, dominant modality — and produces a content description. This is what shows up in the gallery when the source video can't be redistributed.
3. **Brain scan (TRIBE v2 on the 5090).** The GPU scheduler swaps Gemma out of VRAM, loads TRIBE v2 in (~22 GB), runs inference (~70 s for a 50-s clip), then swaps Gemma back. Sequential by necessity — both models can't coexist in 32 GB.
4. **Analysis (CPU, Schaefer-400 atlas).** Top regions by activation magnitude, peak frame, network-level summaries (Yeo 7-network parcellation: visual, auditory, default-mode, frontoparietal, somatomotor, dorsal attention, ventral attention, limbic).
5. **Narration (Gemma 4, three tiers).** Gemma E4B for the layperson tier (197 tok/s — snappy), 26B MoE for clinical-level explanation, 31B Dense for the researcher tier with full BrainAnalysis context at 32k. The user picks the tier with a slider; the same brain data, three explanations.
6. **Visualization (Three.js, WebSocket-streamed).** A web viewer renders 50 ROI markers placed on a cortical mesh, colored by Yeo network. Click a region to see its name, Brodmann area, and Gemma's explanation of *why this region activated for this stimulus*. Drag a time slider to scrub through the BOLD timeline. Toggle networks on and off.

A 30-second clip yields ~7 MB of data and a 90-second analysis. Most of that time is the model swap — which is where the architecture pays off.

---

## Architecture (the load-bearing parts)

The 5090 has 32 GB of VRAM. TRIBE v2 needs 22 GB. Gemma E4B needs 10 GB. They cannot coexist. Most projects would solve this with cloud inference; Cortex solves it locally.

**`cortex.gpu_scheduler.GPUScheduler`** is a state machine: `IDLE → GEMMA_ACTIVE → SWAPPING → TRIBE_ACTIVE → SWAPPING → GEMMA_ACTIVE`. Swap latency is ~5 s GEMMA→TRIBE (CUDA graph warmup) and ~3 s back. Every swap is metered into `SwapMetrics` and exposed at `GET /api/health` for live observability. Gemma chat requests during a brain scan queue with priority; the queue depth is in the same health endpoint. This is the file that turns "two models, one card" from a constraint into a feature.

**`cortex.request_queue.RequestQueue`** sits above the scheduler. It accepts brain-scan, narration, and chat requests with explicit priorities, routes chat to an external LLM fallback when the GPU is busy with TRIBE (configurable, off by default — Cortex defaults to local-only), and returns structured `CortexError` responses on failure rather than raw exceptions.

**`cortex.errors.CortexError`** (per SPEC §15) is the single error path. Every public API returns either a result or a `CortexError` with `code`, `error_class`, `message`, `recovery_action`, `retry`, `fallback_used`, `vram_state`, and `timestamp`. The wire format is locked by 26 unit tests. When the demo fails on stage, the user sees "Auto-trimmed to last 50 seconds" instead of a Python traceback.

**`cortex.gcp_inference.GCPInferenceFallback`** is the only redundancy that touches the cloud. On local CUDA OOM, the scheduler hands the job to a GCP A100 worker (deployed via `gcp/Dockerfile.tribe-worker` + Cloud Build), polls until done, and returns a `RemoteInferenceResult` shaped exactly like the local one. The 5090 is the primary; the A100 is insurance. Bearer-token auth, exponential-backoff retries, structured errors at every failure mode.

---

## Edge / constrained-environment story

The hackathon scoring rubric rewards "running in constrained / edge environments." Cortex's primary deployment is a single RTX 5090 desktop in a kitchen in Iowa. No A100 cluster, no cloud GPU pool. The cloud fallback exists — and is fully tested — but the demo runs locally. Median end-to-end latency target: **under 100 seconds for a 30-second clip**, including the GEMMA↔TRIBE swap.

**This is the differentiator.** Most Gemma 4 hackathon submissions will be cloud-hosted. Cortex's Three.js viewer, FastAPI server, request queue, GPU scheduler, and TRIBE inference all run on a single machine the user already owns.

---

## Reproducibility

Everything is in [github.com/AlexiosBluffMara/cortex](https://github.com/AlexiosBluffMara/cortex), Apache 2.0 licensed. The repository ships:

- **235 unit + integration tests** — `pytest tests/ -q` runs in 4 seconds. CI pinned to Python 3.11 + 3.12 (see `.github/workflows/ci.yml`).
- **Pinned dependencies** — `pyproject.toml` locks every transitive of TRIBE v2 + Gemma + the brain-science stack to a known-good version.
- **Verified Unsloth fine-tune config** — `docs/unsloth.md` traces every hyperparameter back to Daniel Han-Chen's open Gemma 4 31B notebook (the Kaggle copy is gated; we read the GitHub mirror line by line).
- **Operator runbook** — `docs/gcp.md` is a step-by-step deployment guide with cost estimates and a troubleshooting matrix. Total hackathon GCP budget: **$10**.
- **Deterministic synthetic dataset** — `scripts/generate_neuro_dataset.py --seed 42` produces byte-identical output across runs. The 5,000-example synthetic neuroscience QA dataset feeds the Unsloth fine-tune.

**Gemma is a trademark of Google LLC.** Cortex is not endorsed by Google. The fine-tuned model is published as `RedTeamKitchen/cortex-gemma-4-e4b` per Google's Gemma naming guidelines: *"Gemma" appears in the path, not the product name.*

---

## Limits and future work

- **TRIBE v2 weights are CC-BY-NC 4.0** (Meta). Cortex's *code* is Apache 2.0; the weights are not redistributable here. Non-commercial only.
- **Stand-in cortical mesh.** The viewer ships with two procedural hemispheres and 50 ROI markers placed in normalized space. Real `brain.glb` (Schaefer-400 cortical surface) drops in by replacing one function (`buildBrainMesh()`); the viewer's interaction is unchanged.
- **No audio fallback model.** Cortex relies on Gemma 4 E4B's multimodal audio path. Pure-audio clips work via TRIBE's wav2vec-BERT encoder regardless.
- **Unsloth fine-tune is staged but not yet trained.** `scripts/train_cortex.py` is fully wired — including the `--dry-run` mode that prints the resolved config without burning GPU time — but the full 6-minute training pass on the 5090 is queued for May 4 (sprint Day 11).

---

## Submission checklist

| Deliverable | Status |
| --- | --- |
| Public GitHub repo (Apache 2.0) | ✅ shipped |
| Live demo at cortex.redteamkitchen.com | ⏳ Cloud Run pending |
| Public HF model `RedTeamKitchen/cortex-gemma-4-e4b` | ⏳ post-training, May 6 |
| 3-minute YouTube demo video | ⏳ docs/video_script.md drafted, recording May 12 |
| Kaggle write-up (this document) | ✅ draft 1 |
| Cover image | ⏳ |

---

*~1,200 words. Hard limit 1,500.*
*Last updated: 2026-04-25.*
