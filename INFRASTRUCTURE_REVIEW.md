# Cortex Infrastructure Review — 2026-05-04

> Goal: **public website always reachable from anywhere**, **live data updates with the lowest practical latency**, frontend that's **clean, dynamic, page-by-page** instead of one busy mega-page. Compute stays on Seratonin (RTX 5090) + Big Apple (M4 Max) — those are non-negotiable.

This document is a recommendation, not a deploy. Reviews three layers (network, transport, frontend) and ends with a phased rollout.

---

## 1. Networking — public ingress

### Current shape

```
                              ┌──────────────────────┐
  internet  ───── HTTPS ────▶│ Tailscale Funnel      │── public ingress
                              │ big-apple.scylla-     │   (free, scoped to
                              │   betta.ts.net        │    one tailnet host)
                              └──────────┬───────────┘
                                         │ direct WireGuard
                                         ▼
                              ┌──────────────────────┐
                              │ Big Apple :8773      │── cortex backend
                              └──────────┬───────────┘
                                         │ TRIBE-needed proxy via
                                         │ CORTEX_TRIBE_PROXY
                                         ▼ http://100.98.19.87:8773
                              ┌──────────────────────┐
                              │ Seratonin :8773/8766 │── TRIBE + router
                              └──────────────────────┘

  + Cloudflare Tunnel — `cortex.redteamkitchen.com` → rtk-5090 tunnel
                       → Seratonin direct (alternative path)
```

### Options compared

| Option | Public access | Latency from EU/Asia | TLS | Cost | Auth gates | Failure mode |
|---|---|---|---|---|---|---|
| **Tailscale Funnel** (current) | Yes (`*.ts.net`) | 1 hop via TS DERP relay if no direct path; usually 60–120 ms US ↔ EU | Auto (LetsEncrypt via TS) | Free | None on Funnel by design | Funnel gateway is single-region (US) — Asia adds 200+ ms |
| **Cloudflare Tunnel** (already configured for `cortex.redteamkitchen.com`) | Yes (`*.redteamkitchen.com`) | 30–60 ms anywhere — CF anycast | Auto (CF) | Free | Optional CF Access (Zero Trust) | Anycast smoothes geo; CF can cache | 
| Tailscale Funnel + CF in front | Yes | CF anycast hop, then TS warp into BA | Both | Free | CF Access available | Adds one hop; redundant |
| Pure Cloudflare Pages (static) + API at tunnel | Yes | Best (CF anycast for HTML) | Auto | Free | CF Access | Frontend shipped to CF edge, API still via tunnel |
| WireGuard self-hosted | Yes if you forward a port | Direct, 1 hop | DIY (Caddy/nginx + LE) | Free + DNS A record | DIY | Most work; full control; no third-party policy risk |
| ngrok / Cloudflared / Pinggy | Yes | Comparable to CF tunnel | Auto | Free tiers limited | Per-vendor | Vendor lock-in, free-tier rate limits |

### Recommendation

**Promote Cloudflare Tunnel + CF Pages to the canonical ingress**, demote Tailscale Funnel to backup.

Reasons:
1. **Anycast**: CF terminates in 250+ POPs. A user in Tokyo hits a Tokyo POP at <30 ms; Funnel forces them to a US relay first.
2. **Caching**: CF can cache static assets (`/main.js`, `/gridstack.min.css`, `/brain_fsaverage5.glb` — the 3D mesh is multiple MB). Free CDN ≈ free latency.
3. **DDoS / WAF**: free-tier CF gives basic protection; Funnel has none.
4. **DNS choice**: `cortex.redteamkitchen.com` is short, brandable, copyable. `big-apple.scylla-betta.ts.net` looks like a system test.
5. **Tunnel is already up** — `rtk-5090` tunnel via `~/.cloudflare/credentials`; rerouting is config-only.

Tailscale stays as:
- The internal mesh for Sera ↔ Big Apple ↔ Pi 5 (this is its sweet spot, do not move)
- A backup public ingress in case CF Tunnel breaks (Funnel URL stays valid)
- Identity for SSH and `dev.cortex.redteamkitchen.com` if we ever gate that path

**Local-network-only is wrong for our goal** — the user explicitly wants "always accessible from anywhere", which rules out LAN-only. Both Sera and BA are already behind tunnels; flipping the canonical URL is a one-config-line move.

### Concrete one-day migration

1. Add a CF Tunnel route: `cortex.redteamkitchen.com` → `http://localhost:8773` on Big Apple (already done for Sera; mirror to BA).
2. Add a 2nd tunnel for `status.cortex.redteamkitchen.com` → `/status` on whichever node is host.
3. Set CF page rule: cache `*.js *.css *.glb *.json` with 24 h browser TTL, edge TTL 7 days. Bump cache version via the `?v=…` query string (the HTML already does this).
4. Move the marketing page (`/`) to **Cloudflare Pages** — it's pure HTML+JS+GLB, no server side. Build = `webapp/public/`, output = `webapp/public/`. The 3D brain lives there, the API still hits the tunnel for `/api/*` and `/scan/*`.
5. Once the CF path is verified, set `CORTEX_PUBLIC_URL=https://cortex.redteamkitchen.com` in the env and update `URLS.md` accordingly.

