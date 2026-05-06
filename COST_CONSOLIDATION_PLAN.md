# Cost Consolidation Plan — ABM-ISU / Cortex / Red Team Kitchen

**Author:** Soumit Lahiri (soumitlahiri@philanthropytraders.com)
**Date:** 2026-05-01
**Audience:** Soumit, ISU faculty collaborators, GCP support engineer assigned to case on billing account `01F58E-689697-ADE7FC`.
**Status:** Action plan, post-incident. The $2K Gemini API spike on project `abm-isu` (created 2026-04-26) is contained: `aiplatform.googleapis.com` and `generativelanguage.googleapis.com` are disabled, four budgets and a Pub/Sub kill switch are live.

---

## Section 1 — Executive summary

- **All LLM inference moves off Vertex / Gemini API.** Local Ollama on the RTX 5090 is the default; Cloudflare Workers AI is the rate-limited fallback; HuggingFace Inference is last resort. Direct calls to `google.generativeai`, `vertexai`, `openai`, `anthropic` are forbidden — they must go through `D:\cortex\inference_router\`.
- **Storage, cache, vectors, and edge HTTP move from GCP/Firebase to Cloudflare.** R2 (zero egress), KV (free 100k reads/day), Vectorize ($0.04 / 1M queried), Pages, Workers. GCS keeps only `abm-isu_cloudbuild` artifacts.
- **GCP keeps four roles only:** Cloud Run for the relay (already wired to the tunnel), the kill-switch infra (budgets + Pub/Sub + Cloud Function), BigQuery for billing-export, and Workspace mail. Vertex stays disabled until academic credits fund it.
- **Cost ceilings stay enforced in code, not in policy.** Daily $100 hard cap via `daily-cap-watcher`, monthly $50 soft cap via `budget-killer` detaching billing at 90%. Cloudflare side has no hard cap because Free-tier usage at single-digit-thousands of users is < $5/month.
- **Expected 90-day net savings: $850 (low) / $1,400 (mid) / $2,200 (high)** versus continuing on the current GCP-heavy footprint, before any credit refund or research grant. With the Start-tier $2K credit applied, GCP spend rounds to zero through August 2026.

---

## Section 2 — Where every dollar goes today, where it should go tomorrow

Pricing is current to 2026-05-01. Scale assumption: single-digit thousands of monthly users, ~50k inference requests/month, ~5 GB stored blobs, ~500 MB embeddings.

| Workload | Today | Tomorrow | Today $/mo | Tomorrow $/mo |
|---|---|---|---|---|
| LLM inference | Gemini 2.5 Pro direct ($1.25 in / $5 out per 1M tok) | Ollama on 5090 (primary) → Workers AI Neurons (fallback) → HF Inference (last resort) | $80–400+ (the spike showed this can blow past $2,000 in days) | $0–8 |
| Image transforms | Cloud Run + sharp / on-demand Cloud Functions | Cloudflare Image Resizing on Free zone (per-request, no egress) | $5–20 | $0–3 |
| Vector search | Vertex Vector Search ($0.45/GB/mo + node-hours) | Cloudflare Vectorize (5M queried + 30M stored free, then $0.04 / 1M queried) | $30–90 | $0 |
| Object storage | GCS Standard us-central1 ($0.02/GB + $0.12/GB egress) | Cloudflare R2 ($0.015/GB stored, $0 egress, 10 GB free) | $5–25 | $0 |
| KV / cache | Firestore reads ($0.06 per 100k) | Cloudflare KV (free 100k reads/day, 1k writes/day) | $3–15 | $0 |
| Static site | Firebase Hosting | Cloudflare Pages (already on apex; just register `www`) | $0–5 | $0 |
| Edge HTTP / orchestration | Cloud Run `cortex-relay` + `cortex-webapp` | Keep `cortex-relay` (it terminates the tunnel). Move `cortex-webapp` static parts to Pages; only keep dynamic endpoints on Cloud Run | $8–25 | $4–10 |
| Marketing analytics | None | Cloudflare Web Analytics (cookieless) + GA4 via GTM (both free) | $0 | $0 |
| Logs / alerting | Cloud Logging + Monitoring | Stay on Cloud Logging for GCP-native; Logpush → R2 only after volume justifies (>50 GB/mo). Skip a SIEM until then. | $1–5 | $1–5 |
| Budget enforcement | Budgets + Pub/Sub + `budget-killer` + `daily-cap-watcher` | **No change. Already correct.** | $0 (under free tier) | $0 |

**Rationale per row:**

- **Inference.** The 5090 has 32 GB GDDR7 and runs `gemma4:26b`, `cortex-gemma-4-e4b`, `embeddinggemma:300m` locally. For the hackathon judging window, a queue is acceptable because user concurrency is bounded. Workers AI free tier (10k Neurons/day) absorbs spillover. HF Inference is rate-limited but free under HF Pro; reserve for cold starts when 5090 is offline.
- **Image transforms.** Cloudflare Image Resizing on Free does up to 5,000 transformations/month free; we are nowhere near that.
- **Vectors.** Vectorize free tier covers our entire embedding workload for the next 6 months. Vertex Vector Search node-hours alone would dwarf our compute budget.
- **Object storage.** R2 wins on egress. Even at 100 GB stored (we are at 8 MB), R2 = $1.50/mo with no egress, GCS = $2.00 + egress.
- **KV.** Firestore is overkill for an edge cache that only needs string-keyed reads. KV is purpose-built and free at our scale.
- **Cloud Run.** Keep `cortex-relay` because it is the orchestrator behind the `rtk-5090` tunnel and integrates with GCP IAM. Slim `cortex-webapp` down — anything that can be static goes to Pages.
- **Logs.** Cloud Logging is free up to 50 GiB ingest/month per project. We will not exceed that.

---

## Section 3 — What stays on Google (hard requirements)

| Service | Why it stays |
|---|---|
| Gemma fine-tuning + research credits | Only place that funds GPU time at scale. ISU faculty PI applies for Research Credits. |
| Workspace `philanthropytraders.com` | Already paid, separate billing, contains all org mail and the Workspace alias plan for `redteamkitchen.com`. |
| BigQuery `billing_export` dataset | Required for the daily-cap query and for any analytics Cloudflare GraphQL Analytics API cannot answer. |
| Vertex AI | **Disabled today; re-enable only when academic credits fund it.** Never on personal credit card. |
| Cloud Run `cortex-relay` | Terminates the `rtk-5090` Cloudflare Tunnel; tightly coupled with the demo orchestrator. Migration not worth the risk before May 18 deadline. |
| Cloud Scheduler `daily-cap-watcher-hourly` | Already deployed, drives the hard cap. |
| Pub/Sub topic `budget-overrun` + Cloud Function `budget-killer` | Already deployed, detaches billing at 90% of any budget. Do not touch. |
| Cloud Function `daily-cap-watcher` | Already deployed, queries Cloud Monitoring hourly. Do not touch. |
| Artifact Registry `cortex` | Holds 5.9 GB of Cloud Run images. Prune to last 3 tags per service quarterly; do not migrate. |

Everything else is a candidate for Cloudflare migration.

---

## Section 4 — What moves to Cloudflare in the next 7 days

All commands below are git-bash on Windows. They assume `wrangler` is on PATH and the Cloudflare API token at `~/.cloudflare/credentials` has been upgraded per `D:\cortex\CLOUDFLARE_TOKEN_UPGRADE.md`. Account ID: `abdd39b91455684cd6ac0e47e8bdb0cc`.

### 1. Create AI Gateway and route inference router's Workers AI fallback through it

```bash
export CF_ACCOUNT_ID=abdd39b91455684cd6ac0e47e8bdb0cc
export CF_API_TOKEN=$(cat ~/.cloudflare/credentials)

