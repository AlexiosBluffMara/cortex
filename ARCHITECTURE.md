# Cortex Architecture

Updated: 2026-06-23

Cortex is now local-first for live TRIBE inference, Cloudflare-first for public
hosting, and git-first for editing/review. The public website should keep
working when the RTX 5090 desktop is asleep; live scans require either the
desktop or an explicitly configured cloud TRIBE worker.

## Current Hosting Shape

```text
GitHub / local git repo
  |
  +-- website/              -> redteamkitchen.com
  |                           Durable marketing, project pages, public copy.
  |                           Cloudflare Pages is the intended host.
  |
  +-- pages-cortex/         -> cortex.redteamkitchen.com static shell
  |                           Static dashboard/kiosk that can load while the
  |                           PC is offline.
  |
  +-- workers/cortex-api/   -> cortex.redteamkitchen.com/api/*
  |                           Cloudflare Worker API relay. Useful for a
  |                           Cloudflare-owned API edge route; it should point
  |                           at the current live inference origin when used.
  |
  +-- webapp/               -> local/live Cortex FastAPI app
  |                           Runs on Seratonin at http://127.0.0.1:8765.
  |                           May be exposed through Cloudflare Tunnel while
  |                           the PC is online.
  |
  +-- cloud/tribe_worker/   -> paid HTTP cloud GPU worker contract
  |                           FastAPI worker for Modal, RunPod, Docker Spaces,
  |                           Hugging Face Endpoints, or another GPU host.
  |
  +-- cloud/huggingface_space/
                              Gradio/ZeroGPU experiment adapter. Good for
                              no-cost experiments, not the direct FastAPI API
                              contract used by the webapp.
```

## Runtime Request Flow

```text
Visitor
  |
  | browse durable pages
  v
Cloudflare Pages
  - redteamkitchen.com from website/
  - cortex.redteamkitchen.com static shell from pages-cortex/

Visitor wants a live scan
  |
  v
webapp/public/index.html
  |
  +-- compute_target=local
  |     |
  |     v
  |   Seratonin FastAPI (:8765)
  |     -> GPU scheduler
  |     -> TRIBE v2 on RTX 5090
  |     -> per-vertex BOLD saved under scans/
  |     -> persona narration through selected local/OpenRouter model
  |
  +-- compute_target=cloud_hf | cloud_modal | cloud_runpod
        |
        | requires funded access code
        v
      Seratonin FastAPI (:8765)
        -> proxies upload/text to CORTEX_CLOUD_TRIBE_ENDPOINT
        -> cloud worker runs TRIBE and returns BOLD/top ROI fields
        -> Seratonin hydrates the result and generates persona narrations
        -> browser reads BOLD/source media through Seratonin proxy endpoints
```

The cloud worker does not own persona policy. It is intentionally limited to
TRIBE-style BOLD inference plus media/BOLD retrieval. The main webapp owns the
model selector, free/paid OpenRouter gating, fallback narration, and result
panel semantics.

## Public Availability Rules

| Surface | Should work if Seratonin is offline? | Owner | Notes |
| --- | --- | --- | --- |
| `redteamkitchen.com` | Yes | `website/` on Cloudflare Pages | Marketing/project copy, GitHub links, exported media. |
| `redteamkitchen.com/cortex` | Yes | `website/cortex.html` | Honest public project page. Live scans are not promised. |
| `cortex.redteamkitchen.com` static shell | Yes | `pages-cortex/` on Cloudflare Pages | Can show status, gallery exports, and instructions. |
| `cortex.redteamkitchen.com/api/*` | Maybe | Worker or Tunnel | Requires a live origin or configured cloud worker. |
| `http://127.0.0.1:8765` | No | `webapp/server.py` | Local lab app and canonical live development target. |

Public copy should say: live scans require Seratonin's RTX 5090 or a configured
cloud TRIBE worker. The old Tailscale Funnel routes are retired.

## Compute Modes