---

## 2. Transport — live data updates

### Current shape

The frontend polls a few endpoints on intervals:
- `/api/fleet-health` every 2 s (status + GPU)
- `/api/scans?limit=…` every 5 s (gallery)
- `/api/health` every 2 s (legacy)
- WebSocket already exists (`hub.broadcast(...)` in `webapp/server.py`) for **scan progress events** (`scan_progress`, `scan_complete`, `scan_failed`, `scan_narrations_ready`) — it's wired but underused (only the live activity stream consumes it).

### Options for live updates

| Mechanism | Use case | Latency | Reconnect | Server cost | Mobile/proxy compatible | Bidirectional |
|---|---|---|---|---|---|---|
| **HTTP polling** (current default) | Periodic state | 2–5 s | Trivial | High (N clients × M endpoints) | Yes | No |
| **Server-Sent Events (SSE)** | Server-push of typed events | <100 ms | Browser does it free | Low (one open conn per client, no protocol overhead) | Yes (over HTTPS) | One-way (server→client) |
| **WebSocket** (already wired) | Bidirectional + low-latency | <50 ms | Manual logic | Low | Mostly yes; some corp proxies block WS upgrade | Yes |
| **WebTransport / QUIC** | Multi-stream low-latency | <30 ms | Native | Lowest | Chrome-only as of 2026 | Yes |
| Long-poll | Fallback for hostile networks | Comparable to SSE | Built-in | Higher than SSE | Universal | One-way |

### Recommendation

**Move all "live" data off polling onto the existing WebSocket** + add a small SSE fallback for read-only telemetry. Specifically:

1. **Fleet health, GPU, queue depth, scan list** → push from server when something changes, instead of every client polling every 2 s.
   - Server: emit `fleet:gpu`, `fleet:queue`, `fleet:scan-update` on the existing hub.
   - Client: subscribe in `wireTelemetry()` and `pollFleet()`, drop the `setInterval`.
2. **Scan progress** → already on WS; keep as-is, just consume more events.
3. **Watchdog state** (`http://localhost:8780/status` on Sera) → expose via the hub too, since the cortex backend can poll the watchdog and re-broadcast at sub-second cadence.
4. **Robinhood Legend reference**: their live tape is socket-driven; their layout is Golden-Layout/Dock-style. We already adopted gridstack; matching their update cadence means moving telemetry to the socket.

Latency budget if we do this:
- TRIBE state change (e.g. "GPU went busy") → BA backend → WS → browser: **<50 ms** end-to-end on the same continent.
- Scan completion event already round-trips at this latency; just expose more event types.

### Implementation sketch (for the next batch)

```python
# webapp/server.py — already exists
class WebSocketHub:
    async def broadcast(self, msg: dict): ...

# Add a periodic broadcaster (cheap; one task, not per-client):
async def _telemetry_loop(app):
    last = None
    while True:
        snap = await build_fleet_snapshot()   # existing fleet-health logic
        if snap != last:                       # only push on change
            await hub.broadcast({"type": "fleet:health", "data": snap})
            last = snap
        await asyncio.sleep(0.5)               # 2 Hz max
```

```javascript
// webapp/public/main.js — replace setInterval(pollFleet, 2000) with:
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.type === "fleet:health") paintFleet(m.data);
});
```

Net effect: fleet UI feels truly live, server-side load drops by 100×.

---

## 3. Frontend — page-by-page, dynamic, fluid

### What's wrong with one mega-page

- Initial paint loads the 3D mesh + D3 + Three.js + gridstack + everything. ~2 MB JS, ~4 MB GLB. Perceived load > 3 s on slow connections.
- Visitors hunting for "what is this" don't want a fleet-status dashboard.
- Operators don't want a marketing page.
- A single bug in one section can crater the whole page.

### Recommendation: split into routes

```
/                      → marketing landing (static, tiny, ships from CF Pages)
/demo                  → the brain viewer + upload (the current index.html, slimmed)
/gallery               → scan gallery (current /gallery.html, becomes its own SPA)
/scan/<id>             → single-scan deep view (currently /?scan=…)
/status                → fleet status (current /status.html)
/personas              → persona cards (current /personas.html)
/specs                 → tech specs (current /specs.html)
/console               → operator-only — drag-resize panels, watchdog ctrl, kanban-style
```

