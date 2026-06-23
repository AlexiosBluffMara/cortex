# Off-Google Migration Plan — Cortex

**Trigger:** $2K unexpected Gemini API bill (May 1, 2026). All paid Google AI surfaces are now off-limits. Hackathon submission deadline: **May 18, 2026**.

**Scope:** Replace Cloud Run (`cortex-relay`, `cortex-webapp`) with a Cloudflare-only edge stack fronting the local seratonin/seratonin/baby-pi cluster via the existing `rtk-5090` tunnel. Keep public site fast. Keep public GitHub. Keep Cortex demo working end-to-end before deadline.

---

## 1. Executive summary

- **Replace** Cloud Run path for `cortex.redteamkitchen.com` with Cloudflare Pages (UI) + Workers (API) + Queues (async) + R2 (artifacts) + AI Gateway (cache/observability) — all routed to the 5090 inference router via the existing `rtk-5090` tunnel.
- **Expected Cloudflare spend at demo scale (100 scans/day, 50K page views/mo): under $5/mo.** Almost everything is on free tier; only Queues + R2 storage drift above zero.
- **Delete after cutover:** Cloud Run `cortex-relay`, Cloud Run `cortex-webapp`. Active GCS buckets if any. **Keep:** `budget-killer` Cloud Function + `daily-cap-watcher` (insurance until GCP project is closed).
- **Inference moves fully off paid Google APIs.** Local Ollama (5090) is primary; Workers AI ($0.011/Neuron) is paid fallback only when the 5090 is unreachable; HuggingFace Inference is the second fallback. No Vertex/Gemini API in any hot path.
- **Deadline:** Pages cutover today, Worker + R2 within 2 days, Queues + AI Gateway within 5 days, Cloud Run deletion within 10 days, demo recorded by **May 18**.

---

## 2. New traffic flow

```
                           cortex.redteamkitchen.com
                                       |
                                       v
                        +-----------------------------+
                        |  Cloudflare Pages (static)  |  <-- replaces Cloud Run UI
                        |  D:\cortex\pages-site\      |
                        +--------------+--------------+
                                       |
                            (browser fetches HTML/JS/CSS)
                                       |
                                       v
            +----------- /api/* route bound to Worker -------------+
            |                                                       |
            |   Worker: cortex-api  (D:\cortex\workers\cortex-api)   |
            |   - POST /api/scan     -> upload to R2, enqueue        |
            |   - GET  /api/healthz  -> ping inference router        |
            |   - GET  /api/job/:id  -> R2 result lookup             |
            |                                                       |
            +-------------------+---------------+-------------------+
                                |               |
                                v               v
                         R2 buckets       Cloudflare Queue
                         - cortex-public-   - cortex-jobs (producer)
                           scans (input)
                         - cortex-public-
                           results (output)
                                                |
                                                v
                                +---------------+----------------+
                                |  Worker: cortex-worker         |
                                |  (queue consumer)              |
                                |  fetch job -> POST through     |
                                |  AI Gateway -> tunnel host     |
                                +---------------+----------------+
                                                |
                                                v
                       gateway.ai.cloudflare.com/v1/<acc>/primary/...
                                                |
                                                v
                          inference.redteamkitchen.com  (Cloudflare Tunnel rtk-5090)
                                                |
                                                v
                          inference router on seratonin (port 8765 FastAPI)
                                                |
                              +-----------------+-----------------+
                              |                 |                 |
                              v                 v                 v
                       Ollama (5090)     seratonin (Mac)    baby-pi (BitNet)
                              |                 |                 |
                              +-----------------+-----------------+
                                                |
                                                v
                                       result JSON + PNG
                                                |
                                                v
                                Worker writes to R2 (cortex-public-results)
                                                |
                                                v
                       Browser polls /api/job/:id -> 200 with signed URL
```

Key properties:

- Cloudflare orchestrates; the cluster computes. Nothing on GCP touches user traffic.
- The tunnel (`rtk-5090`) is the only ingress to local hardware. Authenticated via Cloudflare Access service token from the Worker.
- AI Gateway in front of the inference router gives free request-level caching, logging, and per-token cost view. Identical scans hit cache for free.

---

## 3. Step-by-step migration

### a) Move the Cortex landing page to Pages

Pull current HTML from Cloud Run, drop into a Pages project, deploy, swap DNS.

