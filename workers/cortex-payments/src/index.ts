/**
 * cortex-payments — Cloudflare Worker
 *
 * Routes mounted under https://redteamkitchen.com/api/* (configured via
 * Pages -> Functions -> Service binding OR a Worker route in wrangler.toml).
 *
 *   POST /api/checkout/donate     -> Stripe Checkout, mode=payment, custom $
 *   POST /api/checkout/subscribe  -> Stripe Checkout, mode=subscription, by tier
 *   POST /api/webhook/stripe      -> Stripe webhook receiver (signed)
 *   GET  /api/me                  -> current customer's plan + usage (Bearer token)
 *
 * KV: rtk-customers
 *   key:  cust:<customerId>          -> Customer JSON
 *   key:  tok:<accessToken>          -> "<customerId>"   (lookup)
 *   key:  email:<lowercase email>    -> "<customerId>"
 */

import Stripe from 'stripe';

export interface Env {
  STRIPE_SECRET_KEY: string;
  STRIPE_WEBHOOK_SECRET: string;
  STRIPE_PRICE_ID_HOBBYIST: string;
  STRIPE_PRICE_ID_BUILDER: string;
  STRIPE_PRICE_ID_PRO: string;
  TURNSTILE_SECRET_KEY: string;
  PUBLIC_BASE_URL: string;            // e.g. https://redteamkitchen.com
  RTK_CUSTOMERS: KVNamespace;
}

type Tier = 'hobbyist' | 'builder' | 'pro';

interface Customer {
  customerId: string;
  email: string;
  subscriptionId?: string;
  plan: 'free' | Tier;
  status?: string;                    // active, past_due, canceled, etc.
  calls_used: number;
  calls_max: number | null;           // null = unlimited
  calls_reset_at: string;             // ISO timestamp of next reset
  accessToken?: string;
}

const PLAN_LIMITS: Record<Tier, number | null> = {
  hobbyist: 100,
  builder: 1000,
  pro: null,
};

// ---------- helpers ----------

const json = (obj: unknown, status = 200, extra: HeadersInit = {}): Response =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...extra },
  });

const corsHeaders = (origin: string): HeadersInit => ({
  'access-control-allow-origin': origin,
  'access-control-allow-methods': 'GET,POST,OPTIONS',
  'access-control-allow-headers': 'content-type,authorization',
  'access-control-max-age': '86400',
});

const stripeClient = (env: Env): Stripe =>
  new Stripe(env.STRIPE_SECRET_KEY, {
    apiVersion: '2024-06-20',
    httpClient: Stripe.createFetchHttpClient(),
  });

async function verifyTurnstile(env: Env, token: string, ip: string): Promise<boolean> {
  if (!token) return false;
  const body = new URLSearchParams({
    secret: env.TURNSTILE_SECRET_KEY,
    response: token,
    remoteip: ip,
  });
  const r = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
    method: 'POST',
    body,
  });
  const j = (await r.json()) as { success?: boolean };
  return !!j.success;
}

function tierFromPriceId(env: Env, priceId: string | null | undefined): Tier | null {
  if (!priceId) return null;
  if (priceId === env.STRIPE_PRICE_ID_HOBBYIST) return 'hobbyist';
  if (priceId === env.STRIPE_PRICE_ID_BUILDER) return 'builder';
  if (priceId === env.STRIPE_PRICE_ID_PRO) return 'pro';
  return null;
}

function priceIdForTier(env: Env, tier: Tier): string {
  return {
    hobbyist: env.STRIPE_PRICE_ID_HOBBYIST,
    builder: env.STRIPE_PRICE_ID_BUILDER,
    pro: env.STRIPE_PRICE_ID_PRO,
  }[tier];
}

function nextMonthIso(): string {
  const d = new Date();
  d.setUTCMonth(d.getUTCMonth() + 1);
  return d.toISOString();
}

function genToken(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return 'rtk_live_' + Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}

async function loadCustomer(env: Env, customerId: string): Promise<Customer | null> {
  return await env.RTK_CUSTOMERS.get<Customer>(`cust:${customerId}`, 'json');
}

async function saveCustomer(env: Env, c: Customer): Promise<void> {
  await env.RTK_CUSTOMERS.put(`cust:${c.customerId}`, JSON.stringify(c));
  if (c.email) {
    await env.RTK_CUSTOMERS.put(`email:${c.email.toLowerCase()}`, c.customerId);
  }
  if (c.accessToken) {
    await env.RTK_CUSTOMERS.put(`tok:${c.accessToken}`, c.customerId);
  }
}

