# Cortex — URL map (2026-05-04)

> All `redteamkitchen.com` URLs are also reachable as `cortex.redteamkitchen.com` via the Cloudflare Tunnel. Use the clean alias in tweets / docs; use the Tailscale URL for ironclad uptime when CF caching might bite.

## Public web

| URL | What it is | Who it's for |
|---|---|---|
| **https://cortex.redteamkitchen.com** | Live demo — 3D cortical brain + drag-and-drop upload. Submit any video/audio/image/PDF/text and watch TRIBE v2 predict the BOLD response, then 4 personas (student / patient / clinician / ML scientist) narrate it in their own voice. | First-time visitors. The default landing page. |
| **https://cortex.redteamkitchen.com/gallery.html** | Public gallery of every completed scan — thumbnails, top regions, peak time, persona narrations, downloadable `.npy` arrays. | Anyone curious what's been scanned. Click any card to load it into the 3D viewer. |
| **https://cortex.redteamkitchen.com/personas.html** | Explanation of each of the 4 personas — who they are, what institution they're anchored to, what register they write in, why the 4-voice pattern was chosen. | People who want to understand WHY there are 4 narrations and how they differ. |
| **https://cortex.redteamkitchen.com/specs.html** | Technical specs — TRIBE v2 architecture (V-JEPA2 + wav2vec-BERT 2.0 + Llama-3.2-3B encoders), 20,484-vertex fsaverage5 output @ 2 Hz, hardware mix, model affinity, cost per scan. | Engineers, academics, anyone vetting the stack. |
| **https://cortex.redteamkitchen.com/status** | Live fleet dashboard — both nodes, all services, recent scans, watchdog state. Polls `/api/fleet-health` every 2 s, scans every 5 s. | Operators. Live diagnostic. |
| **https://redteamkitchen.com** | Marketing landing for Red Team Kitchen / Alexios Bluff Mara LLC. The umbrella above Cortex. | Anyone arriving from a non-Cortex link. |

## Public API

| URL | Returns | Use |
|---|---|---|
| `/api/health` | GPU state, queue depth, version | Quick liveness check |
| `/api/fleet-health` | Both nodes + router + Ollama + OpenRouter, all in one JSON | Status page, watchdog, monitoring |
| `/api/scans?limit=N&status=all` | Most recent N scans across the fleet | Gallery feed |
| `/api/scan/{id}` | Full scan record incl. narrations, ROIs, timings | Direct scan link |
| `/api/scan` (POST multipart) | Submit a new scan — `file` + `tier` (0–6) + `source` | Programmatic submit |

## Source + ops

| URL | What it is |
|---|---|
| **https://github.com/AlexiosBluffMara/cortex** | Full source. MIT-style. Includes the orchestrator, edit-pipeline, watchdog, four `.claude/skills/*` you can drop into any Claude Code install (video-editing, screen-recording, short-form-content, livestreaming). |

## Discord

| Channel | What it does |
|---|---|
| **`#nous-research-hermes`** | Snowy bot (`@abmsnowy`) posts every completed scan there as a 4-embed message — student / patient / clinician / ML scientist narrations + brain screenshot + gallery link. Drop a file in chat with the word "scan" (or `/scan <file>`) and the bot will run it for you. |

## Local-only (developer / ops)

| URL | Purpose |
|---|---|
| `http://100.98.19.87:8773` | Seratonin (RTX 5090) backend direct |
| `http://127.0.0.1:11434` | Seratonin Ollama direct (Tailscale only) |
| `http://localhost:8766/healthz` | Inference router on Sera |
| `http://localhost:8780/status` | Fleet watchdog status JSON |
