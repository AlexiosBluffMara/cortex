# Cortex — TRIBE v2 Brain Foundation Model + Gemma 4

> Submit a video clip. Watch 20,484 cortical vertices light up in real-time. Hear it explained at your level.

Built for the [Gemma 4 Good Hackathon](https://www.kaggle.com/competitions/gemma-4-good-hackathon) (Health & Sciences track) and the [Nous Research + Kimi Hackathon](https://....) (Creative track).

## What it does

1. Upload a video, audio clip, image, or text (≤ 50 MB, ≤ ~2 min practical)
2. **TRIBE v2** predicts cortical BOLD responses at 20,484 fsaverage5 vertices × 2 Hz
3. **Gemma 4** interprets the activation at three audience levels simultaneously:
   - **General** — high-school register, no jargon
   - **College** — large-scale networks, functional anatomy
   - **Clinical** — Yeo-7 networks, laterality, peak timing, clinical framing
4. Interactive **Three.js viewer** animates the per-vertex activation in real-time

Runs fully local on an RTX 5090 — no cloud, no API keys.

## Model limits

| Parameter | Value |
|---|---|
| TRIBE v2 TR | 0.5 s (2 Hz BOLD) |
| Timepoint mapping | t = N → N × 0.5 s |
| Hemodynamic lag | 5 s pre-applied |
| Surface | fsaverage5, 20,484 vertices |
| Training pool | 25 subjects (group-averaged; not diagnostic) |
| Practical max clip | ~120 s (~240 timepoints × 20,484 × 4B = ~20 MB/scan) |
| Gemma tier model | E4B (fast), 26B MoE (standard), 31B dense (expert) |
| Gemma context | 4 K – 32 K tokens depending on tier |

**t=7 means 3.5 s into the prediction. t=11 means 5.5 s.** Peak activation around t=7–14 (3.5–7 s) is typical for visual stimuli with the 5 s HRF lag already corrected.

## Architecture

```
  Upload → media_gate (Gemma multimodal description)
        → TRIBE v2 pipeline (V-JEPA2 vision + wav2vec audio + Llama text)
        → BrainAnalysis (Schaefer-400 / Yeo-7 parcellation)
        → Gemma narrate × 3 tiers (general / college / clinical)
        → WebSocket broadcast → Three.js viewer (per-vertex animation)
```

GPU scheduler: IDLE → GEMMA_ACTIVE → TRIBE_ACTIVE — eviction-driven, with OOM recovery. The two models cannot coexist on 32 GB VRAM.

## Quickstart

```bash
git clone https://github.com/AlexiosBluffMara/cortex.git
cd cortex
uv venv C:/Users/soumi/cortex/.venv
source C:/Users/soumi/cortex/.venv/Scripts/activate
uv pip install -e ".[dev]"
ollama pull gemma4:e4b
uvicorn webapp.server:app --host 0.0.0.0 --port 8765 --reload
# open http://localhost:8765
```

## Tests

```bash
pytest tests/ -v                    # unit tests (no GPU needed)
pytest tests/ -v -m "not e2e"      # exclude e2e (need running server)
pytest tests/e2e/ -m e2e           # e2e (requires uvicorn running)
```

## Production readiness checklist

- [ ] Replace in-memory ScanRegistry with Redis + TTL
- [ ] Add auth (API key middleware or OAuth)
- [ ] Rate-limit uploads per IP
- [ ] Add Prometheus metrics on inference latency
- [ ] Cloudflare Tunnel or nginx TLS termination for the public endpoint
- [ ] TRIBE v2 is group-averaged (25 subjects): not a personal diagnostic tool — add disclaimer to UI

## License

TRIBE v2 model weights: CC-BY-NC 4.0 (Meta). Gemma 4: Gemma Terms of Use (Google). All code: MIT.
