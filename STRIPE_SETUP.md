# Stripe payments — Red Team Kitchen setup runbook

End-to-end recipe to take the rails in `pages-site/` and `workers/cortex-payments/`
from "scaffolded" to "accepting live payments". Every step is copy-paste-ready
with full paths. Owner: Soumit Lahiri (Alexios Bluff Mara LLC, dba Red Team Kitchen).

---

## 0. Prereqs

- Stripe account is **not** yet created. The LLC's EIN, business address, and
  business debit card must be ready before step 1.
- Cloudflare account is set up (`redteamkitchen.com` zone live, Pages project
  `redteamkitchen` deployed).
- `wrangler` is logged in (`wrangler login`).
- Node 20+ on PATH.

---

## 1. Create the Stripe account (one-time, manual)

1. Go to <https://dashboard.stripe.com/register> and sign up with
   `soumitlahiri@philanthropytraders.com`.
2. Choose **Business → Single-member LLC**.
3. Enter:
   - Legal business name: **Alexios Bluff Mara LLC**
   - DBA: **Red Team Kitchen**
   - EIN: *(LLC's EIN from the IRS letter)*
   - Address: LLC's registered address (Bizee filing)
   - Industry: **Software / SaaS**
4. Connect a bank account (LLC business checking) — this is where payouts land.
5. Add the LLC business debit card as the chargeback fallback method.
6. Verify identity (Persona flow). Activation typically completes same-day.
7. While waiting, you can use **test mode** for everything below.

After activation, in the dashboard:
- **Settings → Public details** → set support email, support URL `https://redteamkitchen.com`, statement descriptor `RTK*RTK` (max 22 chars).
- **Settings → Branding** → upload the RTK favicon as the icon.
- **Settings → Customer portal** → enable, allow cancellation + plan switching.

---

## 2. Get API keys

In the Stripe dashboard top-right toggle, switch **Test mode** ON first.

**Test keys** (start here):

```
Developers -> API keys
  Publishable: pk_test_...
  Secret:      sk_test_...
```

Switch to **Live mode** after activation:

```
  Publishable: pk_live_...
  Secret:      sk_live_...
```

We never paste these into code — they go into Worker secrets (step 6).

---

## 3. Install Stripe CLI (Windows)

```powershell
winget install Stripe.StripeCli
```

After install, restart the shell (winget puts new bins on PATH only after
restart). Then in git-bash:

```bash
"/c/Program Files/Stripe/stripe.exe" --version
"/c/Program Files/Stripe/stripe.exe" login   # opens browser for OAuth
```

If `stripe` is on PATH after restart, drop the full path.

---

## 4. Create products + prices via CLI

Run with **test mode** keys first. Re-run later with live mode (the CLI uses
the active mode from `~/.config/stripe/config.toml`).

```bash
# Hobbyist  $10/mo
stripe products create \
  --name "Ascended Base — Hobbyist" \
  --description "100 inference calls / month on the Red Team Kitchen home rig (RTX 5090)."

# capture the product id (prod_...) and pipe it in:
stripe prices create \
  --product prod_HOBBYIST_FROM_ABOVE \
  --unit-amount 1000 \
  --currency usd \
  --recurring "interval=month"

# Builder  $50/mo
stripe products create \
  --name "Ascended Base — Builder" \
  --description "1,000 inference calls / month on the Red Team Kitchen home rig."
stripe prices create \
  --product prod_BUILDER_FROM_ABOVE \
  --unit-amount 5000 \
  --currency usd \
  --recurring "interval=month"

# Pro  $200/mo
stripe products create \
  --name "Ascended Base — Pro" \
  --description "Unlimited fair-use inference + priority queue on the home rig."
stripe prices create \
  --product prod_PRO_FROM_ABOVE \
  --unit-amount 20000 \
  --currency usd \
  --recurring "interval=month"
```

Donations don't need a pre-created product — the Worker creates an inline
`price_data` per session.

Save the three `price_..` IDs — they go into Worker secrets in step 6.

---

## 5. Create the KV namespace + deploy the Worker (staging first)

```bash
cd /d/cortex/workers/cortex-payments

# Install deps
npm install

# Create staging KV namespace
wrangler kv:namespace create rtk-customers --env=staging
# -> copy the returned id into wrangler.toml under [env.staging] kv_namespaces.id

# Type-check
npm run typecheck

# Deploy staging (uses Stripe TEST keys)
npm run deploy:staging
```

Set staging secrets (Stripe test mode):

```bash
wrangler secret put STRIPE_SECRET_KEY        --env=staging   # paste sk_test_...
wrangler secret put STRIPE_WEBHOOK_SECRET    --env=staging   # placeholder, fill in step 7
wrangler secret put STRIPE_PRICE_ID_HOBBYIST --env=staging   # paste price_test_... for $10/mo
wrangler secret put STRIPE_PRICE_ID_BUILDER  --env=staging
wrangler secret put STRIPE_PRICE_ID_PRO      --env=staging
wrangler secret put TURNSTILE_SECRET_KEY     --env=staging   # from Cloudflare Turnstile widget
```

---

## 6. Create the production KV + secrets, deploy

```bash
cd /d/cortex/workers/cortex-payments

# Create production KV namespace
wrangler kv:namespace create rtk-customers --env=production
# -> paste the id into [env.production] kv_namespaces.id in wrangler.toml

# Set production secrets (Stripe TEST keys until activation; switch to LIVE when ready)
wrangler secret put STRIPE_SECRET_KEY        --env=production
wrangler secret put STRIPE_WEBHOOK_SECRET    --env=production   # placeholder, real one in step 7
wrangler secret put STRIPE_PRICE_ID_HOBBYIST --env=production
wrangler secret put STRIPE_PRICE_ID_BUILDER  --env=production
wrangler secret put STRIPE_PRICE_ID_PRO      --env=production
wrangler secret put TURNSTILE_SECRET_KEY     --env=production

# Deploy
wrangler deploy --env=production
```

The Worker is now live at:
- `https://redteamkitchen.com/api/checkout/donate`
- `https://redteamkitchen.com/api/checkout/subscribe`
- `https://redteamkitchen.com/api/webhook/stripe`
- `https://redteamkitchen.com/api/me`

(The route patterns in `wrangler.toml` mount it at `/api/*` on the apex zone,
which preempts Pages for those paths.)

---

## 7. Register the Stripe webhook + capture signing secret

Stripe dashboard → **Developers → Webhooks → Add endpoint**:

- Endpoint URL: `https://redteamkitchen.com/api/webhook/stripe`
- Events to listen for:
  - `checkout.session.completed`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.paid`
  - `invoice.payment_failed`

After creating, click "Reveal signing secret" → copy `whsec_...` and update
the Worker:

```bash
wrangler secret put STRIPE_WEBHOOK_SECRET --env=production
# paste whsec_...
```

For local development you can pipe events through the CLI instead:

```bash
stripe listen --forward-to https://redteamkitchen.com/api/webhook/stripe
# CLI prints a different whsec_... — set that as the staging secret
```

---

## 8. Cloudflare Turnstile widget

Cloudflare dashboard → **Turnstile → Add site**:

- Site name: `redteamkitchen.com`
- Domains: `redteamkitchen.com`, `www.redteamkitchen.com`
- Widget mode: **Managed**

Copy the **site key** into `D:\cortex\pages-site\app.js`
(`TURNSTILE_SITEKEY = '0x4AAAAAAA…'`).

Copy the **secret key** into the Worker:
```bash
wrangler secret put TURNSTILE_SECRET_KEY --env=production
```

---

## 9. Deploy the Pages site

The new pages live at `D:\cortex\pages-site\` (donate.html, access.html,
account.html, app.js). Either copy them into the existing `D:\cortex\website\`
output dir, or update Cloudflare Pages to point at `pages-site/`.

```bash
cd /d/cortex
wrangler pages deploy ./pages-site --project-name=redteamkitchen --branch=main
```

(Or use Pages Git integration — drop these files in whatever repo backs the
Pages project.)

---

## 10. End-to-end test (test mode)

1. Visit `https://redteamkitchen.com/donate.html`.
2. Pick $50, complete Turnstile, click "Donate with card".
3. On the Stripe Checkout page use test card:
   - **Card**: `4242 4242 4242 4242`
   - **Expiry**: `12 / 34`
   - **CVC**: `123`
   - **ZIP**: any
4. Confirm you land on `/donate.html?status=ok&session=cs_test_...`.
5. In the Stripe dashboard → Payments, see a $50 succeeded payment.

Repeat with `https://redteamkitchen.com/access.html` → "Subscribe — $50/mo".
After completing checkout the webhook should populate KV — verify with:

```bash
wrangler kv:key list --env=production --binding=RTK_CUSTOMERS | head
```

You should see a `cust:cus_...`, an `email:...`, and a `tok:rtk_live_...` key.

Trigger every webhook type via the CLI to smoke-test:

```bash
stripe trigger checkout.session.completed
stripe trigger customer.subscription.updated
stripe trigger invoice.paid
stripe trigger invoice.payment_failed
stripe trigger customer.subscription.deleted
```

Watch the Worker logs:

```bash
wrangler tail --env=production
```

---

## 11. Switch to live mode

Once the LLC's Stripe account is activated:

1. In the Stripe dashboard, toggle to **Live mode**.
2. Re-run step 4 (create products + prices) under live mode — you'll get
   distinct `price_live_...` IDs.
3. Re-create the webhook endpoint under live mode (Step 7) — the signing
   secret is different from test mode.
4. Update production secrets:
   ```bash
   wrangler secret put STRIPE_SECRET_KEY        --env=production   # sk_live_...
   wrangler secret put STRIPE_WEBHOOK_SECRET    --env=production   # whsec_... (live)
   wrangler secret put STRIPE_PRICE_ID_HOBBYIST --env=production   # price_live_...
   wrangler secret put STRIPE_PRICE_ID_BUILDER  --env=production
   wrangler secret put STRIPE_PRICE_ID_PRO      --env=production
   ```
5. Redeploy: `wrangler deploy --env=production`.
6. First real payment: donate $5 to yourself with a personal card; verify
   payout to LLC checking on next Stripe payout schedule (default T+2 in US).

---

## 12. After-launch hygiene

- Add Stripe Radar rules (default settings are fine for low volume).
- Set Stripe payout schedule: **Settings → Payouts → Daily** (small biz friendly).
- Configure tax: **Settings → Tax → Stripe Tax** if you sell across states. For
  the Hobbyist/Builder/Pro tiers (digital services), confirm Illinois nexus rules
  with your CPA before enabling automated tax collection.
- Forward the receipt email to `lahirisoumit@gmail.com` as a backup.

---

## File map

| File | Purpose |
|---|---|
| `D:\cortex\pages-site\donate.html` | Donation page (Checkout, mode=payment) |
| `D:\cortex\pages-site\access.html` | Subscription tier picker (Checkout, mode=subscription) |
| `D:\cortex\pages-site\account.html` | User dashboard — paste token, see plan + usage |
| `D:\cortex\pages-site\app.js` | Stripe Checkout glue + Turnstile + token mgmt |
| `D:\cortex\workers\cortex-payments\src\index.ts` | Worker — Checkout, webhook, /me |
| `D:\cortex\workers\cortex-payments\wrangler.toml` | Worker config (routes, KV, env) |
| `D:\cortex\workers\cortex-payments\package.json` | Deps: `stripe`, `@cloudflare/workers-types` |
| `D:\cortex\workers\cortex-payments\tsconfig.json` | TypeScript strict config |
| `D:\cortex\STRIPE_SETUP.md` | This file |
