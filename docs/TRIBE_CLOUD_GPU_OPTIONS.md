# TRIBE v2 Cloud GPU Options

Status: implementation-backed provider screen, official pricing/docs refreshed
June 23, 2026.

## Goal

Cortex should keep the local RTX 5090 path as the default, but expose a funded
cloud TRIBE path for moments when Seratonin is offline, busy, or unsuitable for
a public demo. The cloud path must be explicit because it can spend money.

## Current WebUI Contract

- `compute_target=local` is the public default.
- `compute_target=cloud_hf`, `cloud_modal`, or `cloud_runpod` requires funded
  access.
- The backend returns `cloud_tribe_not_configured` until a worker URL is set in
  `CORTEX_CLOUD_TRIBE_ENDPOINT`.
- `CORTEX_CLOUD_TRIBE_PROVIDER` labels the configured provider.
- `CORTEX_CLOUD_TRIBE_MODE=fastapi` keeps the default direct worker contract.
- `CORTEX_CLOUD_TRIBE_MODE=gradio` calls a Hugging Face Gradio Space
  `api_name=/scan`, then caches the returned BOLD `.npy` locally so the normal
  Cortex `/api/scan/{id}/bold-vertex` viewer path still works.
- `CORTEX_CLOUD_TRIBE_TOKEN` is optional bearer auth for a deployed worker.
- `CORTEX_CLOUD_TRIBE_HF_TOKEN` is optional Hugging Face auth for private
  Gradio Spaces.

Implemented cloud artifacts:

- `cloud/tribe_worker/app.py` is the FastAPI worker contract for Docker Spaces,
  Modal, RunPod, or any ordinary HTTP GPU host.
- `cloud/tribe_worker/Dockerfile` packages that worker.
- `cloud/huggingface_space/app.py` is the Gradio adapter for Hugging Face
  ZeroGPU experiments.
- `cloud/huggingface_space/SPACE_README.md` is the Space card/front matter to
  copy into a Hugging Face Space root.
- `scripts/export_huggingface_space.ps1` exports a ready Space directory under
  `build/huggingface-space` with `app.py`, `README.md`, `requirements.txt`,
  `cloud/`, and `cortex/` included.

The FastAPI worker remains the clean production contract because it exposes
queued scans, status hydration, source media, and raw BOLD bytes over ordinary
HTTP. The Gradio path is now supported as an adapter mode for ZeroGPU: Cortex
keeps its own public API stable, calls the Space in the background, copies the
returned `.npy` into the local scan cache, and serves Three.js from the same
`/bold-vertex` endpoint used by local and FastAPI-worker scans.

Before routing production traffic to any cloud worker, check:

```powershell
Invoke-RestMethod https://your-worker.example/api/tribe/readiness
```

`contract_ready=true` means the HTTP proxy contract is online. In
`CORTEX_WORKER_MODE=real`, `real_mode_ready=true` must also be true; otherwise
the worker will reject real scans quickly with the missing module, weights, or
CUDA readiness details.

Then run the real end-to-end verifier:

```powershell
python -m cloud.tribe_worker.verify `
  --endpoint https://your-worker.example `
  --token $env:CORTEX_CLOUD_TRIBE_TOKEN `
  --require-real
```

This is the deployment gate. It submits a tiny stimulus, polls completion,
downloads `bold-vertex`, verifies the `(T, 20484)` byte shape, and confirms
source media can be served back to the browser.

For a Gradio Space, use an actual funded upload/text scan through the webapp as
the final integration test:

```powershell
$env:CORTEX_CLOUD_TRIBE_ENDPOINT = "https://your-space.hf.space"
$env:CORTEX_CLOUD_TRIBE_MODE = "gradio"
$env:CORTEX_CLOUD_TRIBE_PROVIDER = "huggingface-zerogpu"
```

Then submit `compute_target=cloud_hf` with funded access and confirm the scan
reaches `status=complete` and `/api/scan/{id}/bold-vertex` returns
`X-N-Vert: 20484`.

Before the webapp is involved, verify the Space itself:

```powershell
python -m cloud.huggingface_space.verify `
  --endpoint $env:CORTEX_CLOUD_TRIBE_ENDPOINT `
  --hf-token $env:CORTEX_CLOUD_TRIBE_HF_TOKEN `
  --require-real
