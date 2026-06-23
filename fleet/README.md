# Ascended Base - Fleet Control Scripts

This folder now describes the Seratonin-local Cortex stack only. The retired
laptop peer has been removed from startup, watchdog, failover, and status
scripts.

## Current Runtime

| Node | OS | Role |
| --- | --- | --- |
| Seratonin | Windows 11 / WSL2 | RTX 5090 host for Cortex, TRIBE v2, local Ollama, Mercury, and the web UI |
| Cloud TRIBE worker | configured by env | Optional funded fallback for TRIBE inference when deployed |
| OpenRouter | external API | Narration fallback and selectable model catalog |

## Start / Stop / Status

Run from Seratonin:

```bash
pwsh fleet/up-seratonin.ps1
pwsh fleet/down-seratonin.ps1
bash fleet/status.sh
```

The current app path is FastAPI on `:8765`. Older ports may still appear in
some diagnostics while we finish consolidating, but no script should route
traffic to a peer laptop.

## Required Environment

- `OPENROUTER_API_KEY` for narration model access.
- `CORTEX_CLOUD_TRIBE_ENDPOINT` only when a cloud TRIBE worker is deployed.
- `CORTEX_CLOUD_TRIBE_TOKEN` when that worker requires bearer auth.
- Local Ollama at `http://localhost:11434` for local narration.

## Deployment Direction

Use git as the source of truth, Cloudflare for the public surface, Seratonin
for local TRIBE, and a cloud worker as the explicit paid fallback. Any future
extra worker should be added through `OLLAMA_BACKENDS` or cloud-worker env
vars by name, not by resurrecting machine-specific failover scripts.
