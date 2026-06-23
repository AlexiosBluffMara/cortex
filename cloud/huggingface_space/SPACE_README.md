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
than the FastAPI worker contract used by Docker Spaces, Modal, and RunPod.

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
contract, so use the included UI/API there as a no-cost experiment path. Use a
Docker Space, Modal, RunPod, or another ASGI-capable GPU host when Cortex needs
`CORTEX_CLOUD_TRIBE_ENDPOINT` directly.