async function upsertCustomerFromStripe(
  env: Env,
  stripeCustomerId: string,
  email: string,
): Promise<Customer> {
  let c = await loadCustomer(env, stripeCustomerId);
  if (!c) {
    c = {
      customerId: stripeCustomerId,
      email,
      plan: 'free',
      calls_used: 0,
      calls_max: 0,
      calls_reset_at: nextMonthIso(),
      accessToken: genToken(),
    };
  } else if (!c.accessToken) {
    c.accessToken = genToken();
  }
  if (email) c.email = email;
  return c;
}

// ---------- handlers ----------

async function handleDonate(req: Request, env: Env): Promise<Response> {
  const body = await req.json().catch(() => ({})) as { amount?: number; turnstile?: string };
  const amt = Number(body.amount);
  if (!Number.isFinite(amt) || amt < 5 || amt > 1000) {
    return json({ error: 'amount must be 5-1000 USD' }, 400);
  }
  const ip = req.headers.get('cf-connecting-ip') || '';
  if (!(await verifyTurnstile(env, body.turnstile || '', ip))) {
    return json({ error: 'turnstile failed' }, 400);
  }
  const stripe = stripeClient(env);
  const session = await stripe.checkout.sessions.create({
    mode: 'payment',
    payment_method_types: ['card'],
    line_items: [{
      price_data: {
        currency: 'usd',
        product_data: { name: 'Donation to Alexios Bluff Mara LLC (dba Red Team Kitchen)' },
        unit_amount: Math.round(amt * 100),
      },
      quantity: 1,
    }],
    submit_type: 'donate',
    success_url: `${env.PUBLIC_BASE_URL}/donate.html?status=ok&session={CHECKOUT_SESSION_ID}`,
    cancel_url: `${env.PUBLIC_BASE_URL}/donate.html?status=cancel`,
    metadata: { kind: 'donation' },
  });
  return json({ url: session.url });
}

async function handleSubscribe(req: Request, env: Env): Promise<Response> {
  const body = await req.json().catch(() => ({})) as { tier?: Tier; turnstile?: string };
  const tier = body.tier;
  if (tier !== 'hobbyist' && tier !== 'builder' && tier !== 'pro') {
    return json({ error: 'invalid tier' }, 400);
  }
  const ip = req.headers.get('cf-connecting-ip') || '';
  if (!(await verifyTurnstile(env, body.turnstile || '', ip))) {
    return json({ error: 'turnstile failed' }, 400);
  }
  const stripe = stripeClient(env);
  const session = await stripe.checkout.sessions.create({
    mode: 'subscription',
    payment_method_types: ['card'],
    line_items: [{ price: priceIdForTier(env, tier), quantity: 1 }],
    allow_promotion_codes: true,
    success_url: `${env.PUBLIC_BASE_URL}/account.html?status=ok&session={CHECKOUT_SESSION_ID}`,
    cancel_url: `${env.PUBLIC_BASE_URL}/access.html?status=cancel`,
    metadata: { kind: 'subscription', tier },
    subscription_data: { metadata: { tier } },
  });
  return json({ url: session.url });
}

