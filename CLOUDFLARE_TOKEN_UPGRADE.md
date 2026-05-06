# Cloudflare API Token Upgrade

Get from a read-only token to a write-scoped, rotation-managed token usable by `wrangler` and `cloudflared`.

Current state:
- Token on disk: `~/.cloudflare/credentials` (env-style, sourced by `~/.bashrc`).
- Existing token value: `<redacted — stored in ~/.cloudflare/credentials>` (account-scoped, read-only).
- Account: `abdd39b91455684cd6ac0e47e8bdb0cc`. Zone: `redteamkitchen.com` (`852bfc5b2fea2a11bf030fd97d034ad0`).

---

## 1. Dashboard steps

1. Open https://dash.cloudflare.com/profile/api-tokens.
2. Locate the existing token (likely named `claude-cloudflare-specialist`). Three options:
   - **A. Edit** — click the token name, then "Edit", and append the missing permissions. Same secret value is reused.
   - **B. Roll** — click the row's "..." menu, then "Roll". Same scopes, new secret value. Old value invalidates within ~60s.
   - **C. Revoke + Create fresh** — click "..." then "Delete", then "Create Token" → "Custom token". Cleanest audit trail, no leftover scope drift.
3. **Recommended: option C.** Click "Create Token" → at the bottom, "Custom token" → "Get started". Name it `rtk-write-2026-05`. Fill in the permissions table below, then click "Continue to summary" → "Create Token". Copy the displayed value once — it is shown only this time.

---

## 2. Permissions to add

Match each row exactly to a checkbox in the "Permissions" section of the Custom Token form. The dashboard groups by category in the first dropdown (Account / Zone / User), then resource in the second, then permission in the third. Add one row per click of "+ Add more".

| Category | Resource / Group              | Permission |
|----------|-------------------------------|------------|
| Account  | Cloudflare Tunnel             | Edit       |
| Account  | Pages                         | Edit       |
| Account  | Workers Scripts               | Edit       |
| Account  | Workers KV Storage            | Edit       |
| Account  | Workers R2 Storage            | Edit       |
| Account  | Workers AI                    | Edit       |
| Account  | D1                            | Edit       |
| Account  | Queues                        | Edit       |
| Account  | AI Gateway                    | Edit       |
| Account  | Vectorize                     | Edit       |
| Account  | Access: Apps and Policies     | Edit       |
| Account  | Access: Service Tokens        | Edit       |
| Account  | Email Routing Addresses       | Edit       |
| Account  | Account Settings              | Edit       |
| Account  | Account Analytics             | Read       |
| Account  | Logs                          | Read       |
| Zone     | DNS                           | Edit       |
| Zone     | Zone                          | Edit       |
| Zone     | Zone Settings                 | Edit       |
| Zone     | Page Rules                    | Edit       |
| Zone     | Cache Purge                   | Purge      |
| Zone     | Zone WAF                      | Edit       |
| Zone     | Bot Management                | Edit       |
| Zone     | Email Routing Rules           | Edit       |
| Zone     | Turnstile                     | Edit       |
| Zone     | Analytics                     | Read       |
| User     | User Details                  | Read       |

**Account Resources:** Include → Specific account → `Soumit Lahiri's Account` (`abdd39b91455684cd6ac0e47e8bdb0cc`).

**Zone Resources:** Include → Specific zone → `redteamkitchen.com` (`852bfc5b2fea2a11bf030fd97d034ad0`).

**Client IP Address Filtering:** leave empty.

**TTL:** leave start/end blank (no auto-expiry). Set a calendar reminder to roll every 6 months.

---

## 3. Save the new token

```bash
nano ~/.cloudflare/credentials
# Replace the CLOUDFLARE_API_TOKEN line with the new value
chmod 600 ~/.cloudflare/credentials
. ~/.cloudflare/credentials
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/tokens/verify" | python -m json.tool
```

A successful verify returns `"status": "active"` in the result block.

---

## 4. Smoke-test the new permissions

Three calls that previously failed under the read-only token and should now succeed.

**Create the AI Gateway:**

```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/ai-gateway/gateways" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"id":"primary","cache_ttl":3600,"collect_logs":true}' | python -m json.tool
```

**Edit a DNS record's comment (no-op write):**

```bash
RECORD=$(curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$CLOUDFLARE_ZONE_ID_REDTEAMKITCHEN/dns_records?name=redteamkitchen.com&type=CNAME" \
  | python -c "import json,sys; print(json.load(sys.stdin)['result'][0]['id'])")
curl -s -X PATCH "https://api.cloudflare.com/client/v4/zones/$CLOUDFLARE_ZONE_ID_REDTEAMKITCHEN/dns_records/$RECORD" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"comment":"Pages apex — verified write at <date>"}' | python -m json.tool
```

**List Workers (200 even if empty):**

```bash
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts" | python -m json.tool | head -10
```

---

## 5. Wrangler setup

Wrangler uses its own OAuth session, separate from the API token.

```bash
npm install -g wrangler
wrangler login           # opens browser, sign in with the GCP-linked Cloudflare account
wrangler whoami          # confirms login
```

Wrangler will work even if the API token is read-only. The API token only matters for CI / unattended workflows (GitHub Actions, scheduled scripts, `cloudflared` config pushes).

---

## 6. Rotation policy

Set a calendar reminder for **2026-11-01** (6 months from issue) to rotate. Procedure: dashboard → token row "..." → Roll → paste new value into `~/.cloudflare/credentials` → `. ~/.cloudflare/credentials`. The old value invalidates within ~60 seconds of clicking Roll. Never commit `~/.cloudflare/credentials` to a repo — confirm `.gitignore` covers `.cloudflare/` and `*credentials*` before any `git add -A`.

---

## 7. Update the cloudflare skill memory

After the upgrade lands and the smoke tests pass, edit `~/.claude/skills/cloudflare/SKILL.md` and replace the "READ THIS FIRST" read-only warning block with a single line:

```
Token rotated 2026-05-01 (rtk-write-2026-05); next rotation 2026-11-01.
```
