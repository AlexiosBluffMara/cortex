# Cortex Live Ops

Updated: 2026-06-23

Use this when preparing `https://cortex.redteamkitchen.com` for a live demo from
Seratonin.

## Architecture

- Public route: Cloudflare Tunnel `cortex.redteamkitchen.com`
- Origin: Seratonin `http://localhost:8765`
- App: `D:\cortex\webapp\server.py`
- UI assets: `D:\cortex\webapp\public`
- GPU work: TRIBE v2 on the local RTX 5090
- Optional cloud GPU: `CORTEX_CLOUD_TRIBE_ENDPOINT` using
  `cloud/tribe_worker/app.py`
- Cloud narration: OpenRouter chat completions
- Model selector: live OpenRouter `/api/v1/models` free-model catalog, cached for 30 minutes
- GPU residency: scans using OpenRouter/Gemini narration keep TRIBE loaded after
  inference; local Gemma narration is the only path that warms Gemma again.

The public site at `redteamkitchen.com` is Cloudflare Pages and should remain
useful when Seratonin is offline. The Cortex tunnel is lab mode. If Seratonin is
not available for a live demo, configure a cloud TRIBE worker and use the funded
cloud compute target instead of promising the local PC will be online.

## Start Or Restart Cortex

```powershell
cd D:\cortex
$old = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pid in $old) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue }

Start-Process `
  -FilePath 'C:\Users\soumi\cortex\.venv\Scripts\python.exe' `
  -ArgumentList @('-m','uvicorn','webapp.server:app','--host','0.0.0.0','--port','8765') `
  -WorkingDirectory 'D:\cortex' `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'D:\cortex\logs\webapp_live.log' `
  -RedirectStandardError 'D:\cortex\logs\webapp_live.err.log'
```

## OpenRouter Key

Cortex checks OpenRouter credentials in this order:

1. `OPENROUTER_API_KEY` process environment variable
2. `CORTEX_OPENROUTER_ENV_PATH` or `OPENROUTER_ENV_PATH`
3. `D:\cortex\.env`
4. `~/.hermes/.env`

The file format is:

```text
OPENROUTER_API_KEY=sk-or-v1-...
```

Do not commit `.env` files. They are ignored by Git.

Validate without spending completion credits:

```powershell
Invoke-RestMethod https://cortex.redteamkitchen.com/api/openrouter/status |
  ConvertTo-Json -Depth 6
```

Ready state:

```json
{ "ok": true, "status": "ready" }
```

If the status is `invalid_key`, replace the key and restart the FastAPI process.
As of this runbook update, the only real local candidate in `~/.hermes/.env`
returned `401 User not found`.

## TRIBE Readiness

Check readiness:

```powershell
Invoke-RestMethod https://cortex.redteamkitchen.com/api/tribe/status |
  ConvertTo-Json -Depth 6
```

Warm TRIBE when the GPU is free:

```powershell
Invoke-RestMethod -Method Post https://cortex.redteamkitchen.com/api/tribe/warm |
  ConvertTo-Json -Depth 6
```

Ready state:

```json
{
  "pc_online": true,
  "tribe_loaded": true,
  "tribe_ready": true,
  "gpu": { "state": "tribe_active" }
}
```

## Cloud TRIBE Worker

Set these on the main FastAPI process before starting Cortex:

```powershell
$env:CORTEX_CLOUD_TRIBE_ENDPOINT = "https://your-worker.example"
$env:CORTEX_CLOUD_TRIBE_PROVIDER = "modal" # or huggingface, runpod, etc.
$env:CORTEX_CLOUD_TRIBE_TOKEN = "shared-worker-secret"
```

Set the matching secret on the worker as `CORTEX_WORKER_TOKEN`. For a real GPU
worker, also set:

```text
CORTEX_WORKER_MODE=real
```

Before allowing funded cloud scans, verify:

```powershell
Invoke-RestMethod "$env:CORTEX_CLOUD_TRIBE_ENDPOINT/api/tribe/readiness" |
  ConvertTo-Json -Depth 6
```

Required state:

```json
{
  "contract_ready": true,
  "real_mode_ready": true
}
```

In fake mode, `contract_ready=true` is enough for smoke tests only. It proves the
HTTP shape works, not that TRIBE weights and CUDA are available.

Then run the end-to-end worker contract verifier:

```powershell
python -m cloud.tribe_worker.verify `
  --endpoint $env:CORTEX_CLOUD_TRIBE_ENDPOINT `
  --token $env:CORTEX_CLOUD_TRIBE_TOKEN `
  --require-real
```

This submits a tiny stimulus to the worker, waits for completion, downloads the
full per-vertex BOLD response, validates the `(T, 20484)` byte shape, and checks
that source media can be served back to the browser.

## Pre-Demo Checklist

1. `https://cortex.redteamkitchen.com/api/health` returns `ok: true`.
2. `GET /api/narration-models` returns `catalog_source: openrouter_live`.
3. `GET /api/openrouter/status` returns `status: ready`.
4. `POST /api/tribe/warm` returns `status: tribe_ready`.
5. If using cloud compute, `GET /api/compute-options` shows
   `endpoint_configured: true` and worker readiness passes.
6. `GET /gallery.html` shows brain canvases, not placeholder videos.
7. Upload, typed text, camera capture, and voice recording paths submit through
   the main UI.