```bash
# 1. Snapshot the current Cloud Run landing page
mkdir -p /d/cortex/pages-site
curl -sSL https://cortex.redteamkitchen.com/ -o /d/cortex/pages-site/index.html

# 2. Pull any referenced assets (manual review — open the file and grep for /static/, /assets/, etc.)
grep -oE 'src="[^"]+"|href="[^"]+"' /d/cortex/pages-site/index.html | sort -u

# 3. First-time Pages project create + deploy
cd /d/cortex/pages-site
wrangler pages project create cortex-site --production-branch=main
wrangler pages deploy . --project-name=cortex-site --branch=main --commit-dirty=true
```

**Custom domain mapping** (cortex.redteamkitchen.com -> Pages):

```bash
# Requires Pages:Edit on the API token. If still read-only, see fallback below.
wrangler pages project domain add cortex-site cortex.redteamkitchen.com
```

**Token-permission gap:** the current `~/.cloudflare/credentials` token is READ-ONLY. To run the domain-add command above you need an account-scoped token with these permission groups added:

- `Account` -> `Cloudflare Pages` -> **Edit**
- `Account` -> `Workers Scripts` -> **Edit**
- `Account` -> `Workers R2 Storage` -> **Edit**
- `Account` -> `Workers KV Storage` -> **Edit**
- `Account` -> `Workers AI` -> **Edit** (for AI Gateway)
- `Zone` -> `DNS` -> **Edit** (zone: redteamkitchen.com)
- `Zone` -> `Workers Routes` -> **Edit**

**Dashboard fallback** (if token stays read-only):

1. dash.cloudflare.com -> Workers & Pages -> `cortex-site` -> Custom domains -> Set up a custom domain -> `cortex.redteamkitchen.com` -> Continue.
2. Cloudflare auto-creates the CNAME and removes the old Cloud Run record after Pages is verified.

**Verify:**

```bash
curl -sI https://cortex.redteamkitchen.com/ | grep -iE 'server|cf-ray'
# Expect: server: cloudflare  (was: server: Google Frontend)
```

### b) Move the API endpoints to a Worker

```bash
mkdir -p /d/cortex/workers/cortex-api
cd /d/cortex/workers/cortex-api
wrangler init . --yes --type=javascript
```

Replace the generated files with the ones below.

**`/d/cortex/workers/cortex-api/wrangler.toml`:**

```toml
name = "cortex-api"
main = "src/index.js"
compatibility_date = "2026-04-01"
account_id = "REPLACE_WITH_ACCOUNT_ID"

routes = [
  { pattern = "cortex.redteamkitchen.com/api/*", zone_name = "redteamkitchen.com" }
]

[[r2_buckets]]
binding = "SCANS"
bucket_name = "cortex-public-scans"

[[r2_buckets]]
binding = "RESULTS"
bucket_name = "cortex-public-results"

[[queues.producers]]
binding = "JOBS"
queue = "cortex-jobs"

[[kv_namespaces]]
binding = "CACHE"
id = "REPLACE_WITH_KV_ID"

[vars]
INFERENCE_BASE = "https://inference.redteamkitchen.com"
```

**`/d/cortex/workers/cortex-api/src/index.js`:**

```js
export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname;

    if (req.method === "POST" && path === "/api/scan") {
      const ct = req.headers.get("content-type") || "";
      if (!ct.startsWith("video/mp4") && !ct.startsWith("multipart/form-data")) {
        return json({ error: "expected video/mp4 or multipart" }, 415);
      }
      const id = crypto.randomUUID();
      const body = await req.arrayBuffer();
      if (body.byteLength > 50 * 1024 * 1024) return json({ error: "max 50MB" }, 413);
      await env.SCANS.put(`${id}.mp4`, body, { httpMetadata: { contentType: "video/mp4" } });
      await env.JOBS.send({ id, key: `${id}.mp4`, ts: Date.now() });
      return json({ id, status: "queued" }, 202);
    }

    if (req.method === "GET" && path === "/api/healthz") {
      const r = await fetch(`${env.INFERENCE_BASE}/v1/healthz`, { cf: { cacheTtl: 5 } });
      return json({ inference: r.ok ? "up" : "down", code: r.status });
    }

    if (req.method === "GET" && path.startsWith("/api/job/")) {
      const id = path.slice("/api/job/".length);
      const obj = await env.RESULTS.get(`${id}.json`);
      if (!obj) return json({ id, status: "pending" }, 202);
      return new Response(obj.body, { headers: { "content-type": "application/json" } });
    }

    return json({ error: "not found" }, 404);
  }
};

function json(o, status = 200) {
  return new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });
}
```

**Deploy:**

```bash
cd /d/cortex/workers/cortex-api
wrangler deploy
```

### c) Cloudflare Queues for async inference

Create the queue, then a consumer Worker.