curl -X POST \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/ai-gateway/gateways" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"id":"cortex","cache_ttl":3600,"cache_invalidate_on_update":true,"collect_logs":true,"rate_limiting_interval":60,"rate_limiting_limit":120,"rate_limiting_technique":"sliding"}'
```

**Verification:** `curl -H "Authorization: Bearer ${CF_API_TOKEN}" "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/ai-gateway/gateways/cortex"` returns 200 with the gateway object.
**Edit `D:\cortex\inference_router\workers_ai_client.py`** to set base URL to `https://gateway.ai.cloudflare.com/v1/${CF_ACCOUNT_ID}/cortex/workers-ai`.
**Time:** 15 min.

### 2. Create R2 bucket and migrate `cortex-public-scans`

```bash
wrangler r2 bucket create cortex-public-scans
gcloud storage cp -r gs://cortex-public-scans/* /tmp/cortex-scans/
wrangler r2 object put cortex-public-scans/ --file /tmp/cortex-scans/ --recursive
```

**Verification:** `wrangler r2 object list cortex-public-scans | wc -l` matches `gcloud storage ls -r gs://cortex-public-scans/** | wc -l`.
After parity confirmed: `gcloud storage rm -r gs://cortex-public-scans` (only after Cortex code is repointed at R2).
**Time:** 20 min.

