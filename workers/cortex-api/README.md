# cortex-api

Cloudflare Worker bound to `cortex.redteamkitchen.com/api/*`. Relays browser
traffic from the Pages site (`cortex-site`) to the local 5090 inference router
via the rtk-5090 Cloudflare tunnel.

## Routes

- `GET  /api/healthz`     -> `https://inference.redteamkitchen.com/healthz`
- `GET  /api/tags`        -> `https://ollama.redteamkitchen.com/api/tags`
- `GET  /api/narrations`  -> `https://inference.redteamkitchen.com/v1/narrations`
- `POST /api/scan`        -> `https://inference.redteamkitchen.com/v1/scan` (multipart, 50 MB cap)

## Deploy

```bash
cd D:/cortex/workers/cortex-api
npm install
wrangler deploy
```

Requires a Cloudflare API token with **Workers Scripts:Edit** and
**Workers Routes:Edit** on the redteamkitchen.com zone. The current
read-only token will fail with 403 on `wrangler deploy`.

## DNS shape

`cortex.redteamkitchen.com` should be Cloudflare-owned. The static shell can be
served from the `cortex-site` Pages project, while this Worker owns `/api/*`.
The Worker then relays to the live local inference route only while Seratonin is
online. If that local route is down, the static shell should still load and the
UI should report that live scans require the PC or a configured cloud TRIBE
worker.
