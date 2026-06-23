# TRIBE v2 Cloud GPU Options

Status: implementation-backed provider screen, June 23, 2026.

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
- `CORTEX_CLOUD_TRIBE_TOKEN` is optional bearer auth for a deployed worker.

Implemented cloud artifacts:

- `cloud/tribe_worker/app.py` is the FastAPI worker contract for Docker Spaces,
  Modal, RunPod, or any ordinary HTTP GPU host.
- `cloud/tribe_worker/Dockerfile` packages that worker.
- `cloud/huggingface_space/app.py` is the Gradio adapter for Hugging Face
  ZeroGPU experiments.
- `cloud/huggingface_space/SPACE_README.md` is the Space card/front matter to
  copy into a Hugging Face Space root.

The FastAPI worker is what Cortex can call directly today. The ZeroGPU adapter
is a no-cost experiment target, but it is not the same HTTP contract because
ZeroGPU currently requires Gradio.

Before routing production traffic to any cloud worker, check:

```powershell
Invoke-RestMethod https://your-worker.example/api/tribe/readiness
```

`contract_ready=true` means the HTTP proxy contract is online. In
`CORTEX_WORKER_MODE=real`, `real_mode_ready=true` must also be true; otherwise
the worker will reject real scans quickly with the missing module, weights, or
CUDA readiness details.

## Provider Screen

| Provider | Best Use | Cost Shape | Fit For TRIBE v2 |
| --- | --- | --- | --- |
| Hugging Face ZeroGPU Space | No/low-cost public experiment | Free shared GPU allocation for Gradio Spaces; daily quota by account tier | Best first prototype if TRIBE can fit the Gradio + ZeroGPU call model. Not ideal for arbitrary long video jobs or direct FastAPI proxying. |
| Hugging Face GPU Space | Simple always-available demo | Hourly hardware; can sleep/pause | Good operationally, but idle billing matters unless sleep is aggressive. Current docs list L4 at $0.80/hr, A10G small at $1.00/hr, A100 large at $2.50/hr. |
| Hugging Face Inference Endpoint | Managed endpoint with autoscaling | Minute-level billing while initializing/running; scale-to-zero exists | Cleaner production endpoint, but requires subscription/card and still has cold starts when scaled to zero. |
| Modal | Serverless Python GPU function | Starter plan lists $30/mo credits plus compute; autoscale up/down | Strong candidate for spiky scans because code can stay Python-native and run only per request. |
| RunPod Serverless | Containerized GPU worker API | Serverless inference, GPU listed per hour/second | Strong candidate once we want a container-native endpoint; current serverless list shows A100 at $2.72/hr and L40/L40S/6000 Ada class at $1.90/hr. |
| AWS/GCP direct GPU VM | Full control | Usually cheapest only if used heavily or with spot/preemptible | Overkill for sparse demos; best reserved for training or if serverless cold starts are unacceptable. |

## Recommendation

1. Keep local RTX 5090 as the default public path.
2. Deploy `cloud/huggingface_space` as a ZeroGPU smoke test, starting in
   `CORTEX_WORKER_MODE=fake`, then switching to `real` only after dependencies
   and weights load inside the Space. This tests whether TRIBE can tolerate
   ZeroGPU duration, queueing, and Gradio constraints.
3. Deploy `cloud/tribe_worker` as a FastAPI worker on the cheapest paid
   HTTP-compatible GPU surface once we need the production webapp to call it
   directly. For sparse demos, try Modal first because of monthly starter
   credits. For a container-native endpoint with GPU SKU control, try RunPod
   Serverless next. For a simpler Hugging Face-only route, use a paid Docker
   Space or Inference Endpoint.
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

Run:

```powershell
pytest tests\integration\test_webapp.py tests\integration\test_tribe_worker.py tests\integration\test_huggingface_space.py -W ignore::pytest.PytestConfigWarning
```

## Sources Checked

- Hugging Face ZeroGPU docs describe shared dynamic GPU allocation, free GPU
  access for Spaces, Gradio-only compatibility, `@spaces.GPU`, daily quotas,
  and the $1 per 10 minutes over-quota credit rate:
  https://huggingface.co/docs/hub/en/spaces-zerogpu
- Hugging Face GPU Spaces docs list current hourly prices and note upgraded
  Spaces run indefinitely by default unless sleep/pause is configured:
  https://huggingface.co/docs/hub/en/spaces-gpus
- Hugging Face Inference Endpoints pricing documents minute-level cost while an
  endpoint is initializing/running, GPU hourly rates, and scale-to-zero quota
  behavior: https://huggingface.co/docs/inference-endpoints/en/pricing
- Hugging Face Inference Endpoints autoscaling docs state endpoints can scale to
  zero after idle time: https://huggingface.co/docs/inference-endpoints/en/autoscaling
- Modal pricing lists $30/month starter credits and serverless autoscaling:
  https://modal.com/pricing
- RunPod pricing lists pod and serverless GPU pricing, including lower-cost
  24 GB/48 GB pod options and request-driven serverless worker pricing:
  https://www.runpod.io/pricing