```

Without `--require-real`, the command is a fake-mode smoke test. With
`--require-real`, it fails unless the Space payload reports `worker_mode=real`,
`real_mode_ready=true`, and a downloaded BOLD array shaped `(T, 20484)`.

## Provider Screen

| Provider | Best Use | Cost Shape | Fit For TRIBE v2 |
| --- | --- | --- | --- |
| Hugging Face ZeroGPU Space | No-cost public experiment | Free shared dynamic GPU allocation for Gradio Spaces; hosting your own ZeroGPU Space requires PRO for personal accounts or Team/Enterprise for orgs | Best first prototype if TRIBE can fit the Gradio + ZeroGPU call model. Use `CORTEX_CLOUD_TRIBE_MODE=gradio`; Cortex will cache the returned `.npy` and preserve the public viewer API. |
| Hugging Face GPU Space | Simple demo with a sleepable GPU | Per-minute billing while Starting/Running; paid Spaces run indefinitely by default unless sleep/pause is configured | Good operationally if we set aggressive sleep. Current pricing lists L4 24 GB at $0.80/hr, A10G 24 GB at $1.00/hr, and A100 80 GB at $2.50/hr. |
| Hugging Face Inference Endpoint | Managed autoscaling endpoint | Requires active subscription/card; hourly prices are calculated by minute while endpoints initialize/run | Cleaner production endpoint, but likely more setup than a Space or serverless worker for this custom TRIBE stack. |
| Modal | Serverless Python GPU function | Starter plan is $0 plus compute and lists $30/month included compute; autoscale up/down | Strong candidate for spiky scans because code can stay Python-native and run only per request. Verify real TRIBE in a GPU function before committing. |
| RunPod Serverless | Containerized worker API | Request-driven serverless GPU pricing listed per hour/second | Strong candidate once we want a container-native endpoint; current serverless list includes 24 GB L4/A5000/3090 at $0.69/hr, 32 GB 5090 at $1.58/hr, 48 GB A6000/A40 at $1.22/hr, and A100 at $2.72/hr. |
| AWS/GCP direct GPU VM | Full control | Usually cheapest only if used heavily or with spot/preemptible | Overkill for sparse demos; best reserved for training or if serverless cold starts are unacceptable. |

## Recommendation

1. Keep local RTX 5090 as the default public path.
2. Deploy `cloud/huggingface_space` as a ZeroGPU smoke test, starting in
   `CORTEX_WORKER_MODE=fake`, then switching to `real` only after dependencies
   and weights load inside the Space. This tests whether TRIBE can tolerate
   ZeroGPU duration, queueing, and Gradio constraints.
   Export the uploadable directory with:

   ```powershell
   pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/export_huggingface_space.ps1 -Clean
   ```

   Point the webapp at it with `CORTEX_CLOUD_TRIBE_MODE=gradio` once the Space
   is healthy.

3. Deploy `cloud/tribe_worker` as a FastAPI worker on the cheapest paid
   HTTP-compatible GPU surface once we need the production webapp to call it
   directly. For the absolute cheapest direct contract, try a 24 GB RunPod
   Serverless or HF L4/A10G class GPU first and let `verify_worker --require-real`
   decide whether TRIBE really fits. If 24 GB fails, move to 32 GB 5090 or 48 GB
   A6000/A40/L40-class hardware. For Python-native deployment with starter
   credits, try Modal. For a Hugging Face-only route, use a paid Docker Space
   with aggressive sleep or an Inference Endpoint.
4. Use AWS/GCP/NVIDIA Cloud only when we need long-running control or training.

## Local-To-Cloud Test Shape

The integration suite now proves:

- paid cloud uploads are rejected until the `boileruphammerdown` access code is
  supplied;
- a funded upload can proxy to a bearer-token-protected worker;
- a funded text scan can proxy to the same worker;
- scan status hydration preserves worker `has_bold_vertex` metadata;
- BOLD vertex bytes and source media proxy back through the main Cortex app.
- the cloud worker reports real-mode readiness and fails fast when a GPU image
  is missing TRIBE dependencies, weights, or CUDA.
- `python -m cloud.tribe_worker.verify` submits a scan to a worker, validates
  returned BOLD bytes, and fails unless real mode is ready when `--require-real`
  is supplied.
- `python -m cloud.huggingface_space.verify` calls the Gradio `scan` API,
  validates the returned scan JSON plus downloaded `.npy`, and can fail unless
  the Space is in real TRIBE mode.

Run:

```powershell
pytest tests\integration\test_webapp.py tests\integration\test_tribe_worker.py tests\integration\test_huggingface_space.py -W ignore::pytest.PytestConfigWarning
```

## Sources Checked

- Hugging Face pricing lists Spaces hardware, including CPU Basic free, ZeroGPU
  free with up to 96 GB VRAM, L4 at $0.80/hr, A10G small at $1.00/hr, and A100
  large at $2.50/hr: https://huggingface.co/pricing
- Hugging Face ZeroGPU docs describe shared dynamic GPU allocation, free GPU
  access, PRO/Team requirements for hosting ZeroGPU Spaces, and 48 GB/96 GB
  ZeroGPU sizes: https://huggingface.co/docs/hub/en/spaces-zerogpu
- Hugging Face GPU Spaces docs state billing is per minute while the Space is
  Starting/Running, free Spaces sleep after inactivity, upgraded Spaces run
  indefinitely by default, and sleeping paid Spaces are not billed:
  https://huggingface.co/docs/hub/en/spaces-gpus
- Hugging Face Inference Endpoints pricing documents subscription/card
  requirements and minute-calculated cost while endpoints initialize/run:
  https://huggingface.co/docs/inference-endpoints/en/pricing
- Modal pricing lists serverless autoscaling and the Starter plan with
  $30/month included compute: https://modal.com/pricing
- RunPod pricing and Serverless docs list request-driven serverless GPU pricing,
  including 24 GB L4/A5000/3090 at $0.69/hr, 32 GB 5090 at $1.58/hr, 48 GB
  A6000/A40 at $1.22/hr, and A100 at $2.72/hr:
  https://www.runpod.io/pricing and https://www.runpod.io/product/serverless
