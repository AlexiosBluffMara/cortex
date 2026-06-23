# TRIBE v2 Cloud GPU Options

Status: first-pass provider screen, June 23, 2026.

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

## Provider Screen

| Provider | Best Use | Cost Shape | Fit For TRIBE v2 |
| --- | --- | --- | --- |
| Hugging Face ZeroGPU Space | No/low-cost public experiment | Free shared GPU allocation for Spaces | Best first prototype if TRIBE can fit the Gradio + ZeroGPU call model. Not ideal for arbitrary long video jobs. |
| Hugging Face GPU Space | Simple always-available demo | Hourly hardware; can sleep/pause | Good operationally, but idle billing matters unless sleep is aggressive. L4 is listed at $0.80/hr, A10G at $1.00/hr, A100 at $2.50/hr. |
| Hugging Face Inference Endpoint | Managed endpoint with autoscaling | Minute-level billing while initializing/running; scale-to-zero exists | Cleaner production endpoint, but requires subscription/card and still has cold starts when scaled to zero. |
| Modal | Serverless Python GPU function | Starter plan has $30/mo credits plus compute; autoscale up/down | Strong candidate for spiky scans because code can stay Python-native and run only per request. |
| RunPod Serverless | Containerized GPU worker API | Serverless inference, GPU listed per hour/second | Strong candidate once we have a Dockerized TRIBE worker; A100 serverless is listed at $2.72/hr, L40/L40S/Ada class at $1.90/hr. |
| AWS/GCP direct GPU VM | Full control | Usually cheapest only if used heavily or with spot/preemptible | Overkill for sparse demos; best reserved for training or if serverless cold starts are unacceptable. |

## Recommendation

1. Prototype Hugging Face ZeroGPU first because it is the only plausible
   no-cost hosted GPU path. Build a tiny Gradio Space that loads TRIBE once and
   exposes an HTTP-compatible `/infer` shim.
2. If ZeroGPU cannot handle TRIBE dependencies, runtime, or video duration,
   move the same worker image to Modal. Modal's starter credits make it the
   most forgiving paid experiment.
3. If the worker needs a container-native endpoint with predictable GPU SKUs,
   use RunPod Serverless next.
4. Keep Hugging Face dedicated Endpoints as a managed but not cheapest option.

## Sources Checked

- Hugging Face ZeroGPU docs describe shared dynamic GPU allocation and free GPU
  access for Spaces: https://huggingface.co/docs/hub/en/spaces-zerogpu
- Hugging Face GPU Spaces docs list L4, A10G, and A100 hourly prices and note
  upgraded Spaces run indefinitely by default unless sleep/pause is configured:
  https://huggingface.co/docs/hub/en/spaces-gpus
- Hugging Face Inference Endpoints pricing documents minute-level cost while an
  endpoint is initializing/running, GPU hourly rates, and scale-to-zero quota
  behavior: https://huggingface.co/docs/inference-endpoints/en/pricing
- Hugging Face Inference Endpoints autoscaling docs state endpoints can scale to
  zero after idle time: https://huggingface.co/docs/inference-endpoints/en/autoscaling
- Modal pricing lists $30/month starter credits and serverless autoscaling:
  https://modal.com/pricing
- RunPod pricing and serverless docs list GPU workload pricing and serverless
  GPU endpoints for request-driven inference:
  https://www.runpod.io/pricing and https://www.runpod.io/product/serverless