### 3. Create KV namespace `rtk-cache`

```bash
wrangler kv namespace create rtk-cache
```

Add the returned `id` to `D:\cortex\wrangler.toml` under `[[kv_namespaces]]`.
**Verification:** `wrangler kv namespace list | grep rtk-cache`.
**Time:** 5 min.

### 4. Create Vectorize index `rtk-embeddings`

```bash
wrangler vectorize create rtk-embeddings --dimensions=768 --metric=cosine
```

Matches `embeddinggemma:300m` output dimensions.
**Verification:** `wrangler vectorize list | grep rtk-embeddings`.
**Time:** 5 min.

### 5. Register `www.redteamkitchen.com` on Pages (closes WWW_FIX.md)

```bash
curl -X POST \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/pages/projects/redteamkitchen/domains" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name":"www.redteamkitchen.com"}'
```

Then add the CNAME on the zone (Cloudflare auto-issues the cert when proxied):

```bash
ZONE_ID=$(curl -s -H "Authorization: Bearer ${CF_API_TOKEN}" \
  "https://api.cloudflare.com/client/v4/zones?name=redteamkitchen.com" | jq -r '.result[0].id')

curl -X POST "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"type":"CNAME","name":"www","content":"redteamkitchen.pages.dev","proxied":true}'
```

**Verification:** `curl -I https://www.redteamkitchen.com` returns 200 with a Cloudflare-issued cert.
**Time:** 10 min including cert provisioning.

### 6. Add Cloudflare Web Analytics to the Pages site

In dash (no API): Analytics & Logs → Web Analytics → Add a site → `redteamkitchen.com` (use automatic setup since the zone is on Cloudflare). Snippet auto-injects.
**Verification:** Visit the site, then refresh the Web Analytics dashboard within 60 seconds.
**Time:** 5 min.

### 7. Add Turnstile to the Cortex demo brain-scan upload endpoint

```bash
curl -X POST \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/challenges/widgets" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name":"cortex-upload","domains":["redteamkitchen.com","www.redteamkitchen.com","cortex-relay-*.run.app"],"mode":"managed"}'
```

Capture the returned `sitekey` and `secret`. Add `sitekey` to the upload form and validate `secret` server-side in `cortex-relay`.
**Verification:** Submit the form once with the widget rendered; relay logs show `cf-turnstile-response` validation = success.
**Time:** 30 min including code wiring.

### 8. Workers AI free-tier health check via the inference router

Add to `D:\cortex\inference_router\health.py`:

```python
def workers_ai_health() -> bool:
    r = httpx.post(
        f"https://gateway.ai.cloudflare.com/v1/{CF_ACCOUNT_ID}/cortex/workers-ai/@cf/meta/llama-3.1-8b-instruct",
        headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
        json={"prompt": "health", "max_tokens": 1},
        timeout=10,
    )
    return r.status_code == 200
```

Wire into `daily-cap-watcher` so the hourly run also confirms the fallback path is green.
**Verification:** Force Ollama down for 60 seconds; inference router returns Workers AI output without error.
**Time:** 30 min.

### 9. (Stretch) Move marketing pages to Workers + Pages Functions for SSR

