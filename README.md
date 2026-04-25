# Cortex

> Watch a video. See your brain light up. Hear it explained in plain English.

**Cortex** is a multimodal brain-response analysis system that combines [Gemma 4](https://ai.google.dev/gemma) with Meta's TRIBE v2 brain foundation model to predict and explain cortical activation in response to video, audio, or text stimuli — running locally on a single RTX 5090 via [Ollama](https://ollama.com), with cloud failover to GCP.

Built for the [Gemma 4 Good Hackathon](https://www.kaggle.com/competitions/gemma-4-good-hackathon) (Health & Sciences track).

---

## What Cortex does

1. You upload a short video, audio clip, or piece of text (≤50 seconds).
2. **Gemma 4 E4B** (multimodal) describes the content and gates copyright/safety.
3. **TRIBE v2** predicts your cortex's BOLD response — 20,484 vertices over time.
4. **Gemma 4** translates the raw activation into plain-English narration at the requested expertise tier (toddler → researcher).
5. An interactive **Three.js cortex viewer** shows the activation in 3D, with click-to-inspect region details.

The two models cannot coexist on 32 GB of VRAM, so Cortex ships a [GPU priority scheduler](cortex/gpu_scheduler.py) that swaps them deterministically with OOM recovery, swap metrics, and fallback routing.

---

## Architecture

```
                 ┌────────────────────────────────────────────────────────┐
                 │  Hermes Agent (orchestrator)                           │
                 │  brain_scan │ narrate │ visualize │ describe_input     │
                 └─────────┬──────────────────────────────────────────────┘
                           │
                  ┌────────▼─────────┐
                  │  RequestQueue    │  priority queue, GPU-aware routing
                  │  (cortex/        │  fallback to external LLM if TRIBE
                  │   request_queue) │  is hogging the GPU
                  └────────┬─────────┘
                           │
                  ┌────────▼──────────┐
                  │  GPUScheduler     │  state machine: IDLE / GEMMA_ACTIVE /
                  │  (cortex/         │  TRIBE_ACTIVE / SWAPPING
                  │   gpu_scheduler)  │  with metrics + state listeners
                  └─┬───────────────┬─┘
                    │               │
        ┌───────────▼──┐    ┌───────▼─────────┐
        │ Ollama       │    │ TRIBE v2        │
        │ Gemma 4 E4B/ │    │ V-JEPA 2 +      │
        │ 26B/31B      │    │ wav2vec-BERT +  │
        │ (multimodal) │    │ Llama-3.2-3B    │
        └──────────────┘    └─────────────────┘

         ┌──────────────────────────────────────────┐
         │  Web UI (FastAPI + Vite + Three.js)      │
         │  Cloudflare Tunnel → 5090 desktop         │
         │  GCP Cloud Run for static + cold start   │
         └──────────────────────────────────────────┘
```

See [SPEC.md](SPEC.md) for the complete technical specification and [SPRINT_PLAN.md](SPRINT_PLAN.md) for the day-by-day plan to the May 18, 2026 hackathon deadline.

---

## Quickstart

### Prerequisites

- **Hardware**: RTX 5090 (32 GB) or any CUDA GPU ≥ 24 GB; 64 GB RAM recommended.
- **Software**: Python 3.11+, [Ollama](https://ollama.com/download), FFmpeg, git, Node.js 20+ (for the webapp).
- **Models**: Pull Gemma 4 from Ollama (see below). TRIBE v2 weights are downloaded separately.

### Install

```bash
# 1. Clone
git clone https://github.com/AlexiosBluffMara/cortex.git
cd cortex

# 2. Python env (kept on C: for speed on Windows)
python -m venv .venv
source .venv/Scripts/activate   # or `.venv\Scripts\activate` on PowerShell

# 3. Blackwell-compatible PyTorch (cu128)
pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision

# 4. Cortex itself
pip install -e ".[dev]"

# 5. TRIBE v2 source + weights (separate, CC-BY-NC 4.0 — non-commercial only)
git clone https://github.com/facebookresearch/tribev2.git tribev2_src
pip install --no-deps -e tribev2_src
# Weights: see tribev2_src/README for the HuggingFace download

# 6. Pull Gemma 4 models (NO Gemma 3 fallback)
ollama pull gemma4:e4b
ollama pull gemma4:26b
ollama pull gemma4:31b

# 7. Configure
cp .env.example .env
# edit .env to set your local paths and tokens
```

### Run

```bash
# CLI: analyze a single clip
cortex analyze ./assets/sample.mp4 --tier 2

# Web UI (FastAPI + Three.js viewer)
uvicorn webapp.server:app --host 0.0.0.0 --port 8765

# Hermes Agent (autonomous orchestration)
python -m hermes.agent
```

### Test

```bash
pytest                      # full suite (263 tests, ~7s)
pytest -m unit              # fast unit tests (no GPU/network)
pytest -m "not slow"        # skip slow tests
ruff check .                # lint
mypy cortex hermes cli      # type-check
```

### Generate the synthetic neuroscience training dataset

The fine-tune is trained on a 2,000-example synthetic neuro-QA dataset
covering 20 brain regions across all 8 Yeo networks. The supervisor handles
unattended multi-hour runs — it resumes if killed, retries failed examples,
hits an Ollama health check between regions, and stops at a configurable
deadline.

```bash
# Sensible defaults for an unattended overnight run against gemma4:e4b
python -m scripts.generate_neuro_dataset \
    --backend ollama:gemma4:e4b \
    --n-per-family 20 \
    --supervised \
    --max-runtime-min 180 \
    --output data/cortex_train.jsonl

# Quality-check the result
python -m scripts.validate_dataset --input data/cortex_train.jsonl
# Wakes you up to data/dataset_quality_report.md with per-region distribution,
# answer-length stats, region-mention rate, and a verdict block.
```

### Fine-tune

```bash
# Dry run — print resolved config and exit (no GPU work)
python -m scripts.train_cortex --dry-run

# Real run on the 5090 (after dataset is generated)
python -m scripts.train_cortex \
    --dataset data/cortex_train.jsonl \
    --epochs 3 --merge --gguf q4_k_m --modelfile
```

Hyperparameters are pinned to Daniel Han-Chen's verified Gemma 4 31B Unsloth
notebook (`r=32, alpha=32, all-linear, max_grad_norm=0.3, weight_decay=0.001,
adamw_8bit, gemma-4-thinking chat template`). See [docs/unsloth.md](docs/unsloth.md)
for the rationale, source pinning, and bug-fix gotchas.

### Fetch demo content

```bash
# Edit scripts/demo_clips.yaml first to point at the YouTube IDs you want
python -m scripts.fetch_demo_clips --output-dir assets/demo
# Trims each clip to ≤50s (TRIBE's hard cap) and writes a manifest.json
```

---

## Project layout

```
cortex/
├── cortex/                  # main package: GPU scheduler, queue, pipeline,
│                              errors, GCP fallback client
├── hermes/                  # Hermes Agent fork — tools + agent config
├── cli/                     # typer-based CLI
├── webapp/                  # FastAPI + WebSocket + Three.js viewer
├── gcp/                     # Cloud Run worker + Dockerfiles + Cloud Build
├── scripts/
│   ├── regions.py           # 20 brain regions across all 8 Yeo networks
│   ├── templates.py         # 5 QA template families (per SPEC §8)
│   ├── backends.py          # LLM backends: stub / ollama / anthropic
│   ├── generate_neuro_dataset.py  # supervised dataset generator
│   ├── validate_dataset.py  # quality checker → Markdown report
│   ├── train_cortex.py      # Unsloth fine-tune (cortex-gemma-4-e4b)
│   ├── fetch_demo_clips.py  # yt-dlp wrapper + curated YAML clip list
│   └── demo_clips.yaml      # curated Google YouTube clip references
├── docs/
│   ├── unsloth.md           # verified hyperparameters + bug-fix notes
│   ├── turboquant.md        # KV-cache quantization viability assessment
│   ├── gcp.md               # GCP deployment runbook
│   ├── kaggle_writeup.md    # Kaggle submission draft (~1,200 words)
│   └── video_script.md      # 3-minute demo video shot list
├── tests/                   # 263 unit + integration tests, all passing
├── SPEC.md                  # full technical specification
├── SPRINT_PLAN.md           # day-by-day hackathon sprint
└── CLAUDE.md                # working notes for Claude Code
```

---

## Hackathon submission

| Track | Goal |
|-------|------|
| **Health & Sciences Impact** | Make TRIBE v2 brain analysis accessible to non-specialists |
| **Ollama track** | End-to-end local pipeline running entirely on a single 5090 |
| **Unsloth track** *(stretch)* | `cortex-gemma-4-e4b` fine-tune — Gemma 4 specialized for neuroscience interpretation |

Deliverables (as required by the hackathon rules):

- [x] Public GitHub repository (this one) — Apache 2.0
- [x] Kaggle write-up draft — see [docs/kaggle_writeup.md](docs/kaggle_writeup.md) (~1,200 words)
- [x] Demo video shot list — see [docs/video_script.md](docs/video_script.md) (recording pending)
- [ ] Live demo at `cortex.redteamkitchen.com` *(Cloud Run pending)*
- [ ] Public HuggingFace model: `RedTeamKitchen/cortex-gemma-4-e4b` *(post-fine-tune)*
- [ ] 3-minute YouTube demo video *(record after the fine-tune lands)*

---

## Licensing & attribution

The Cortex code in this repository is **Apache 2.0** licensed (see [LICENSE](LICENSE)). Bundled or referenced third-party works retain their own licenses — see [NOTICE](NOTICE) for the full attribution table.

Two notes worth surfacing:

- **TRIBE v2 weights are CC-BY-NC 4.0** — non-commercial only. Do not use Cortex with TRIBE v2 in a commercial product without obtaining a separate license from Meta.
- **"Gemma" is a trademark of Google LLC.** Cortex is not endorsed by Google. Per [Google's Gemma naming guidelines](https://ai.google.dev/gemma/prohibited_use_policy), the fine-tuned model is published as `RedTeamKitchen/cortex-gemma-4-e4b` (Gemma referenced in the path, not the product name).

---

## Status

Active development for the Gemma 4 Good Hackathon (deadline: **May 18, 2026, 6:59 PM CDT**).

Built by [Alexios Bluff Mara LLC](https://redteamkitchen.com) (dba Red Team Kitchen).
