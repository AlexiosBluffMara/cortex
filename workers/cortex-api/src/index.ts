import { Hono } from 'hono';
import { cors } from 'hono/cors';

type Env = {
  INFERENCE_BASE: string;
  OLLAMA_BASE: string;
  ALLOWED_ORIGIN: string;
  MAX_UPLOAD_BYTES: string;
};

const app = new Hono<{ Bindings: Env }>();

app.use('/api/*', async (c, next) => {
  const handler = cors({
    origin: c.env.ALLOWED_ORIGIN,
    allowMethods: ['GET', 'POST', 'OPTIONS'],
    allowHeaders: ['Content-Type'],
    maxAge: 600,
  });
  return handler(c, next);
});

app.get('/api/healthz', async (c) => {
  const upstream = `${c.env.INFERENCE_BASE}/healthz`;
  try {
    const r = await fetch(upstream, { cf: { cacheTtl: 0 } });
    const body = await r.text();
    return new Response(body, {
      status: r.status,
      headers: {
        'content-type': r.headers.get('content-type') ?? 'application/json',
        'cache-control': 'no-store',
      },
    });
  } catch (err) {
    return c.json({ ok: false, error: String(err) }, 502);
  }
});

app.get('/api/tags', async (c) => {
  const upstream = `${c.env.OLLAMA_BASE}/api/tags`;
  try {
    const r = await fetch(upstream, { cf: { cacheTtl: 0 } });
    const body = await r.text();
    return new Response(body, {
      status: r.status,
      headers: {
        'content-type': r.headers.get('content-type') ?? 'application/json',
        'cache-control': 'no-store',
      },
    });
  } catch (err) {
    return c.json({ ok: false, error: String(err) }, 502);
  }
});

app.post('/api/scan', async (c) => {
  const max = Number(c.env.MAX_UPLOAD_BYTES) || 52428800;
  const cl = Number(c.req.header('content-length') || '0');
  if (cl && cl > max) {
    return c.json({ error: 'file too large', max_bytes: max }, 413);
  }

  const ct = c.req.header('content-type') || '';
  if (!ct.toLowerCase().startsWith('multipart/form-data')) {
    return c.json({ error: 'expected multipart/form-data' }, 400);
  }

  const upstream = `${c.env.INFERENCE_BASE}/v1/scan`;
  try {
    const r = await fetch(upstream, {
      method: 'POST',
      headers: { 'content-type': ct },
      body: c.req.raw.body,
    });
    const body = await r.arrayBuffer();
    return new Response(body, {
      status: r.status,
      headers: {
        'content-type': r.headers.get('content-type') ?? 'application/json',
        'cache-control': 'no-store',
      },
    });
  } catch (err) {
    return c.json({ ok: false, error: String(err) }, 502);
  }
});

app.get('/api/narrations', async (c) => {
  const limit = c.req.query('limit') ?? '10';
  const upstream = `${c.env.INFERENCE_BASE}/v1/narrations?limit=${encodeURIComponent(limit)}`;
  try {
    const r = await fetch(upstream, { cf: { cacheTtl: 0 } });
    const body = await r.text();
    return new Response(body, {
      status: r.status,
      headers: {
        'content-type': r.headers.get('content-type') ?? 'application/json',
        'cache-control': 'no-store',
      },
    });
  } catch (err) {
    return c.json({ items: [], error: String(err) }, 200);
  }
});

app.all('/api/*', (c) => c.json({ error: 'not found' }, 404));

export default app;