Implementation: **adopt a tiny client router** (no React; the existing code is vanilla). Use the [Navigation API](https://developer.mozilla.org/en-US/docs/Web/API/Navigation_API) where supported, history-API fallback elsewhere. Each route lazy-loads its page bundle.

### Apple-style polish (worth copying)

After auditing recent apple.com pages (iPhone 17 Pro, MacBook Pro M5, Vision Pro):

| Pattern | What it is | How to ship it for Cortex |
|---|---|---|
| **Pinned scroll storytelling** | Section pins to the viewport, content advances per scroll-tick | `IntersectionObserver` + `position: sticky` on hero sections of `/`. The 3D brain rotates as you scroll past the hero. |
| **Parallax depth** | Foreground/background move at different rates | CSS `transform: translateY(--scroll * factor)` driven by `scroll` + `requestAnimationFrame` |
| **Glassmorphism nav** | Frosted nav bar that gains opacity on scroll | We already have this on the topbar. |
| **Scroll-triggered Three.js** | Camera moves on scroll progress | Already have Three.js; just expose camera to scroll-progress in the demo route. |
| **Per-section colour scheme** | Hero is dark, "How it works" pivots to bone, etc. | CSS custom-properties swapped per route. ISU palette gives us cardinal/gold/bone/midnight to play with. |
| **Spring transitions, never linear** | Everything eases in with a slight overshoot | Already on `--spring: cubic-bezier(0.34, 1.56, 0.64, 1)`. Apply more broadly. |

### Robinhood Legend — what's worth stealing

I scanned recent Legend product threads and reviews. The standout patterns:

1. **Configurable workspaces** — every panel is a draggable, resizable card. Layout persists per-user, per-device. **We have this** via gridstack + `localStorage` (committed today).
2. **Live socket tape** — every quote / news / chart update streams over WS. **Plan above covers this.**
3. **Right-rail dynamic detail** — clicking a row in one panel updates the right rail without a navigation. Pattern = a single shared `selectedScan` state, rendered into a `<details-pane>` slot.
4. **Studio mode toggle** — beginner UI vs power-user UI is a single toggle. Beginner hides advanced panels; power-user enables Mercury TUI inline + watchdog ctrl + raw API responses. We can do this with a single CSS class on `<body>`.
5. **Keyboard shortcuts** — `?` for help, `s` to focus search, `g h` to go home. Should add these.

### Human-centred design principles applied (Norman + ISO 9241-210)

1. **Visibility of system status** — fleet is visible at a glance (card colours), persona narrations open automatically when the scan completes. ✅ already done in this turn.
2. **Match the system to the real world** — persona names are roles people recognise (Student, Patient, Clinician, Researcher), not invented categories. ✅
3. **User control and freedom** — drag-resize layout persists; "Reset layout" undoes. ✅
4. **Consistency and standards** — every interactive element uses the cardinal-red gradient pill. ✅
5. **Error prevention** — disabled `Analyze` button until a file is selected; validation before TRIBE submit.
6. **Recognition rather than recall** — collapsed cards have a badge with the live count; you don't have to remember whether there are events.
7. **Flexibility and efficiency** — keyboard shortcuts (TODO), saved layouts (✅).
8. **Aesthetic and minimalist design** — collapse-by-default for narrations + charts; one CTA per region. ✅ (today)
9. **Help users recognize, diagnose, recover from errors** — error toasts on scan failure (TODO; currently logs to console).
10. **Help and documentation** — `/personas`, `/specs`, `/status` already exist; need a `/docs` aggregator route.

---

## Phased rollout

### Phase 1 — this week (one-evening tasks)

- [x] Frontend trimmed (this turn)
- [ ] Cloudflare Tunnel route added for `cortex.redteamkitchen.com` → Big Apple :8773 (mirror Sera config)
- [ ] CF page rule: cache `*.js *.css *.glb`
- [ ] WebSocket fleet-health broadcaster added; client poll dropped on the WS path; polling kept as fallback
- [ ] Routes split: `/demo`, `/gallery`, `/status`, `/personas`, `/specs` become first-class (right now they're all sub-pages off the same shell)

### Phase 2 — next week

- [ ] CF Pages: ship `/` as a static marketing landing built from a slimmer template
- [ ] Apple-style scroll-driven hero on the marketing page (Three.js camera tied to scroll)
- [ ] Beginner ↔ power-user mode toggle
- [ ] Keyboard shortcuts (`g d`, `g g`, `g s`, `?`)

### Phase 3 — pair with Mercury divergence

- [ ] All telemetry over WS (drop polling everywhere)
- [ ] `/console` operator route — Mercury TUI embedded via xterm.js, watchdog control, kanban
- [ ] Scoped CF Access policy on `/console` (Cloudflare Zero Trust)

---

## TL;DR

- **Networking**: stay on Tailscale internally, switch the canonical public URL to **Cloudflare Tunnel + Pages** (`cortex.redteamkitchen.com`). Gives anycast latency, CDN caching, free DDoS, and a brand-friendly URL. Tailscale stays as backup.
- **Live updates**: existing WebSocket hub is underused. Move fleet-health + gallery + status off polling onto the socket — sub-50 ms end-to-end on the same continent, ~100× server load drop.
- **Frontend**: split into routes (`/demo`, `/gallery`, `/status`, etc.), keep gridstack-driven draggable layout (✅ today), add Apple-style scroll storytelling on the marketing page, persist layouts per-user.
- **Compute stays on Seratonin + Big Apple.** No move.