```bash
wrangler queues create cortex-jobs

mkdir -p /d/cortex/workers/cortex-worker
cd /d/cortex/workers/cortex-worker
wrangler init . --yes --type=javascript
```

**`/d/cortex/workers/cortex-worker/wrangler.toml`:**

```toml
name = "cortex-worker"
main = "src/index.js"
compatibility_date = "2026-04-01"
account_id = "REPLACE_WITH_ACCOUNT_ID"

[[r2_buckets]]
binding = "SCANS"
bucket_name = "cortex-public-scans"

[[r2_buckets]]
binding = "RESULTS"
bucket_name = "cortex-public-results"

[[queues.consumers]]
queue = "cortex-jobs"
max_batch_size = 1
max_batch_timeout = 5
max_retries = 3
dead_letter_queue = "cortex-jobs-dlq"

[vars]
INFERENCE_BASE = "https://gateway.ai.cloudflare.com/v1/REPLACE_ACC_ID/primary/compat/openai"
TUNNEL_HOST = "https://inference.redteamkitchen.com"
```

**`/d/cortex/workers/cortex-worker/src/index.js`:**

```js
export default {
  async queue(batch, env) {
    for (const msg of batch.messages) {
      const { id, key } = msg.body;
      try {
        const resp = await fetch(`${env.TUNNEL_HOST}/v1/generate`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ scan_url: `r2://cortex-public-scans/${key}`, job_id: id })
        });
        if (!resp.ok) throw new Error(`router ${resp.status}`);
        const out = await resp.text();
        await env.RESULTS.put(`${id}.json`, out, { httpMetadata: { contentType: "application/json" } });
        msg.ack();
      } catch (e) {
        msg.retry();
      }
    }
  }
};
```

**Deploy + DLQ:**

```bash
wrangler queues create cortex-jobs-dlq
wrangler deploy
```

### d) R2 buckets

```bash
wrangler r2 bucket create cortex-public-scans
wrangler r2 bucket create cortex-public-results

# Public read for results (so the browser can fetch via CDN)
wrangler r2 bucket dev-url enable cortex-public-results

# Lifecycle: delete raw scans after 7 days (privacy + storage cost)
cat > /tmp/scans-lifecycle.json <<'EOF'
{ "rules": [ { "id": "expire-7d", "enabled": true, "conditions": { "prefix": "" }, "deleteObjectsTransition": { "condition": { "type": "Age", "maxAge": 604800 } } } ] }
EOF
wrangler r2 bucket lifecycle set cortex-public-scans --file=/tmp/scans-lifecycle.json
```

### e) AI Gateway in front of the inference router

```bash
# Create gateway via API (one-shot, dashboard equivalent: AI -> AI Gateway -> Create Gateway)
ACC=$(jq -r .account_id ~/.cloudflare/credentials)
TOK=$(jq -r .api_token   ~/.cloudflare/credentials)
curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/$ACC/ai-gateway/gateways" \
  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"id":"primary","cache_invalidate_on_update":true,"cache_ttl":86400,"collect_logs":true,"rate_limiting_interval":60,"rate_limiting_limit":120,"rate_limiting_technique":"sliding"}'
```

Update the inference router on seratonin to send Workers AI / HF calls through the gateway:

```bash
# On seratonin, edit the router env (path adjusted to where it actually lives)
# Add:
#   AI_GATEWAY_URL=https://gateway.ai.cloudflare.com/v1/<ACCOUNT_ID>/primary
#   AI_GATEWAY_TOKEN=<the new write-scoped token>
# Then restart the router service.
```

### f) Delete cortex-relay from Cloud Run (cutover, last)

Only after Pages + Worker path is verified end-to-end (curl `/api/scan`, watch R2, poll `/api/job/<id>`, see narration come back).

```bash
gcloud config set project abm-isu
gcloud run services delete cortex-relay  --region=us-central1 --quiet
gcloud run services delete cortex-webapp --region=us-central1 --quiet

# Verify nothing else in us-central1 is running
gcloud run services list --region=us-central1
```

**Do NOT delete:** `budget-killer` Cloud Function, `daily-cap-watcher`, the `budget-overrun` Pub/Sub topic. These stay until the GCP project itself is shut down.

### g) Optional: shut down GCP entirely (after credits applied)

```bash
# Export billing audit trail to R2 first
bq extract --destination_format=NEWLINE_DELIMITED_JSON \
  'abm-isu:billing_export.gcp_billing_export_v1_*' \
  'gs://abm-isu-billing-export/billing-*.jsonl'