| Mode | Trigger | Cost | What Runs | Best Use |
| --- | --- | --- | --- | --- |
| Local RTX 5090 | `compute_target=local` | No cloud GPU spend | TRIBE v2 on Seratonin; narration via OpenRouter/free or local model | Default development and demos while the PC is available. |
| Hugging Face ZeroGPU experiment | `cloud/huggingface_space` manual/Gradio flow | Shared free quota | Gradio adapter around TRIBE contract | Test whether TRIBE fits ZeroGPU limits without building production API assumptions on it. |
| Paid cloud worker | `compute_target=cloud_hf`, `cloud_modal`, or `cloud_runpod` | Provider usage | `cloud/tribe_worker/app.py` in real mode | Public demos when the PC is unavailable or busy. |
| OpenRouter free narration | default free model IDs ending in `:free` | Free request quota | Persona text only | Public default for explanation text. |
| OpenRouter paid narration | paid model selection after access code | Token-priced | Persona text only | Funded, higher-quality narration experiments. |

Paid spend paths require the simple funded-access code configured by
`CORTEX_PAID_ACCESS_CODE` (default local dev value: `boileruphammerdown`). The UI
locks paid OpenRouter cards and cloud GPU targets until that code is entered.

## Environment Contract

Main webapp:

```text
OPENROUTER_API_KEY              optional; enables OpenRouter narration
CORTEX_OPENROUTER_ENV_PATH      optional path to an env file holding the key
CORTEX_PAID_ACCESS_CODE         optional override for funded spend unlock
CORTEX_CLOUD_TRIBE_ENDPOINT     optional cloud worker base URL
CORTEX_CLOUD_TRIBE_PROVIDER     label shown in diagnostics/UI
CORTEX_CLOUD_TRIBE_TOKEN        optional bearer token for worker calls
```

Cloud worker:

```text
CORTEX_WORKER_MODE=real         real TRIBE inference; fake is contract smoke mode
CORTEX_WORKER_PROVIDER          provider label
CORTEX_WORKER_TOKEN             bearer token expected from the main app
CORTEX_WORKER_ROOT              writable root for uploads/scans
HF_TOKEN                        required when the image downloads gated TRIBE weights
```

Never commit `.env` files or API keys. Runtime secrets belong in the local
process environment, Cloudflare secrets, or the selected cloud GPU provider's
secret store.

## Data And Media

Local live app:

- scan registry: `scans/registry.sqlite`
- per-vertex BOLD: `scans/{scan_id}.npy`
- generated ASCII/Manim videos: `scans/ascii/`, `scans/manim/`
- uploads: `uploads/`

Durable public site:

- should publish selected thumbnails, recordings, static gallery metadata, and
  GitHub links through Cloudflare Pages/R2 or another Cloudflare-owned static
  store;
- should not depend on the local SQLite registry being online.

Cloud worker:

- keeps its own temporary uploads and BOLD files;
- exposes `/api/scan/{id}/bold-vertex`, `/bold-simulate`, and `/source-media`;
- the main app proxies those media endpoints back to the browser for proxied
  scans.

## Deployment Checklist

1. Make edits in git and commit each verified slice.
2. Run backend checks:

   ```powershell
   python -m pytest tests/integration/test_webapp.py -q
   ```

3. Run browser checks against a temporary local `uvicorn`:

   ```powershell
   cd webapp
   npm run test:funded --silent
   npm run test:capture --silent
   ```

4. Build static web assets:

   ```powershell
   cd webapp
   npm run build --silent
   ```

5. Deploy `website/` and/or `pages-cortex/` to Cloudflare Pages.
6. If live scans are needed, start Seratonin's `webapp.server:app` and expose it
   through the current Cloudflare-controlled route.
7. If Seratonin cannot be available, configure `CORTEX_CLOUD_TRIBE_ENDPOINT`
   against a readiness-checked cloud worker before enabling cloud scan demos.

## Current Verification

The latest verified slice proves:

- camera capture and voice recording submit end to end in the browser test;
- funded access unlocks paid OpenRouter selection and cloud GPU target selection;
- cloud upload and text scans proxy to a worker and hydrate back into the main
  scan registry;
- completed cloud TRIBE results receive four persona narrations in the main app;
- the result panel uses ROI bars, a 3D BOLD ribbon, and a ranked network summary
  instead of the old polar placeholder;
- retired laptop routing strings are absent from the current tree.
