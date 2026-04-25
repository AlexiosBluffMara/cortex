# GCP deployment runbook

This file is the operator's guide for the GCP side of Cortex: the Cloud Run
webapp, the A100 inference worker, and the secrets/IAM that connect them. The
**runtime client** for the worker is `cortex.gcp_inference.GCPInferenceFallback`
(see `cortex/gcp_inference.py`); this file is about getting the *server*
deployed so the client has something to talk to.

## Architecture

```
                      ┌─────────────────────────────────────┐
                      │ Cloud Run: cortex-webapp            │
                      │   webapp.server:app (FastAPI)       │
                      │   no GPU, 1 CPU, 1 Gi               │
                      └─────────┬───────────────────────────┘
                                │ HTTPS, Identity-token
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
  ┌──────────────┐    ┌────────────────────┐   ┌────────────────────┐
  │ Cloudflare   │    │ Cloud Run: cortex- │   │ Cloud Storage      │
  │ Tunnel       │    │ tribe-worker       │   │ cortex-results     │
  │ → 5090       │    │ A100 / L4, 16 Gi   │   │ scan outputs +     │
  │ (primary)    │    │ inference_api:app  │   │ BOLD .npy          │
  └──────────────┘    └─────────┬──────────┘   └────────────────────┘
                                │
                                ▼
                      ┌────────────────────┐
                      │ Secret Manager     │
                      │ - hf-token         │
                      │ - gcp-inference-   │
                      │   token            │
                      └────────────────────┘
```

The webapp prefers the Cloudflare-tunneled local 5090. When a CUDA OOM hits,
`cortex.gpu_scheduler.run_brain_scan` calls into `GCPInferenceFallback`, which
posts to `cortex-tribe-worker` over HTTPS and polls until the result is back.

## One-time project setup

```bash
# 0. Authenticate
gcloud auth login
gcloud auth application-default login

# 1. Pick (or create) a project
gcloud config set project YOUR_PROJECT_ID
export PROJECT_ID=$(gcloud config get-value project)
export REGION=us-central1

# 2. Enable APIs
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    storage.googleapis.com \
    compute.googleapis.com

# 3. Create the GCS bucket for results + model artifacts
gsutil mb -l "$REGION" "gs://cortex-results-$PROJECT_ID"

# 4. Create secrets
echo -n "$(openssl rand -base64 32)" | \
    gcloud secrets create gcp-inference-token --data-file=- \
    --replication-policy=user-managed --locations="$REGION"

echo -n "$HF_TOKEN" | \
    gcloud secrets create hf-token --data-file=- \
    --replication-policy=user-managed --locations="$REGION"

# 5. Service account for Cloud Run with minimum roles
gcloud iam service-accounts create cortex-runtime \
    --display-name "Cortex Cloud Run runtime"

# Grant Secret Manager + GCS read/write to the SA
SA="cortex-runtime@$PROJECT_ID.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"
```

## Build + deploy via Cloud Build

The whole pipeline is in [`gcp/cloudbuild.yaml`](../gcp/cloudbuild.yaml). Run
it from the repo root:

```bash
gcloud builds submit --config=gcp/cloudbuild.yaml --substitutions=COMMIT_SHA=$(git rev-parse HEAD)
```

This will:

1. Build `gcr.io/$PROJECT_ID/cortex-webapp` from `gcp/Dockerfile.webapp`.
2. Build `gcr.io/$PROJECT_ID/cortex-tribe-worker` from `gcp/Dockerfile.tribe-worker`.
3. Push both to GCR with `:$COMMIT_SHA` and `:latest` tags.
4. Deploy `cortex-webapp` to Cloud Run (no GPU).
5. Deploy `cortex-tribe-worker` to Cloud Run with `--gpu=1 --gpu-type=nvidia-l4`.

**A100 vs L4.** The cloudbuild defaults to L4 because A100 on Cloud Run is in
preview and not available in every project. For real TRIBE inference at
production speed, swap `nvidia-l4` to `nvidia-a100` (if your project has the
preview enabled) or, more reliably, deploy the worker to a Compute Engine
MIG with `a2-highgpu-1g`. See the next section.

## Compute Engine fallback (when Cloud Run GPU isn't available)

If your project doesn't have GPU-on-Cloud-Run access, run the worker on
Compute Engine instead:

```bash
gcloud compute instances create-with-container cortex-tribe-worker \
    --zone="${REGION}-a" \
    --machine-type=a2-highgpu-1g \
    --maintenance-policy=TERMINATE \
    --container-image="gcr.io/$PROJECT_ID/cortex-tribe-worker:latest" \
    --container-env="GCP_INFERENCE_TOKEN=$(gcloud secrets versions access latest --secret=gcp-inference-token)" \
    --container-env="HF_TOKEN=$(gcloud secrets versions access latest --secret=hf-token)" \
    --service-account="cortex-runtime@$PROJECT_ID.iam.gserviceaccount.com" \
    --boot-disk-size=200GB \
    --tags=cortex-worker

# Open the port (8080) only to the webapp's egress IP, ideally
gcloud compute firewall-rules create cortex-worker-allow \
    --network=default \
    --target-tags=cortex-worker \
    --allow=tcp:8080 \
    --source-ranges=$WEBAPP_EGRESS_CIDR
```

