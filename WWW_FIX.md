# www.redteamkitchen.com 522 Fix

## Findings

- **DNS (Cloudflare API)**: `www.redteamkitchen.com` is a `CNAME` -> `redteamkitchen.com`, `proxied: true`, TTL auto. Record id `a79aede8232b8c380f3f950e5ce76c43`. Public resolution works (104.21.58.37 / 172.67.199.126).
- **Pages custom domains** on project `redteamkitchen`: only **one** entry — `redteamkitchen.com` (status `active`, cert via Google CA). `www.redteamkitchen.com` is **NOT** registered.
- **wrangler**: not installed (`wrangler: command not found`).
- **HTTP probe** to `https://www.redteamkitchen.com/`: TLS handshake completes, Cloudflare edge returns body `error code: 522` with `Server: cloudflare`, `CF-RAY: 9f51ce34adbaff5c-ORD`. So the request reaches Cloudflare but the Pages origin refuses the unknown Host header → edge logs it as a connection timeout / 522.

## Root cause

**(A)** `www.redteamkitchen.com` is not registered as a custom domain on the Pages project. Cloudflare Pages routes by Host header; an unrecognized Host on `*.pages.dev` infrastructure produces no valid backend response and the edge surfaces it as 522. The proxied CNAME `www -> redteamkitchen.com` is fine on its own — the missing piece is the Pages project binding.

## Fix (one step, requires wrangler)

```
wrangler pages domain add www.redteamkitchen.com --project-name=redteamkitchen
```

Cloudflare will auto-provision the cert (Google CA, same as apex). DNS already points the right way, so no record changes needed.

## If wrangler isn't installed

```
npm install -g wrangler
wrangler login        # one-time browser OAuth
wrangler pages domain add www.redteamkitchen.com --project-name=redteamkitchen
```

## Manual fallback (no CLI)

Open: `https://dash.cloudflare.com/16735c11ac1f72e3c6df4596499fa022/pages/view/redteamkitchen/domains`
Click **Set up a custom domain** → enter `www.redteamkitchen.com` → **Continue** → **Activate domain**. (Cloudflare will detect the existing CNAME and skip the DNS step.)

## After-fix verify probe

```
curl -sI --max-time 8 https://www.redteamkitchen.com/
```

Expect `HTTP/2 200` with `server: cloudflare` and a `cf-ray` header. If still 522, wait 30–60s for cert provisioning and retry.
