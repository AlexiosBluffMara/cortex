# Cortex Cloud TRIBE Worker

This is the cloud worker contract used by `cortex.redteamkitchen.com` when the
local RTX 5090 is offline or when funded cloud GPU mode is selected.

## API Contract

The main webapp can proxy to this worker by setting:

```powershell
$env:CORTEX_CLOUD_TRIBE_ENDPOINT = "https://your-worker.example"
$env:CORTEX_CLOUD_TRIBE_PROVIDER = "huggingface-zero-gpu" # or modal/runpod
$env:CORTEX_CLOUD_TRIBE_TOKEN = "shared-worker-secret"
```

The worker exposes:

- `GET /healthz`
- `GET /api/tribe/status`
- `POST /api/scan`
- `GET /api/scan/{scan_id}`
- `GET /api/scan/{scan_id}/bold-vertex`
- `GET /api/scan/{scan_id}/bold-simulate`
- `GET /api/scan/{scan_id}/source-media`

This mirrors the subset of the local Cortex API the viewer needs after a scan
is queued.

Set the same secret on the worker as `CORTEX_WORKER_TOKEN`. When configured,
scan submission plus scan/media/BOLD reads require `Authorization: Bearer ...`.

## Local Contract Smoke Test

Fake mode runs without TRIBE weights:

```powershell
python -m uvicorn cloud.tribe_worker.app:app --host 127.0.0.1 --port 8876
```

Then point Cortex at it for a funded-cloud dry run:

```powershell
$env:CORTEX_CLOUD_TRIBE_ENDPOINT = "http://127.0.0.1:8876"
$env:CORTEX_CLOUD_TRIBE_PROVIDER = "local-fake-worker"
$env:CORTEX_CLOUD_TRIBE_TOKEN = ""
```

## Real GPU Mode

Set:

```text
CORTEX_WORKER_MODE=real
```

Real mode imports `cortex.pipeline.run_inference`, so the deployment image must
include the same TRIBE dependencies and weights as the local Seratonin path.

## Provider Notes

- **Hugging Face ZeroGPU:** official docs say ZeroGPU is Gradio-only and uses
  `@spaces.GPU`. Use this FastAPI worker as the contract target for Docker
  Spaces or paid Endpoints; use `cloud/huggingface_space` for no-cost ZeroGPU
  experiments.
- **Modal:** wrap this app in an ASGI web endpoint or call the same processing
  function from a Modal GPU function. Good first paid serverless candidate.
- **RunPod Serverless:** containerize this worker and expose the same HTTP
  routes through a serverless endpoint. Good once we want a container-native GPU
  SKU.