# Mirror the GCS bucket into R2 (rclone with both remotes configured)
rclone sync gcs:abm-isu-billing-export r2:cortex-public-results/audit/billing/ --progress

# Detach billing -> all GCP services go PERMISSION_DENIED
gcloud beta billing projects unlink abm-isu

# Eventually
gcloud projects delete abm-isu --quiet
```

---

## 4. Cost comparison (100 scans/day, 50K page views/month)

| Workload | GCP today | Cloudflare-only | Notes |
| --- | --- | --- | --- |
| Static landing page | Cloud Run min=0, ~$0-3/mo | Pages free | Unlimited bandwidth, 500 builds/mo free |
| API endpoints (`/api/scan`, `/api/healthz`) | Cloud Run | Workers free (100K req/day) | 3K req/day at this scale, well inside free |
| Async inference queue | n/a | Queues ~$0.05/mo | $0.40 / M operations; ~3K msgs/day = 90K/mo |
| Object storage (videos + PNGs) | GCS Standard $0.020/GB + egress | R2 ~$0.30/mo | $0.015/GB-mo, zero egress; ~20GB at steady state |
| AI inference (was Gemini API) | $7-21 / M tokens **(killed)** | $0 local + Workers AI $0.011/Neuron fallback | Cache hits via AI Gateway are free |
| LLM observability | none | AI Gateway free | Logs, cache hit rate, per-model spend |
| Vector embeddings (future) | Vertex Vector Search ($) | Vectorize free <5M queries/mo | $0.04/M queried beyond that |
| KV / cache | Firestore ($) | Workers KV free <100K reads/day | Plenty of headroom |
| Billing kill-switch | Cloud Function (cents) | Stays on GCP | Cheap insurance, do not delete |
| **Total** | **$2K incident risk** | **<$5/mo** | Savings = the $2K plus future Gemini spend |

---

## 5. Public GitHub strategy

- Repo: `github.com/AlexiosBluffMara/cortex` — already public, stays public.
- README slant: architecture + reproducibility (judge bait). One diagram = the section-2 ASCII flow.
- Demo submission must include: 30-second screen capture, public URL `https://cortex.redteamkitchen.com`, GitHub link, license badge.
- License: **Apache 2.0** (matches Gemma 4 weights, Cloudflare SDK examples, BitNet).
- Secret scanning: install `gitleaks` and run pre-commit + CI.

```bash
# pre-commit
curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_windows_x64.zip -o /tmp/gl.zip
unzip -j /tmp/gl.zip gitleaks.exe -d /c/Users/soumi/bin/
echo '#!/bin/sh\n/c/Users/soumi/bin/gitleaks.exe protect --staged --redact' > /d/cortex/.git/hooks/pre-commit
chmod +x /d/cortex/.git/hooks/pre-commit

# GitHub Actions step (one file, free for public repos)
mkdir -p /d/cortex/.github/workflows
cat > /d/cortex/.github/workflows/gitleaks.yml <<'EOF'
name: gitleaks
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2
EOF
```

---

## 6. What stays "Google-adjacent" (and why)

Acceptable:

- **Gemma 4 weights** (Apache 2.0) — downloaded once, run locally on the 5090. Not "Google Cloud."
- **Workspace email** under `philanthropytraders.com` — already paid through 2026; no migration value.
- **GitHub** — Microsoft, listed only for clarity.
- **GA4 + Search Console + GTM** — free, gives SEO/traffic data with no good alternative. Single GTM snippet on the Pages site.

Explicitly OFF:

- Vertex AI / Gemini API / any paid Google model endpoint — **kill switch enforced.**
- Cloud Run — replaced by Workers + Pages.
- Cloud Functions — kept ONLY for `budget-killer`. No new ones.
- Firestore / Cloud Storage in hot paths — replaced by KV / R2.
- BigQuery — only the historical billing-export read; no new pipelines.

Single-line escape hatch (documented, not preferred): if a feature unavoidably needs a Google API, route it through the inference router on seratonin using a service-account JSON pinned to a daily cap of $1 via the `budget-overrun` topic. Never expose a Google API to a public Worker route.

---

## 7. Deadline ladder

| When | What | Done-when |
| --- | --- | --- |
| **Today (May 1)** | Pages site live at `cortex.redteamkitchen.com`. Cloud Run still serving as backstop. | `curl -I` shows `server: cloudflare` |
| **+2 days (May 3)** | `cortex-api` Worker deployed, R2 buckets exist, `/api/scan` returns a job id, R2 has the upload | `wrangler r2 object list cortex-public-scans` shows test object |
| **+5 days (May 6)** | Queue + `cortex-worker` consumer wired, AI Gateway in front of router, end-to-end demo under 30s | A POST -> result fetch round-trip in browser DevTools < 30s |
| **+10 days (May 11)** | `cortex-relay` and `cortex-webapp` deleted from Cloud Run. budget-killer remains. | `gcloud run services list --region=us-central1` empty for cortex-* |
| **May 18** | 30-second submission video recorded showing full Cloudflare-only flow. PR opened on submission repo. | Submission accepted in hackathon portal |

