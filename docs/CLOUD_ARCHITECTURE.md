# Cortex — Cloud Architecture

**`ALEXIOS BLUFF MARA × ILLINOIS STATE UNIVERSITY`**
*Research conducted in association with [Illinois State University](https://illinoisstate.edu), Bloomington–Normal, IL.*

---

```yaml
# TLDR
what:    Cortex's cloud side of the Mercury × Cortex stack — fMRI ingest,
         Vertex AI retraining, and L4-GPU inference fallback when the 5090 is offline.
not:     A replacement for the local-first 5090 path. The relay still routes to the
         Cloudflare Tunnel first; cloud GPU is fallback + retraining only.
cost:    ~$33/hr managed Vertex AI (8× A100 spot ≈ $9.92/hr); L4 fallback ~$0.67/hr.
sla:     Cloud inference fallback < 90s cold start (TRIBE v2 + L4 warmup).
status:  Planning. cortex-relay + 5090 + Cloudflare Tunnel + cortex-tribe-worker (preview)
         are live. cortex-train, fMRI ingest, and Vertex AI hookup are unbuilt.
```

This document is the Cortex-side companion to `D:\mercury\docs\ARCHITECTURE.md`. The Mercury doc is the master plan; this one details the three new Cortex-specific services and how they relate to the existing local-first stack.

---

## 1. What doesn't change

The cortex relay's existing relationship to the local 5090 is unchanged. `cortex-relay` (Cloud Run, `us-central1`) continues to:

- Serve `gallery.html` and `scan.html` as the always-online public surface at `cortex.redteamkitchen.com`.
- Proxy `POST /api/scan` to the Cloudflare Tunnel (`rtk-5090`) when the 5090 is reachable.
- Fall back to a queued state when the 5090 is offline.
- Authenticate scan submissions against Google OAuth (`hd` claim must be `philanthropytraders.com` or `redteamkitchen.com`).

Nothing in the existing `ARCHITECTURE.md` topology gets ripped out. The new components sit *next to* the relay, not in front of it.

---

## 2. New services

```
═══════════════════════════════════════════════════════════════════════════════
                  GOOGLE CLOUD (abm-isu project, us-central1)
═══════════════════════════════════════════════════════════════════════════════

 ┌──────────────────────────┐    ┌──────────────────────────┐
 │ cortex-relay (existing)  │    │ mercury-gateway (new)    │
 │ Cloud Run — public site  │    │ Cloud Run — Discord-first│
 └────────────┬─────────────┘    └────────────┬─────────────┘
              │                                │
              │  /api/scan                     │  /upload-fmri
              ▼                                ▼
 ┌──────────────────────────┐    ┌──────────────────────────────────────┐
 │ Cloudflare Tunnel        │    │ cortex-fmri-uploads (new)            │
 │ → 5090 (preferred)       │    │ GCS bucket, CMEK + per-user KMS keys │
 │ → tribe-worker (fallback)│    │ NIfTI / DICOM / BIDS / .nii.gz       │
 └──────────────────────────┘    └────────────────┬─────────────────────┘
                                                  │ object.finalize
                                                  ▼
                                  ┌────────────────────────────────────┐
                                  │ cortex-fmri-validator (new)        │
                                  │ Cloud Function gen2                │
                                  │ NIfTI/DICOM/BIDS validation        │
                                  │ writes Firestore fmri_scans record │
                                  └────────────────┬───────────────────┘
                                                   │ (N=50 opted-in scans)
                                                   ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ cortex-train (new)                Cloud Run — orchestrator only     │
 │ ─────────────────                                                   │
 │ Triggered by Cloud Scheduler @ 04:00 CT daily                       │
 │ • Counts opted-in scans not yet used in a training run              │
 │ • If ≥ N: probes 5090 /api/utilization                              │
 │   - 5090 free → dispatch via Cloudflare Tunnel                      │
 │   - else → vertex.create_custom_training_job(a2-highgpu-8g, spot)   │
 │ • Writes versioned weights to gs://cortex-tribe-models/{run_id}/    │
 │ • Runs cortex-train-smoketest on a held-out scan                    │
 │ • Promotes to gs://cortex-tribe-models/current/ if MSE within tol   │
 │ • Notifies contributors via Mercury Discord webhook                 │
 └─────────────────────────┬───────────────────────────────────────────┘
                           │
                           ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ cortex-models (new)              GCS bucket: cortex-tribe-models    │
 │ ─────────────────                                                   │
 │ Versioned TRIBE v2 weights                                          │
 │   └── {run_id}/  → tribe_v2.safetensors, schaefer400_proj.npy,      │
 │                    train_metrics.json, smoke_report.json            │
 │   └── current/   → symlink-style copy of the latest promoted run    │
 │ CMEK at rest. WIF read for cortex-tribe-worker + 5090 watcher only. │
 └─────────────────────────┬───────────────────────────────────────────┘
                           │
        ┌──────────────────┴───────────────────┐
        ▼                                      ▼
 ┌──────────────────────────┐   ┌──────────────────────────────────────┐
 │ Local 5090 watcher        │   │ cortex-tribe-worker (existing,       │
 │ (existing) — pulls        │   │ promoted to fallback role)           │
 │ current/ at next idle     │   │ Cloud Run + L4 GPU, min=0, max=2     │
 │ window, hot-swaps weights │   │ Loads gs://cortex-tribe-models/      │
 │                           │   │   current/ at container start        │
 └──────────────────────────┘   └──────────────────────────────────────┘
```

---

## 3. cortex-train — the retraining orchestrator

`cortex-train` is a small Cloud Run service whose only job is to decide *where* training runs and to wait for the result. It does not contain any TRIBE v2 code. The actual training happens in one of two places:

**Path A — local 5090 (preferred).** Free at the margin, fastest, but only available when the 5090 is online and the GPU is idle. The orchestrator does this gating with a single HTTP call:

```
GET https://cortex.redteamkitchen.com/api/utilization

→ {
    "accepting": true,
    "tribe_active": false,
    "gemma_active": false,
    "free_vram_gb": 28.4,
    "queue_depth": 0
  }
```

If `accepting && !tribe_active && free_vram_gb > 24`, the orchestrator pushes a Cloud Tasks message to a queue that the 5090 polls via the same tunnel. The local node runs the retrain end-to-end and uploads the resulting weights to `gs://cortex-tribe-models/{run_id}/` with a service-account-scoped signed URL.

**Path B — Vertex AI (fallback).** When the 5090 is busy, offline, or the queue depth exceeds 1, the orchestrator falls through to a Vertex AI custom training job. Concretely:

| Knob              | Value                                                                |
|-------------------|----------------------------------------------------------------------|
| Machine type      | `a2-highgpu-8g` (8× A100 40GB)                                       |
| Pricing           | ~$33.07/hr managed; spot tier ~$9.92/hr                              |
| Strategy          | Spot by default, with checkpointing every 1k steps                   |
| Container image   | `us-central1-docker.pkg.dev/abm-isu/cortex/tribe-v2-train:latest`    |
| Args              | `--run-id {uuid} --scans-bucket cortex-fmri-uploads --base-weights gs://cortex-tribe-models/current/` |
| Output bucket     | `gs://cortex-tribe-models/{run_id}/`                                 |

**Smoke-test gating.** A second Cloud Run job, `cortex-train-smoketest`, loads the freshly trained weights against a held-out 30-second clip from the original Courtois NeuroMod set, computes per-network MSE on the Schaefer-400 / Yeo-7 projection, and compares to the currently promoted weights. Tolerance is configurable per-network — defaults to ±15% MSE drift. Pass = `cp gs://…/run_id/* gs://…/current/`. Fail = post to `#cortex-train` on Discord and stop.

---

## 4. cortex-models — versioned weights bucket

A single GCS bucket, `cortex-tribe-models`, with two prefixes:

- `gs://cortex-tribe-models/{run_id}/` — every successfully completed training run, regardless of whether it was promoted. We keep these for at least 90 days for audit + rollback.
- `gs://cortex-tribe-models/current/` — the currently promoted weights. The bucket has a versioned-object policy so a bad promotion can be rolled back with `gsutil cp -r` against an older generation.

CMEK at rest. Read access is granted via WIF only to:
- `cortex-tribe-worker@abm-isu.iam.gserviceaccount.com` (Cloud Run inference fallback)
- The 5090's tunnel-side service account (for hot-swap on local idle)

No public read, ever. Weights are MIT-friendly TRIBE v2 fine-tunes but the contributor scans are private — we don't ship the training data, only the model artifact.

---

## 5. cortex-tribe-worker — promoted to inference fallback

Today the `cortex-tribe-worker` Cloud Run service is in preview (allowlist required for Cloud Run GPU). Its role today is "exists, doesn't run 24/7." Its role under this plan:

- **Primary inference target stays the local 5090** via the tunnel. Nothing changes for users of `cortex.redteamkitchen.com` while the 5090 is up.
- **When the 5090 is offline** AND the warm-pool latency budget allows (i.e. the user is on the *balanced* or *batch* tier — see the Mercury doc, §7), `cortex-relay` routes the inference call to `cortex-tribe-worker` instead of queueing.
- The worker loads `gs://cortex-tribe-models/current/` at container start. A new promotion redeploys the worker with a fresh image; cold starts pull the weights once and cache them on the L4-attached SSD.
- Cold start: ~90 s (container pull + L4 GPU attach + TRIBE v2 weights load + first inference). This is *not* in the interactive budget. It is acceptable in batch tier.

Cost: ~$0.67/hr GPU + ~$0.05/hr CPU/RAM = ~$0.72/hr active. Per scan (~6 minutes of work), cloud cost is ~$0.07. This is in line with the existing free-tier-friendly economics; the worker is only ever running when the 5090 cannot answer.

---

## 6. Resilience + automation

| Component                  | Health / trigger                            | Failure mode                                  |
|----------------------------|---------------------------------------------|------------------------------------------------|
| `cortex-train` orchestrator| Cloud Scheduler 04:00 CT daily              | If both 5090 and Vertex AI fail, retry next day |
| Vertex AI training job     | Built-in retry (≤3) + checkpointing every 1k steps | On exhaustion, alert + leave run_id in a `_failed/` prefix |
| `cortex-train-smoketest`   | Runs on every successful train completion   | Fail = no promotion + Discord webhook to `#cortex-train` |
| `cortex-models` bucket     | Object-versioning + 90-day retention        | Bad promotion → manual rollback via `gsutil cp` of older generation |
| `cortex-tribe-worker`      | Cloud Run health check on `/healthz`        | Auto-restart; alert at 3 fails                |
| 5090 weights watcher       | Polls `current/` every 15 min when idle     | If poll fails 4× in a row, Discord alert      |
| Daily cost report          | Cloud Scheduler 09:00 CT                    | Webhook posts yesterday's Vertex + Cloud Run spend by SKU |

Operator alerts route to the same Discord webhook as Mercury (`#mercury-ops` for cross-stack alerts; `#cortex-train` for training-specific events).

---

## 7. Endpoints touched

| Endpoint                                          | Purpose                                                  |
|---------------------------------------------------|----------------------------------------------------------|
| `https://cortex.redteamkitchen.com/api/utilization` | 5090 live status (existing — used by the orchestrator)  |
| `http://192.168.0.34:8765/...`                    | Local FastAPI (existing — proxied via Cloudflare Tunnel) |
| `http://localhost:11434/...`                      | Ollama (existing — narration tier on the 5090)           |
| `gs://cortex-fmri-uploads/{discord_id}/{scan_id}/` | New — fMRI ingest, signed-URL upload only               |
| `gs://cortex-tribe-models/{run_id}/`              | New — versioned training artifacts                       |
| `gs://cortex-tribe-models/current/`               | New — currently promoted weights                         |

---

## 8. Open questions

1. **Vertex AI region.** A100 availability is tighter in `us-central1` than in `us-east4` or `europe-west4`. Do we accept queue waits in `us-central1` to keep everything co-located, or run training in whichever region has spot A100 capacity and pay ~10% extra egress?
2. **Spot vs on-demand.** Spot a2-highgpu-8g is $9.92/hr but can be preempted with 30 s notice. With 1k-step checkpointing, a typical fine-tune restart loses ~3 min. Acceptable for nightly retrain — confirm this matches ISU's expectations.
3. **5090 hot-swap window.** Pulling fresh weights mid-day is disruptive if the GPU is in active use. Do we restrict watcher polls to 03:00–05:00 CT, or rely on the `/api/utilization` `accepting` flag?
4. **Schaefer-400 vs subject-specific parcellation.** The smoke test uses Schaefer-400 / Yeo-7. If contributor scans use a different parcellation, do we project to Schaefer-400 at ingest, or store the raw and project at training time?

---

*Last updated: May 2026. Status: planning. Cortex-side build order: `cortex-fmri-uploads` bucket + KMS keys → `cortex-fmri-validator` Cloud Function → `cortex-train` orchestrator → first end-to-end Vertex AI dry run with 50 synthetic scans.*