Skip unless the demo grows interactive. If pursued: `wrangler pages functions` scaffold inside `D:\cortex\webapp\functions\`. Estimate 4 hours; defer until after May 18.

---

## Section 5 — Credit programs

See `D:\cortex\GCP_BILLING_SUPPORT_CASE.md` for the full applications. Summary table:

| Program | Amount | Owner | Timing |
|---|---|---|---|
| First-time spike refund | up to $2,000 (the actual loss) | Soumit (open case now) | 1–3 weeks |
| Google for Startups — Start tier | $2,000 / 1 yr | Soumit | 1–2 weeks |
| Google Cloud Research Credits — Faculty PI | $5,000 | ISU professor as PI | 4–6 weeks |
| Google Cloud Research Credits — PhD student | $1,000 | ISU PhD collaborator | 4–6 weeks |
| Black Founders Fund | up to $350,000 | Soumit (next cohort) | when applications open |
| Gemma 4 Good Hackathon team credit | TBD | Kaggle / DeepMind | hackathon-dependent |

Floor (refund + Start tier): **$4,000**. Ceiling without BFF: **$10,000**.

---

## Section 6 — Cost ceiling going forward

- **Daily hard cap, GCP:** $100. Enforced by `daily-cap-watcher` (Cloud Function, hourly Scheduler trigger). Triggers `budget-killer` if exceeded.
- **Monthly soft cap, GCP:** $50 across all projects on billing account `01F58E-689697-ADE7FC`. Enforced by 4 budgets → Pub/Sub `budget-overrun` → `budget-killer` Cloud Function detaches billing at 90%.
- **Cloudflare:** No hard cap. Expected spend < $5/month for the next 6 months on Free tier across the entire stack. Workers Paid ($5 base) only if request volume exceeds 100k/day; revisit then.
- **Inference router invariant.** No GenAI provider may bypass `D:\cortex\inference_router\`. Pre-commit hook check (add to `.pre-commit-config.yaml`):

  ```yaml
  - repo: local
    hooks:
      - id: no-direct-genai
        name: Forbid direct GenAI imports outside inference_router
        entry: bash -c 'git diff --cached --name-only -z | xargs -0 -I{} grep -lE "^(from|import) (google\.generativeai|google\.cloud\.aiplatform|vertexai|openai|anthropic)" {} | grep -v "inference_router/" && exit 1 || exit 0'
        language: system
        pass_filenames: false
  ```

  Same rule applies to `D:\mercury\`. Any file importing `google.generativeai`, `google.cloud.aiplatform`, `vertexai`, `openai`, or `anthropic` directly outside `inference_router/` is a bug, not a feature.

---

## Section 7 — One-page diagram

```
                            +---------------------------+
                            |        Browser / API      |
                            |          client           |
                            +-------------+-------------+
                                          |
                                          | HTTPS (TLS via Cloudflare)
                                          v
+-----------------------------------------+-----------------------------------------+
|                              CLOUDFLARE EDGE                                      |
|                                                                                   |
|  redteamkitchen.com / www -> Pages (static marketing)                             |
|  api.redteamkitchen.com   -> Worker (router, rate-limit, Turnstile verify)        |
|  Web Analytics  |  Image Resizing  |  Turnstile  |  KV (rtk-cache)                |
|                                                                                   |
|  Tunnel "rtk-5090" -----> RTX 5090 desktop (Windows 11)                           |
|                                                                                   |
+-----+-----------------------------------------------------------------------+-----+
      |                                                                       |
      | (dynamic / GenAI)                                                     | (storage)
      v                                                                       v
+-----+----------------------+                                  +-------------+----------+
|  Cloud Run cortex-relay    |                                  |  R2 (cortex-public-    |
|  us-central1, 1vCPU/512Mi  |                                  |  scans, blobs, logs    |
|  (orchestrator)            |                                  |  via Logpush later)    |
+-----+----------------------+                                  +------------------------+
      |                                                                       
      | HTTPS via tunnel
      v
+-----+----------------------+
|  inference_router (5090)   |
|  D:\cortex\inference_router|
+--+------+-----------+------+
   |      |           |
   v      v           v
+--+--+ +-+-------+ +-+----------+
|Olla-| |Workers  | |HuggingFace |
|ma   | |AI       | |Inference   |
|26B  | |(via AI  | |(last       |
|local| |Gateway, | |resort)     |
|PRE- | |cached,  | |            |
|FERRD| |fallback)| |            |
+-----+ +---------+ +------------+

Stored data:
  R2          -> blobs (scans, images, model artifacts)
  KV          -> edge cache, session tokens
  Vectorize   -> 768-dim cosine embeddings (rtk-embeddings)
  D1          -> any future relational state (none today)
  BigQuery    -> billing_export only; research output if academic credits fund it

Kill switch (untouched):
  Cloud Monitoring -> daily-cap-watcher (Scheduler, hourly)
                  \-> Pub/Sub budget-overrun -> budget-killer -> detach billing @ 90%
```

---

## Appendix — order of operations

1. Today: items 3, 4, 5 (KV, Vectorize, www). Each under 10 minutes; clears the `WWW_FIX.md` debt.
2. Tomorrow: items 1, 8 (AI Gateway + health check). Re-points the inference router fallback through a cached, observable endpoint.
3. Within 3 days: items 2, 7 (R2 migration, Turnstile). Closes the storage cost vector and the upload abuse vector.
4. Within 7 days: item 6 (Web Analytics).
5. After May 18 hackathon: item 9 if demand justifies.

End of plan.
