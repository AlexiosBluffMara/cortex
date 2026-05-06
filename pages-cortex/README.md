# cortex-site (Cloudflare Pages)

Static landing + kiosk for `cortex.redteamkitchen.com`. Replaces the Cloud Run
`cortex-relay` service. Dynamic `/api/*` routes are served by the
`cortex-api` Worker (see `D:/cortex/workers/cortex-api/`).

## Deploy

```bash
cd D:/cortex/pages-cortex
wrangler pages deploy . --project-name=cortex-site --branch=main --commit-dirty=true
```

Requires a Cloudflare API token with **Cloudflare Pages:Edit**. The current
read-only token at `~/.cloudflare/credentials` will return 403 on
`wrangler pages project create cortex-site`. Two options:

1. **Upgrade the token** at https://dash.cloudflare.com/profile/api-tokens
   (add `Pages:Edit`, `Workers Scripts:Edit`, `Workers Routes:Edit`,
   `Zone DNS:Edit`).
2. **Dashboard fallback** - https://dash.cloudflare.com/?to=/:account/pages/new
   -> "Direct upload" -> drag this folder.

## Custom domain

After first deploy, in the Pages project settings -> Custom domains, add
`cortex.redteamkitchen.com`. Cloudflare auto-provisions the CNAME if the
zone is on the same account. If not, manually:

```bash
ZONE=$(curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=redteamkitchen.com" \
  | jq -r '.result[0].id')

# Find existing record (currently CNAME to ghs.googlehosted.com):
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records?name=cortex.redteamkitchen.com" | jq

# Update it:
RECORD=<id-from-above>
curl -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records/$RECORD" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type":"CNAME","name":"cortex","content":"cortex-site.pages.dev","proxied":true}'
```

## Smoke tests (PowerShell, post-cutover)

```powershell
Invoke-WebRequest https://cortex.redteamkitchen.com/                    | % StatusCode  # 200
Invoke-WebRequest https://cortex.redteamkitchen.com/dashboard.html      | % StatusCode  # 200
Invoke-WebRequest https://cortex.redteamkitchen.com/api/healthz         | % StatusCode  # 200
Invoke-WebRequest https://cortex.redteamkitchen.com/api/tags            | % StatusCode  # 200
Invoke-WebRequest https://cortex.redteamkitchen.com/assets/style.css    | % StatusCode  # 200
Invoke-WebRequest https://cortex.redteamkitchen.com/nope-404            | % StatusCode  # 404
```

## Cloud Run teardown (DO NOT EXECUTE until Pages site verified for 24h)

```bash
gcloud run services delete cortex-relay  --region=us-central1 --project=abm-isu --quiet
gcloud run services delete cortex-webapp --region=us-central1 --project=abm-isu --quiet
```