async function handleWebhook(req: Request, env: Env): Promise<Response> {
  const sig = req.headers.get('stripe-signature') || '';
  const payload = await req.text();
  const stripe = stripeClient(env);
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      payload,
      sig,
      env.STRIPE_WEBHOOK_SECRET,
      undefined,
      Stripe.createSubtleCryptoProvider(),
    );
  } catch (e) {
    return json({ error: `signature verification failed: ${(e as Error).message}` }, 400);
  }

  switch (event.type) {
    case 'checkout.session.completed': {
      const s = event.data.object as Stripe.Checkout.Session;
      const customerId = typeof s.customer === 'string' ? s.customer : s.customer?.id;
      const email = s.customer_details?.email || s.customer_email || '';
      if (s.mode === 'subscription' && customerId) {
        const c = await upsertCustomerFromStripe(env, customerId, email);
        const tier = (s.metadata?.tier as Tier) || 'hobbyist';
        c.plan = tier;
        c.status = 'active';
        c.subscriptionId = typeof s.subscription === 'string' ? s.subscription : s.subscription?.id;
        c.calls_max = PLAN_LIMITS[tier];
        c.calls_used = 0;
        c.calls_reset_at = nextMonthIso();
        await saveCustomer(env, c);
      }
      break;
    }
    case 'customer.subscription.created':
    case 'customer.subscription.updated': {
      const sub = event.data.object as Stripe.Subscription;
      const customerId = typeof sub.customer === 'string' ? sub.customer : sub.customer.id;
      const item = sub.items.data[0];
      const tier = tierFromPriceId(env, item?.price.id) || (sub.metadata.tier as Tier) || 'hobbyist';
      const c = await upsertCustomerFromStripe(env, customerId, '');
      c.subscriptionId = sub.id;
      c.plan = tier;
      c.status = sub.status;
      c.calls_max = PLAN_LIMITS[tier];
      await saveCustomer(env, c);
      break;
    }
    case 'customer.subscription.deleted': {
      const sub = event.data.object as Stripe.Subscription;
      const customerId = typeof sub.customer === 'string' ? sub.customer : sub.customer.id;
      const c = await loadCustomer(env, customerId);
      if (c) {
        c.plan = 'free';
        c.status = 'canceled';
        c.calls_max = 0;
        await saveCustomer(env, c);
      }
      break;
    }
    case 'invoice.paid': {
      const inv = event.data.object as Stripe.Invoice;
      const customerId = typeof inv.customer === 'string' ? inv.customer : inv.customer?.id;
      if (!customerId) break;
      const c = await loadCustomer(env, customerId);
      if (c && c.plan !== 'free') {
        c.calls_used = 0;
        c.calls_reset_at = nextMonthIso();
        c.status = 'active';
        await saveCustomer(env, c);
      }
      break;
    }
    case 'invoice.payment_failed': {
      const inv = event.data.object as Stripe.Invoice;
      const customerId = typeof inv.customer === 'string' ? inv.customer : inv.customer?.id;
      if (!customerId) break;
      const c = await loadCustomer(env, customerId);
      if (c) { c.status = 'past_due'; await saveCustomer(env, c); }
      break;
    }
    default:
      // ignore other event types
      break;
  }
  return json({ received: true });
}

async function handleMe(req: Request, env: Env): Promise<Response> {
  const auth = req.headers.get('authorization') || '';
  const m = auth.match(/^Bearer\s+(.+)$/i);
  if (!m) return json({ error: 'missing bearer token' }, 401);
  const customerId = await env.RTK_CUSTOMERS.get(`tok:${m[1]}`);
  if (!customerId) return json({ error: 'invalid token' }, 401);
  const c = await loadCustomer(env, customerId);
  if (!c) return json({ error: 'customer not found' }, 404);

  let portal_url: string | undefined;
  try {
    const stripe = stripeClient(env);
    const portal = await stripe.billingPortal.sessions.create({
      customer: c.customerId,
      return_url: `${env.PUBLIC_BASE_URL}/account.html`,
    });
    portal_url = portal.url;
  } catch { /* portal may not be configured yet */ }

  return json({
    email: c.email,
    plan: c.plan,
    status: c.status || (c.plan === 'free' ? 'inactive' : 'unknown'),
    calls_used: c.calls_used,
    calls_max: c.calls_max,
    calls_reset_at: c.calls_reset_at,
    portal_url,
  });
}

// ---------- entrypoint ----------

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const origin = req.headers.get('origin') || env.PUBLIC_BASE_URL;

    if (req.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    let res: Response;
    try {
      if (url.pathname === '/api/checkout/donate' && req.method === 'POST') {
        res = await handleDonate(req, env);
      } else if (url.pathname === '/api/checkout/subscribe' && req.method === 'POST') {
        res = await handleSubscribe(req, env);
      } else if (url.pathname === '/api/webhook/stripe' && req.method === 'POST') {
        res = await handleWebhook(req, env);   // no CORS — Stripe calls server-to-server
        return res;
      } else if (url.pathname === '/api/me' && req.method === 'GET') {
        res = await handleMe(req, env);
      } else {
        res = json({ error: 'not found' }, 404);
      }
    } catch (e) {
      res = json({ error: (e as Error).message }, 500);
    }
    const merged = new Headers(res.headers);
    for (const [k, v] of Object.entries(corsHeaders(origin))) merged.set(k, v as string);
    return new Response(res.body, { status: res.status, headers: merged });
  },
};