Cost: A100 40GB on-demand is ~$2.19/hr (us-central1). Per the SPEC §13, the
total inference cost for the whole hackathon is expected to land at ~$5-10
across a few dozen demo runs.

## Configure the local webapp to use the GCP fallback

Once the worker is deployed, point `cortex.gcp_inference.GCPInferenceFallback`
at it:

```bash
export GCP_INFERENCE_ENDPOINT="$(gcloud run services describe cortex-tribe-worker --region=$REGION --format='value(status.url)')"
export GCP_INFERENCE_TOKEN="$(gcloud secrets versions access latest --secret=gcp-inference-token)"
```

Verify with the local CLI:

```bash
python -c "
import asyncio
from cortex.gcp_inference import default_fallback
fb = default_fallback()
print('available:', fb.available())
print('health:', asyncio.run(fb.health()))
"
```

## Wiring the fallback into the GPU scheduler

Once the env vars are set, register the fallback at startup:

```python
# In webapp/server.py or cli/main.py — wherever the scheduler is bootstrapped
from cortex.gpu_scheduler import get_scheduler
from cortex.gcp_inference import default_fallback

get_scheduler().set_inference_fallback(default_fallback())
```

This is a one-line change — the existing `GPUScheduler.run_brain_scan` already
calls into the fallback on OOM (see `cortex/gpu_scheduler.py`).

## Cost expectations

| Component                  | Pricing                          | Hackathon estimate |
| -------------------------- | -------------------------------- | ------------------ |
| Cloud Run (webapp)         | first 2M requests/mo free        | $0                 |
| Cloud Run (worker, L4 GPU) | ~$0.65/hr GPU + per-request CPU  | $0-3 (cold-start)  |
| Compute Engine A100 40GB   | ~$2.19/hr on-demand, $0.36/hr spot | $5-10              |
| Cloud Storage              | $0.020/GB/mo                     | $0 (~5 GB demo)    |
| Cloud Build                | first 120 build-min/day free     | $0                 |
| Secret Manager             | $0.06 per 10K accesses           | <$0.01             |

Total budget for the hackathon (Apr 24 → May 18): **$10 alarm**. Set:

```bash
gcloud billing budgets create \
    --billing-account=$BILLING_ACCOUNT_ID \
    --display-name=cortex-hackathon \
    --budget-amount=15USD \
    --threshold-rule=percent=0.7 \
    --threshold-rule=percent=1.0
```

## Local development against the worker

You don't need to deploy to GCP to develop the client — the worker runs fine
locally:

```bash
# Terminal 1: worker
GCP_INFERENCE_TOKEN=dev-token \
    .venv/Scripts/python.exe -m uvicorn gcp.cloud_run.inference_api:app --host 0.0.0.0 --port 8080

# Terminal 2: tests / client
GCP_INFERENCE_ENDPOINT=http://localhost:8080 \
GCP_INFERENCE_TOKEN=dev-token \
    .venv/Scripts/python.exe -c "
import asyncio
from cortex.gcp_inference import GCPInferenceFallback
print(asyncio.run(GCPInferenceFallback().health()))
"
```

## Troubleshooting

| Symptom                                       | Likely cause                                                | Fix |
| --------------------------------------------- | ----------------------------------------------------------- | --- |
| `available() == False`                        | `GCP_INFERENCE_ENDPOINT` or `GCP_INFERENCE_TOKEN` unset      | Re-export from secrets |
| Worker `/healthz` returns 503 / 502           | TRIBE weights still downloading on first boot               | Wait ~2 minutes; subsequent requests are fast |
| `403 Forbidden` from worker                   | Bearer token mismatch                                       | Confirm the same secret version is mounted in both webapp + worker |
| `404` on `/infer/{job_id}` mid-poll           | Worker revision swap mid-job (Cloud Run scaled in)          | Use Compute Engine MIG instead, or `--min-instances=1` |
| TRIBE inference times out at 60 minutes       | Cloud Run GPU job ceiling                                   | Move worker to Compute Engine (no time limit) |
| `quota exceeded` on `nvidia-a100`             | A100 not approved for project                               | Request quota OR switch to L4 (`--gpu-type=nvidia-l4`) |
| Cloud Build fails on the CUDA layer (~6 GB)   | Build VM out of disk                                        | Set `options.diskSizeGb=100` in cloudbuild.yaml |

## Secret rotation

```bash
# Generate a new inference token
echo -n "$(openssl rand -base64 32)" | \
    gcloud secrets versions add gcp-inference-token --data-file=-

# Re-deploy both services so they pick up the new version
gcloud builds submit --config=gcp/cloudbuild.yaml
```

## When this file should be updated

- After changing the wire format in `cortex.gcp_inference` or
  `gcp.cloud_run.inference_api` — the architecture diagram + the trouble­shooting
  table need to stay in sync with the contract tests in
  `tests/integration/test_inference_api.py`.
- After enabling A100 on Cloud Run for the project, swap the cloudbuild
  default GPU type from `nvidia-l4` to `nvidia-a100`.
- After landing Terraform IaC (planned but not yet built) — add a
  "Provision via Terraform" section above the `gcloud` commands.
