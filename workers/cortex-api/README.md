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

## DNS swap (Phase 1 cutover)

Currently `cortex.redteamkitchen.com` is a CNAME to `ghs.googlehosted.com`
(Cloud Run cortex-relay). After the Pages project is created and the custom
domain attached, the record becomes a CNAME to `cortex-site.pages.dev`,
proxied=true. The Worker route on `/api/*` then takes precedence over Pages
asset routing.
