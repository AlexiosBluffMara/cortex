"""cortex-dashboard
Live wall-display server for the Pi kiosk in the living room.

Two views, served from Seratonin on port 9090:
  http://seratonin:9090/mercury   →  left monitor (Mercury feed)
  http://seratonin:9090/cortex    →  right monitor (Cortex / Ascended Base)
  http://seratonin:9090/          →  combined wide view (single 4K monitor)

Server-Sent Events at /events stream live updates every second.

Sources scraped (pull, no daemons):
  Mercury:
    ~/.mercury/cron/jobs.json              upcoming + recent cron jobs
    ~/.mercury/cron/output/<job>/*.md      cron run outputs
    ~/.mercury/sessions/                   active conversations (JSONL)
    ~/.mercury/logs/                       agent stdout
  Cortex:
    local Cortex health + router health
    nvidia-smi (Seratonin GPU)
    AdGuard Home stats API on baby-pi      query log + filter hits

Brand: cardinal red #CC0000 on Material 3 dark.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
import httpx

# ---------------------------------------------------------------------------
# Config (env-tunable)
# ---------------------------------------------------------------------------
MERCURY_HOME = Path(os.environ.get("MERCURY_HOME", str(Path.home() / ".mercury")))
PI_HOST = os.environ.get("PI_HOST", "baby-pi")
ADGUARD_PASS = os.environ.get("ADGUARD_PASS", "ChangeMeNow!")
ADGUARD_USER = os.environ.get("ADGUARD_USER", "soumit")
POLL_SEC = float(os.environ.get("POLL_SEC", "2.0"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Source scrapers (each returns a dict; missing pieces tolerated)
# ---------------------------------------------------------------------------
def scrape_mercury_cron() -> dict[str, Any]:
    out = {"jobs": [], "recent_outputs": []}
    jobs_file = MERCURY_HOME / "cron" / "jobs.json"
    if jobs_file.exists():
        try:
            jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
            if isinstance(jobs, list):
                out["jobs"] = [{
                    "id": j.get("id"),
                    "name": j.get("name") or j.get("prompt", "")[:60],
                    "schedule": j.get("schedule"),
                    "next_run": j.get("next_run"),
                    "last_run": j.get("last_run"),
                    "enabled": j.get("enabled", True),
                    "skill": j.get("skill") or (j.get("skills") or [None])[0],
                } for j in jobs[:30]]
        except Exception:
            pass
    output_dir = MERCURY_HOME / "cron" / "output"
    if output_dir.exists():
        recent = []
        for jdir in output_dir.iterdir():
            if not jdir.is_dir():
                continue
            for f in sorted(jdir.glob("*.md"),
                            key=lambda p: p.stat().st_mtime, reverse=True)[:3]:
                recent.append({
                    "job": jdir.name,
                    "ts": datetime.fromtimestamp(f.stat().st_mtime,
                                                 tz=timezone.utc).isoformat(timespec="seconds"),
                    "size": f.stat().st_size,
                    "preview": f.read_text(encoding="utf-8", errors="replace")[:400],
                })
        recent.sort(key=lambda x: x["ts"], reverse=True)
        out["recent_outputs"] = recent[:15]
    return out


def scrape_mercury_sessions() -> dict[str, Any]:
    out = {"active": [], "recent_messages": []}
    sess_dir = MERCURY_HOME / "sessions"
    if not sess_dir.exists():
        return out
    cutoff = time.time() - 7 * 24 * 3600
    sessions = sorted(
        [p for p in sess_dir.glob("*.jsonl") if p.stat().st_mtime > cutoff],
        key=lambda p: p.stat().st_mtime, reverse=True
    )[:10]
    for s in sessions:
        try:
            with s.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            if not lines:
                continue
            head = json.loads(lines[0])
            tail = json.loads(lines[-1])
            out["active"].append({
                "id": s.stem,
                "started": head.get("timestamp") or head.get("ts"),
                "last": tail.get("timestamp") or tail.get("ts"),
                "n_turns": len(lines),
                "user": head.get("user") or head.get("from") or "?",
                "last_role": tail.get("role") or tail.get("from") or "?",
                "last_preview": (tail.get("content") or tail.get("value") or "")[:200],
            })
            for line in lines[-5:]:
                try:
                    msg = json.loads(line)
                    out["recent_messages"].append({
                        "session": s.stem,
                        "ts": msg.get("timestamp") or msg.get("ts"),
                        "role": msg.get("role") or msg.get("from") or "?",
                        "preview": (msg.get("content") or msg.get("value") or "")[:300],
                    })
                except json.JSONDecodeError:
                    continue
        except Exception:
            continue
    out["recent_messages"].sort(
        key=lambda m: m.get("ts") or "", reverse=True
    )
    out["recent_messages"] = out["recent_messages"][:30]
    return out


_cortex_health_cache: dict[str, Any] = {"ts": 0}


async def scrape_cortex_health() -> dict[str, Any]:
    """Read local Cortex/router health for the kiosk status pane."""
    if time.time() - _cortex_health_cache["ts"] < POLL_SEC:
        return _cortex_health_cache.get("data", {})
    backends: dict[str, str] = {}
    log_tail: list[str] = []
    state = "red"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            health = await client.get("http://localhost:8765/api/health")
            backends["cortex-webapp"] = "UP" if health.status_code == 200 else f"HTTP {health.status_code}"
            log_tail.append(f"cortex-webapp {backends['cortex-webapp']}")
            try:
                router = await client.get("http://localhost:8766/healthz")
                if router.status_code == 200:
                    rj = router.json()
                    for url, ok in (rj.get("ollama_backends") or {}).items():
                        backends[url] = "UP" if ok else "DOWN"
                    backends["openrouter"] = "UP" if rj.get("openrouter") else "DOWN"
                    log_tail.append("router UP")
                else:
                    backends["router"] = f"HTTP {router.status_code}"
            except Exception as exc:
                backends["router"] = "DOWN"
                log_tail.append(f"router DOWN: {exc}")
            state = "green" if backends.get("cortex-webapp") == "UP" else "red"
            if any(v == "DOWN" or str(v).startswith("HTTP") for v in backends.values()):
                state = "amber" if state == "green" else "red"
    except Exception as exc:
        log_tail.append(f"health scrape failed: {exc}")

    data = {
        "state": state,
        "backends": backends,
        "lights": {},
        "log_tail": log_tail[-12:],
    }
    _cortex_health_cache.update({"ts": time.time(), "data": data})
    return data


async def scrape_seratonin_gpu() -> dict[str, Any]:
    nvsmi = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    if not Path(nvsmi).exists():
        nvsmi = "nvidia-smi"
    try:
        proc = await asyncio.create_subprocess_exec(
            nvsmi,
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
        except asyncio.TimeoutError:
            proc.kill()
            return {}
    except Exception:
        return {}
    line = raw.decode("utf-8", errors="ignore").strip().splitlines()
    if not line:
        return {}
    parts = [x.strip() for x in line[0].split(",")]
    if len(parts) < 6:
        return {}
    name, util, mem_used, mem_total, temp, power = parts
    return {
        "name": name,
        "util_pct": int(float(util)),
        "mem_used_mib": int(float(mem_used)),
        "mem_total_mib": int(float(mem_total)),
        "temp_c": int(float(temp)),
        "power_w": float(power),
    }


_adguard_session: dict[str, Any] = {"client": None, "cookie": None, "ts": 0}


async def scrape_adguard() -> dict[str, Any]:
    """Pull stats + last query log entries from AdGuard Home on the Pi.
    Auth uses the soumit/ChangeMeNow! credentials baked into AdGuardHome.yaml.
    """
    base = f"http://{PI_HOST}"
    try:
        if _adguard_session["client"] is None:
            _adguard_session["client"] = httpx.AsyncClient(timeout=4)
        client = _adguard_session["client"]
        if not _adguard_session["cookie"] or time.time() - _adguard_session["ts"] > 300:
            r = await client.post(f"{base}/control/login",
                                  json={"name": ADGUARD_USER, "password": ADGUARD_PASS})
            if r.status_code != 200:
                return {}
            _adguard_session["cookie"] = r.cookies
            _adguard_session["ts"] = time.time()
        cookies = _adguard_session["cookie"]
        stats = (await client.get(f"{base}/control/stats", cookies=cookies)).json()
        ql = (await client.get(f"{base}/control/querylog?limit=20", cookies=cookies)).json()
        return {
            "queries_24h": stats.get("num_dns_queries", 0),
            "blocked_24h": stats.get("num_blocked_filtering", 0),
            "blocked_pct": (
                round(100 * stats.get("num_blocked_filtering", 0) /
                      max(1, stats.get("num_dns_queries", 1)), 1)
            ),
            "top_blocked": stats.get("top_blocked_domains", [])[:8],
            "top_clients": stats.get("top_clients", [])[:8],
            "recent": [{
                "domain": q.get("question", {}).get("name"),
                "client": q.get("client"),
                "blocked": (q.get("reason") or "").startswith("Filtered"),
                "ts": q.get("time"),
            } for q in ql.get("data", [])[:20]],
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
async def collect() -> dict[str, Any]:
    o, gpu, ag = await asyncio.gather(
        scrape_cortex_health(),
        scrape_seratonin_gpu(),
        scrape_adguard(),
    )
    return {
        "ts": now_iso(),
        "mercury": {
            "cron": scrape_mercury_cron(),
            "sessions": scrape_mercury_sessions(),
        },
        "cortex": {
            "orchestra": o,
            "gpu_seratonin": gpu,
            "adguard": ag,
        },
    }


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="cortex-dashboard", version="1.0.0")


@app.get("/snapshot")
async def snapshot():
    return await collect()


@app.get("/events")
async def events():
    async def gen() -> AsyncIterator[bytes]:
        while True:
            try:
                data = await collect()
                yield f"data: {json.dumps(data)}\n\n".encode("utf-8")
            except Exception as exc:
                yield f"data: {json.dumps({'error': repr(exc)})}\n\n".encode("utf-8")
            await asyncio.sleep(POLL_SEC)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/", response_class=HTMLResponse)
@app.get("/wide", response_class=HTMLResponse)
async def wide():
    return HTMLResponse(_HTML.replace("{{VIEW}}", "wide"))


@app.get("/mercury", response_class=HTMLResponse)
async def mercury_view():
    return HTMLResponse(_HTML.replace("{{VIEW}}", "mercury"))


@app.get("/cortex", response_class=HTMLResponse)
async def cortex_view():
    return HTMLResponse(_HTML.replace("{{VIEW}}", "cortex"))


# ---------------------------------------------------------------------------
# Embedded HTML — single file, no build step. Tailwind CDN + Chart.js CDN.
# Material 3 dark + cardinal red #CC0000.
# ---------------------------------------------------------------------------
_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Ascended Base — Live</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Google+Sans+Code:wght@300;400;500;700&family=Inter:wght@300;400;500;700;900&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0c;
    --surface: #14141a;
    --surface-2: #1c1c24;
    --line: #2a2a35;
    --text: #e8e8ec;
    --text-dim: #8a8a96;
    --cardinal: #CC0000;
    --cardinal-dim: #7a0000;
    --green: #4ade80;
    --amber: #fbbf24;
    --red: #ef4444;
  }
  html, body { background: var(--bg); color: var(--text);
    font-family: 'Inter', system-ui, sans-serif; height: 100%; margin: 0; }
  .mono { font-family: 'Google Sans Code', ui-monospace, monospace; }
  .surface { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; }
  .surface-2 { background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; }
  .cardinal { color: var(--cardinal); }
  .cardinal-bg { background: var(--cardinal); }
  .glow-cardinal { box-shadow: 0 0 24px rgba(204,0,0,0.4); }
  .pulse-green::before, .pulse-amber::before, .pulse-red::before {
    content: ''; display: inline-block; width: 10px; height: 10px;
    border-radius: 50%; margin-right: 8px; vertical-align: middle;
    animation: pulse 2s infinite;
  }
  .pulse-green::before { background: var(--green); box-shadow: 0 0 12px var(--green); }
  .pulse-amber::before { background: var(--amber); box-shadow: 0 0 12px var(--amber); }
  .pulse-red::before   { background: var(--red);   box-shadow: 0 0 12px var(--red);   }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .ticker { animation: ticker 60s linear infinite; }
  @keyframes ticker {
    0% { transform: translateX(0); }
    100% { transform: translateX(-50%); }
  }
  .fade-in { animation: fade-in 0.5s ease-out; }
  @keyframes fade-in {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .hatched {
    background-image: repeating-linear-gradient(
      45deg, transparent, transparent 8px,
      rgba(204,0,0,0.08) 8px, rgba(204,0,0,0.08) 12px);
  }
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-thumb { background: var(--line); border-radius: 4px; }
  .grow-line { background: linear-gradient(90deg, transparent, var(--cardinal), transparent); height: 1px; }
</style>
</head><body class="overflow-hidden">

<div id="app" class="flex flex-col h-screen p-4 gap-4">

  <!-- ============== TOP BAR ============== -->
  <div class="surface px-6 py-3 flex items-center justify-between glow-cardinal">
    <div class="flex items-center gap-4">
      <div class="text-2xl font-black tracking-tight">
        <span class="cardinal">ASCENDED</span><span class="text-white"> BASE</span>
      </div>
      <div class="grow-line w-32"></div>
      <div id="cluster-state" class="mono text-sm pulse-amber">awaiting...</div>
    </div>
    <div class="flex items-center gap-6 text-sm">
      <div><span class="text-[var(--text-dim)]">GPU</span>
           <span id="gpu-line" class="mono ml-2">—</span></div>
      <div><span class="text-[var(--text-dim)]">DNS blocked</span>
           <span id="adguard-pct" class="mono ml-2 cardinal">—</span></div>
      <div class="mono text-[var(--text-dim)]" id="clock">—</div>
    </div>
  </div>

  <!-- ============== MAIN PANES ============== -->
  <div id="panes" class="flex-1 flex gap-4 min-h-0">

    <!-- MERCURY PANE -->
    <div id="mercury-pane" class="flex-1 flex flex-col gap-4 min-h-0">
      <div class="surface p-5 flex flex-col gap-3 min-h-0 flex-1">
        <div class="flex items-baseline justify-between">
          <h2 class="text-xl font-bold cardinal">MERCURY</h2>
          <span class="mono text-xs text-[var(--text-dim)]">snowy-the-bot · live feed</span>
        </div>
        <div class="grow-line"></div>
        <div class="grid grid-cols-3 gap-3">
          <div class="surface-2 p-3"><div class="text-xs text-[var(--text-dim)]">ACTIVE SESSIONS</div>
            <div id="mer-active" class="mono text-3xl font-bold cardinal">0</div></div>
          <div class="surface-2 p-3"><div class="text-xs text-[var(--text-dim)]">CRON JOBS</div>
            <div id="mer-cron" class="mono text-3xl font-bold cardinal">0</div></div>
          <div class="surface-2 p-3"><div class="text-xs text-[var(--text-dim)]">RECENT TURNS</div>
            <div id="mer-turns" class="mono text-3xl font-bold cardinal">0</div></div>
        </div>
        <div class="text-xs uppercase tracking-wider text-[var(--text-dim)] mt-2">Live conversation feed</div>
        <div id="mer-feed" class="flex-1 overflow-y-auto space-y-2 mono text-sm pr-2"></div>
      </div>
      <div class="surface p-4 flex flex-col gap-2">
        <div class="text-xs uppercase tracking-wider text-[var(--text-dim)]">Cron schedule</div>
        <div id="mer-cron-list" class="text-sm mono space-y-1 max-h-40 overflow-y-auto"></div>
      </div>
    </div>

    <!-- CORTEX PANE -->
    <div id="cortex-pane" class="flex-1 flex flex-col gap-4 min-h-0">
      <div class="surface p-5 flex flex-col gap-3 min-h-0">
        <div class="flex items-baseline justify-between">
          <h2 class="text-xl font-bold cardinal">CORTEX</h2>
          <span class="mono text-xs text-[var(--text-dim)]">local Cortex stack</span>
        </div>
        <div class="grow-line"></div>
        <div id="backends-grid" class="grid grid-cols-2 gap-2"></div>
        <div class="h-32"><canvas id="throughput-chart"></canvas></div>
      </div>
      <div class="surface p-4 flex flex-col gap-2 flex-1 min-h-0">
        <div class="flex items-baseline justify-between">
          <div class="text-xs uppercase tracking-wider text-[var(--text-dim)]">AdGuard live query log</div>
          <div class="text-xs mono text-[var(--text-dim)]">
            <span id="ag-q24">—</span> queries · <span id="ag-b24" class="cardinal">—</span> blocked / 24h
          </div>
        </div>
        <div class="grow-line"></div>
        <div id="ag-feed" class="flex-1 overflow-y-auto mono text-xs space-y-1"></div>
      </div>
    </div>
  </div>

  <!-- ============== TICKER ============== -->
  <div class="surface py-2 px-4 overflow-hidden hatched">
    <div id="ticker" class="whitespace-nowrap mono text-xs text-[var(--text-dim)] ticker">
      ASCENDED BASE · ALEXIOS BLUFF MARA LLC · DBA RED TEAM KITCHEN · SERATONIN.SCYLLA-BETTA.TS.NET · CARDINAL #CC0000 ·
    </div>
  </div>
</div>

<script>
const VIEW = "{{VIEW}}";
const $ = (id) => document.getElementById(id);

// Hide unused pane in mercury / cortex modes
if (VIEW === "mercury")  $("cortex-pane").style.display = "none";
if (VIEW === "cortex")   $("mercury-pane").style.display = "none";

// Throughput chart
const ctx = $("throughput-chart").getContext("2d");
const tps = {};
const chart = new Chart(ctx, {
  type: "line",
  data: { labels: [], datasets: [] },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { labels: { color: '#8a8a96', font: { size: 10 } } } },
    scales: {
      x: { ticks: { color: '#5a5a66', font: { size: 9 } },
           grid: { color: '#2a2a35' } },
      y: { ticks: { color: '#5a5a66', font: { size: 9 } },
           grid: { color: '#2a2a35' }, beginAtZero: true }
    }
  }
});
const COLORS = ['#CC0000', '#fbbf24', '#4ade80', '#60a5fa', '#a78bfa'];
function pushTps(name, val) {
  if (!tps[name]) {
    tps[name] = { x: [], y: [] };
    chart.data.datasets.push({
      label: name, data: [],
      borderColor: COLORS[chart.data.datasets.length % COLORS.length],
      backgroundColor: COLORS[chart.data.datasets.length % COLORS.length] + '22',
      borderWidth: 2, pointRadius: 0, tension: 0.3, fill: true,
    });
  }
  const ds = chart.data.datasets.find(d => d.label === name);
  const t = new Date().toLocaleTimeString();
  if (chart.data.labels.length === 0 || chart.data.labels[chart.data.labels.length - 1] !== t) {
    chart.data.labels.push(t);
    if (chart.data.labels.length > 60) chart.data.labels.shift();
  }
  ds.data.push(val);
  if (ds.data.length > 60) ds.data.shift();
}

function clockTick() {
  const d = new Date();
  $("clock").textContent = d.toTimeString().slice(0,8) + ' · ' + d.toDateString().slice(4);
}
setInterval(clockTick, 1000); clockTick();

function render(s) {
  // Top bar — cluster state
  const oc = s.cortex.orchestra || {};
  const stateEl = $("cluster-state");
  stateEl.className = "mono text-sm pulse-" + (oc.state || "amber");
  stateEl.textContent = "CLUSTER " + (oc.state || "unknown").toUpperCase();

  const gpu = s.cortex.gpu_seratonin || {};
  if (gpu.name) {
    $("gpu-line").innerHTML =
      `${gpu.util_pct}%  <span class="cardinal">${gpu.temp_c}°C</span>  ${(gpu.mem_used_mib/1024).toFixed(1)}/${(gpu.mem_total_mib/1024).toFixed(0)}G  ${gpu.power_w.toFixed(0)}W`;
  } else {
    $("gpu-line").textContent = "offline";
  }

  // ============ MERCURY ============
  const mer = s.mercury || {};
  $("mer-active").textContent = (mer.sessions?.active || []).length;
  $("mer-cron").textContent = (mer.cron?.jobs || []).filter(j => j.enabled).length;
  $("mer-turns").textContent = (mer.sessions?.recent_messages || []).length;

  const feed = $("mer-feed");
  feed.innerHTML = "";
  for (const m of (mer.sessions?.recent_messages || [])) {
    const role = m.role || "?";
    const color = role === "user" || role === "human" ? "var(--cardinal)" :
                  role === "assistant" || role === "gpt" ? "var(--green)" :
                  "var(--text-dim)";
    const div = document.createElement("div");
    div.className = "fade-in surface-2 p-2";
    div.innerHTML = `<div class="text-[10px] flex justify-between">
      <span style="color:${color}" class="font-bold uppercase">${role}</span>
      <span class="text-[var(--text-dim)]">${m.session?.slice(0,8)} · ${(m.ts || '').slice(11,19)}</span>
    </div><div class="text-xs mt-1">${escapeHtml(m.preview || '')}</div>`;
    feed.appendChild(div);
  }

  const cronList = $("mer-cron-list");
  cronList.innerHTML = "";
  for (const j of (mer.cron?.jobs || []).slice(0, 12)) {
    const div = document.createElement("div");
    div.className = "flex justify-between items-baseline gap-2";
    div.innerHTML = `<span class="${j.enabled ? '' : 'text-[var(--text-dim)] line-through'}">${escapeHtml(j.name || j.id)}</span>
      <span class="text-[var(--text-dim)] text-xs">${escapeHtml(j.schedule || '')}${j.skill ? ' · ' + j.skill : ''}</span>`;
    cronList.appendChild(div);
  }

  // ============ CORTEX ============
  const grid = $("backends-grid");
  grid.innerHTML = "";
  const backends = oc.backends || {};
  for (const [name, val] of Object.entries(backends)) {
    const tps_match = String(val).match(/(\d+(?:\.\d+)?)t\/s/);
    const tps_val = tps_match ? parseFloat(tps_match[1]) : 0;
    const isDown = String(val).toUpperCase().includes("DOWN");
    const div = document.createElement("div");
    div.className = "surface-2 p-3 fade-in";
    div.innerHTML = `<div class="text-[10px] uppercase tracking-wider text-[var(--text-dim)]">${name}</div>
      <div class="mono text-lg font-bold ${isDown ? 'text-[var(--text-dim)]' : 'cardinal'}">${val}</div>`;
    grid.appendChild(div);
    if (!isDown) pushTps(name, tps_val);
  }
  chart.update('none');

  // AdGuard
  const ag = s.cortex.adguard || {};
  if (ag.queries_24h !== undefined) {
    $("ag-q24").textContent = ag.queries_24h.toLocaleString();
    $("ag-b24").textContent = ag.blocked_24h.toLocaleString();
    $("adguard-pct").textContent = ag.blocked_pct + "%";
  }
  const agFeed = $("ag-feed");
  agFeed.innerHTML = "";
  for (const q of (ag.recent || [])) {
    const div = document.createElement("div");
    const blockedCls = q.blocked ? "text-[var(--red)] line-through" : "text-[var(--green)]";
    div.innerHTML = `<span class="text-[var(--text-dim)]">${(q.ts || '').slice(11,19)}</span>
      <span class="ml-2 ${blockedCls}">${escapeHtml(q.domain || '')}</span>
      <span class="ml-2 text-[var(--text-dim)] text-[10px]">${escapeHtml(q.client || '')}</span>`;
    agFeed.appendChild(div);
  }

  // Ticker
  const t = $("ticker");
  let parts = ["ASCENDED BASE", oc.state ? "CLUSTER " + oc.state.toUpperCase() : ""];
  if (s.cortex.gpu_seratonin?.temp_c) parts.push("5090 " + s.cortex.gpu_seratonin.temp_c + "°C");
  if (ag.blocked_24h) parts.push(ag.blocked_24h + " ADS BLOCKED 24H");
  if ((mer.sessions?.active || []).length) parts.push((mer.sessions.active.length) + " ACTIVE MERCURY SESSIONS");
  parts.push("CARDINAL #CC0000");
  t.textContent = "  ·  " + parts.join("  ·  ").repeat(2);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// SSE
const es = new EventSource("/events");
es.onmessage = (ev) => {
  try { render(JSON.parse(ev.data)); } catch (e) { console.error(e); }
};
es.onerror = () => {
  $("cluster-state").className = "mono text-sm pulse-red";
  $("cluster-state").textContent = "RECONNECTING...";
};

// Initial fetch in case SSE is slow
fetch("/snapshot").then(r => r.json()).then(render).catch(() => {});
</script>
</body></html>
"""


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "9090")),
                log_level="warning")


if __name__ == "__main__":
    main()