---

## 8. Risks + mitigations

| Risk | Mitigation |
| --- | --- |
| Tunnel down -> dynamic routes 530 | Worker reads last-good response from KV (TTL 1h), serves cached result with `X-Cortex-Stale: true` |
| 5090 down (power, reboot, driver crash) | Inference router falls through to seratonin, then baby-pi (BitNet b1.58); Worker retries via Queue retry policy (max 3) |
| Cloudflare token still read-only at deploy time | Every step labels its required permission group; dashboard click-paths documented as fallback for Pages domain mapping and AI Gateway create |
| AI Gateway adds 50-150ms latency | Acceptable; cache hits make repeated identical prompts free and instant |
| Tailscale outage | Tunnel is Cloudflare-native — does not depend on Tailscale. Only Mac/RPi node-to-node fallback uses Tailscale; the public ingress is unaffected |
| R2 bucket public exposure of user video | Lifecycle rule deletes scans after 7 days; only `cortex-public-results` is publicly readable, scans use signed URLs only |
| Hackathon judges hit cold-start | AI Gateway cache + KV last-good response keep cold path under 5s |
| Worker hits 100K req/day free cap | Add Workers Paid ($5/mo) — only if traffic justifies; cap protects accidental spend |

---

## 9. Hand-off checklist (paste-ready, run on `seratonin` in git-bash)

```bash
# === Phase A: Pages (today) ===
# 1
mkdir -p /d/cortex/pages-site && curl -sSL https://cortex.redteamkitchen.com/ -o /d/cortex/pages-site/index.html
# 2
cd /d/cortex/pages-site && wrangler pages project create cortex-site --production-branch=main
# 3
cd /d/cortex/pages-site && wrangler pages deploy . --project-name=cortex-site --branch=main --commit-dirty=true
# 4  (requires Pages:Edit token; else use dashboard fallback in section 3a)
wrangler pages project domain add cortex-site cortex.redteamkitchen.com

# === Phase B: API Worker + R2 (+2 days) ===
# 5
wrangler r2 bucket create cortex-public-scans && wrangler r2 bucket create cortex-public-results
# 6
mkdir -p /d/cortex/workers/cortex-api && cd /d/cortex/workers/cortex-api && wrangler init . --yes --type=javascript
# 7  (after pasting wrangler.toml + src/index.js from section 3b)
cd /d/cortex/workers/cortex-api && wrangler deploy

# === Phase C: Queues + consumer + AI Gateway (+5 days) ===
# 8
wrangler queues create cortex-jobs && wrangler queues create cortex-jobs-dlq
# 9
mkdir -p /d/cortex/workers/cortex-worker && cd /d/cortex/workers/cortex-worker && wrangler init . --yes --type=javascript
# 10  (after pasting wrangler.toml + src/index.js from section 3c)
cd /d/cortex/workers/cortex-worker && wrangler deploy
# 11
ACC=$(jq -r .account_id ~/.cloudflare/credentials); TOK=$(jq -r .api_token ~/.cloudflare/credentials); curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/$ACC/ai-gateway/gateways" -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d '{"id":"primary","cache_ttl":86400,"collect_logs":true}'

# === Phase D: Verify end-to-end (+5 days) ===
# 12
curl -sI https://cortex.redteamkitchen.com/ | grep -iE 'server|cf-ray'
# 13
JOB=$(curl -sS -X POST -H "content-type: video/mp4" --data-binary @/d/cortex/test/sample.mp4 https://cortex.redteamkitchen.com/api/scan | jq -r .id) && echo "job=$JOB"
# 14
for i in 1 2 3 4 5 6; do curl -sS https://cortex.redteamkitchen.com/api/job/$JOB | jq .; sleep 5; done

# === Phase E: Delete Cloud Run (+10 days, only after phase D passes) ===
# 15
gcloud config set project abm-isu && gcloud run services delete cortex-relay --region=us-central1 --quiet && gcloud run services delete cortex-webapp --region=us-central1 --quiet
```

---

*Last updated: 2026-05-01. Owner: Soumit Lahiri. Target completion: 2026-05-18 (Gemma 4 Good hackathon submission).*
