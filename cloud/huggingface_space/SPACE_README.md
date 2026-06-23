---
title: Cortex TRIBE v2 ZeroGPU
emoji: C
colorFrom: red
colorTo: gray
sdk: gradio
app_file: app.py
pinned: false
hardware: zerogpu
---

# Cortex TRIBE v2 ZeroGPU

This Space is the no-cost/low-cost experiment path for Cortex TRIBE inference.
ZeroGPU is Gradio-only, so this Space exposes a Gradio `scan` function rather
than the FastAPI worker contract used by Docker Spaces, Modal, and RunPod. The
main Cortex webapp can still call this Space by setting
`CORTEX_CLOUD_TRIBE_MODE=gradio`; it will run the Gradio job in the background,
cache the returned BOLD `.npy`, and serve the normal Cortex viewer endpoints.

Set `CORTEX_WORKER_MODE=fake` for a deployment smoke test. Set
`CORTEX_WORKER_MODE=real` only after the Space image contains the TRIBE
dependencies and weights.

Expected output:

- scan JSON metadata
- a `.npy` file shaped like `(T, 20484)` with fsaverage5 vertex BOLD values

Export a Space root from the Cortex repo with:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/export_huggingface_space.ps1 -Clean
```

Upload `build/huggingface-space` as the Space root. Keep fake mode enabled
until the Space logs show that TRIBE dependencies, weights, and CUDA are ready.

For Docker Spaces or any FastAPI-hosted worker, verify the Cortex proxy contract
before pointing the public app at it:

```bash
python -m cloud.tribe_worker.verify --endpoint "$CORTEX_CLOUD_TRIBE_ENDPOINT" --token "$CORTEX_CLOUD_TRIBE_TOKEN" --require-real
```

ZeroGPU Spaces expose the Gradio `scan` function rather than the FastAPI
contract. For Cortex webapp integration, configure:

```bash
export CORTEX_CLOUD_TRIBE_ENDPOINT="https://your-space.hf.space"
export CORTEX_CLOUD_TRIBE_MODE="gradio"
export CORTEX_CLOUD_TRIBE_PROVIDER="huggingface-zerogpu"
```

Verify the Gradio Space directly before enabling it in the webapp:

```bash
python -m cloud.huggingface_space.verify \
  --endpoint "$CORTEX_CLOUD_TRIBE_ENDPOINT" \
  --hf-token "$CORTEX_CLOUD_TRIBE_HF_TOKEN" \
  --require-real
```

Without `--require-real`, the verifier proves the Space function, metadata, and
downloaded BOLD `.npy` shape. With `--require-real`, it also fails unless the
Space payload reports `worker_mode=real` and `real_mode_ready=true`.

Use a Docker Space, Modal, RunPod, or another ASGI-capable GPU host when Cortex
needs the faster direct FastAPI worker contract.
