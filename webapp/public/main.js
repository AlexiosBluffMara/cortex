// Cortex viewer v2 — real fsaverage5 surface + TRIBE v2 BOLD + Gemma narration
import * as THREE from "https://esm.sh/three@0.176.0";
import { GLTFLoader } from "https://esm.sh/three@0.176.0/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "https://esm.sh/three@0.176.0/examples/jsm/controls/OrbitControls.js";

// ---------------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------------
const root          = document.getElementById("three-root");
const statusDot     = document.getElementById("status-dot");
const statusEl      = document.getElementById("status-text");
const gpuBadge      = document.getElementById("gpu-badge");
const eventLog      = document.getElementById("event-log");
const overlay       = document.getElementById("viewer-overlay");
const overlayInner  = overlay.querySelector("div");
const submitBtn     = document.getElementById("scan-submit");
const tierInput     = document.getElementById("scan-tier");
const tierDisplay   = document.getElementById("tier-display");
const scrubber      = document.getElementById("time-scrubber");
const timeLabel     = document.getElementById("time-label");
const playBtn       = document.getElementById("play-toggle");
const networkBar    = document.getElementById("network-bar");
const narrationBody = document.getElementById("narration-body");
const narrationModel= document.getElementById("narration-model");
const tooltip       = document.getElementById("region-tooltip");
const ttName        = document.getElementById("tt-name");
const ttMeta        = document.getElementById("tt-meta");
const ttZ           = document.getElementById("tt-z");

// ---------------------------------------------------------------------------
// Device detection
// ---------------------------------------------------------------------------
const isMobile = window.matchMedia('(max-width: 768px)').matches;

// ---------------------------------------------------------------------------
// Pipeline mode (local | cloud | cost)
// ---------------------------------------------------------------------------
let currentPipeline = isMobile ? 'cloud' : 'local';
const pipelineTabs = document.querySelectorAll('.pipeline-tab');
const brainLayout   = document.querySelector('.layout');
const costLayout    = document.getElementById('cost-layout');
const pipelineBadge = document.getElementById('pipeline-badge');

pipelineTabs.forEach(btn => {
    btn.addEventListener('click', () => {
        currentPipeline = btn.dataset.pipeline;
        pipelineTabs.forEach(b => b.classList.toggle('active', b === btn));
        if (currentPipeline === 'cost') {
            brainLayout.style.display = 'none';
            costLayout.classList.remove('hidden');
        } else {
            brainLayout.style.display = '';
            costLayout.classList.add('hidden');
            pipelineBadge.className = `pipeline-badge ${currentPipeline}`;
            pipelineBadge.textContent = currentPipeline === 'local'
                ? '⚡ Local · RTX 5090'
                : '☁ Cloud · Vertex AI';
            resize();
        }
    });
});

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const st = {
    atlas:          null,
    vertexLabels:   null,   // Int32Array[20484]: vertex → region idx (1-400, 0=none)
    regionNames:    [],     // string[400]
    regionNetwork:  [],     // string[400]  Yeo-7 key per region
    boldTrace:      null,
    scanResult:     null,
    scanId:         null,
    activeNetworks: new Set(),
    meshes:         [],
    meshLH:         null,
    meshRH:         null,
    lhVertCount:    0,
    playTimer:      null,
    tier:           4,
};

// ---------------------------------------------------------------------------
// Three.js scene
// ---------------------------------------------------------------------------
const scene    = new THREE.Scene();
scene.background = new THREE.Color(0x0b0d12);

const camera   = new THREE.PerspectiveCamera(45, 1, 0.05, 100);
camera.position.set(0, 0.15, 3.0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, isMobile ? 1.5 : 2));
root.appendChild(renderer.domElement);

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const keyLight = new THREE.DirectionalLight(0xfff8f0, 0.9);
keyLight.position.set(3, 5, 3);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x90b0ef, 0.4);
fillLight.position.set(-4, -1, -3);
scene.add(fillLight);
const rimLight = new THREE.DirectionalLight(0xffffff, 0.2);
rimLight.position.set(0, -4, -3);
scene.add(rimLight);

// ---------------------------------------------------------------------------
// OrbitControls
// ---------------------------------------------------------------------------
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.07;
controls.rotateSpeed   = 0.65;
controls.minDistance   = 1.2;
controls.maxDistance   = 9.0;

// ---------------------------------------------------------------------------
// View presets
// ---------------------------------------------------------------------------
const VIEWS = {
    anterior:  [0,  0.1,  3.1],
    posterior: [0,  0.1, -3.1],
    left:      [-3.1, 0.1, 0],
    right:     [3.1, 0.1, 0],
    superior:  [0,  3.1,  0.05],
    inferior:  [0, -3.1,  0.05],
};

function flyTo(preset) {
    const eye = VIEWS[preset];
    if (!eye) return;
    const from  = camera.position.clone();
    const fromT = controls.target.clone();
    const to    = new THREE.Vector3(...eye);
    const toT   = new THREE.Vector3(0, 0, 0);
    const t0 = performance.now(), dur = 550;
    (function tick() {
        const t = Math.min((performance.now() - t0) / dur, 1);
        const e = 1 - Math.pow(1 - t, 3);
        camera.position.lerpVectors(from, to, e);
        controls.target.lerpVectors(fromT, toT, e);
        controls.update();
        if (t < 1) requestAnimationFrame(tick);
    })();
}

document.querySelectorAll("[data-view]").forEach(b =>
    b.addEventListener("click", () => flyTo(b.dataset.view))
);

// ---------------------------------------------------------------------------
// Hemisphere toggle
// ---------------------------------------------------------------------------
document.querySelectorAll("[data-hemi]").forEach(b => {
    b.addEventListener("click", () => {
        document.querySelectorAll("[data-hemi]").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        const h = b.dataset.hemi;
        if (st.meshLH) st.meshLH.visible = (h === "both" || h === "left");
        if (st.meshRH) st.meshRH.visible = (h === "both" || h === "right");
    });
});

// ---------------------------------------------------------------------------
// Brain mesh — fsaverage5 GLB
// ---------------------------------------------------------------------------
const BASE = new THREE.Color(0xc8b4c8);

function addColorBuffer(geom) {
    const n = geom.attributes.position.count;
    const arr = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
        arr[i * 3]     = BASE.r;
        arr[i * 3 + 1] = BASE.g;
        arr[i * 3 + 2] = BASE.b;
    }
    geom.setAttribute("color", new THREE.BufferAttribute(arr, 3));
}

function brainMaterial() {
    if (isMobile) {
        // MeshLambertMaterial: no PBR cost, ~40% cheaper shader on mobile
        return new THREE.MeshLambertMaterial({ vertexColors: true });
    }
    return new THREE.MeshStandardMaterial({
        vertexColors: true,
        metalness: 0.04,
        roughness: 0.68,
        side: THREE.FrontSide,
    });
}

function centroidX(mesh) {
    const p = mesh.geometry.attributes.position.array;
    let s = 0;
    for (let i = 0; i < p.length; i += 3) s += p[i];
    return s / (p.length / 3);
}

async function loadBrainMesh() {
    const loader = new GLTFLoader();
    const gltf = await new Promise((ok, fail) =>
        loader.load("/assets/brain_fsaverage5.glb", ok, undefined, fail)
    );
    scene.add(gltf.scene);

    const meshes = [];
    gltf.scene.traverse(n => { if (n.isMesh) meshes.push(n); });
    if (!meshes.length) throw new Error("GLB contains no meshes");

    // centroid x < 0 → LH, > 0 → RH
    meshes.sort((a, b) => centroidX(a) - centroidX(b));

    for (const m of meshes) {
        const g = m.geometry.clone();
        addColorBuffer(g);
        m.geometry.dispose();
        m.geometry = g;
        m.material = brainMaterial();
    }

    st.meshes = meshes;
    if (meshes.length >= 2) {
        st.meshLH      = meshes[0];
        st.meshRH      = meshes[1];
        st.lhVertCount = meshes[0].geometry.attributes.position.count;
    } else {
        st.meshLH      = meshes[0];
        st.lhVertCount = Math.floor(meshes[0].geometry.attributes.position.count / 2);
    }
}

// ---------------------------------------------------------------------------
// BOLD painting — diverging colormap: blue ← white → red
// ---------------------------------------------------------------------------
function zToRGB(z) {
    const t = Math.max(-1, Math.min(1, z / 3.0));
    if (t >= 0) return [1, 1 - t * 0.627, 1 - t * 0.627];   // white → #ef5f5f
    const u = -t;
    return [1 - u * 0.627, 1 - u * 0.38, 1];                 // white → #5f9eef
}

function paintFrame(t) {
    if (!st.boldTrace || !st.vertexLabels) return;
    const row = st.boldTrace.bold[t];
    if (!row) return;
    timeLabel.textContent = `t = ${(t * st.boldTrace.tr_seconds).toFixed(1)} s`;

    // Mobile: skip expensive 20484-vertex CPU paint, use 2D canvas only
    if (isMobile) { drawMobileFrame(t); return; }

    const filterActive = st.activeNetworks.size > 0;

    for (const [mesh, offset] of [[st.meshLH, 0], [st.meshRH, st.lhVertCount]]) {
        if (!mesh) continue;
        const cb = mesh.geometry.attributes.color;
        if (!cb) continue;
        for (let i = 0; i < cb.count; i++) {
            const vi  = offset + i;
            const ri  = vi < st.vertexLabels.length ? st.vertexLabels[vi] : 0;
            let r = BASE.r, g = BASE.g, b = BASE.b;
            if (ri > 0) {
                const netKey = st.regionNetwork[ri - 1] ?? "";
                if (!filterActive || !netKey || st.activeNetworks.has(netKey)) {
                    const z = ri <= row.length ? row[ri - 1] : 0;
                    [r, g, b] = zToRGB(z);
                }
            }
            cb.setXYZ(i, r, g, b);
        }
        cb.needsUpdate = true;
    }
}

// ---------------------------------------------------------------------------
// Playback
// ---------------------------------------------------------------------------
scrubber.addEventListener("input", () => {
    if (st.boldTrace) paintFrame(+scrubber.value);
});

function setPlaying(on) {
    if (on) {
        if (st.playTimer) return;
        playBtn.textContent = "⏸";
        st.playTimer = setInterval(() => {
            if (!st.boldTrace) return;
            const v = (+scrubber.value + 1) % (+scrubber.max + 1);
            scrubber.value = v;
            paintFrame(v);
        }, 100);
    } else {
        clearInterval(st.playTimer);
        st.playTimer = null;
        playBtn.textContent = "▶";
    }
}
playBtn.addEventListener("click", () => setPlaying(!st.playTimer));

// ---------------------------------------------------------------------------
// Hover tooltip via raycasting against brain surface
// ---------------------------------------------------------------------------
const ray = new THREE.Raycaster();
const ndc = new THREE.Vector2();

renderer.domElement.addEventListener("mousemove", e => {
    const rect = renderer.domElement.getBoundingClientRect();
    ndc.x =  ((e.clientX - rect.left) / rect.width)  * 2 - 1;
    ndc.y = -((e.clientY - rect.top)  / rect.height) * 2 + 1;
    ray.setFromCamera(ndc, camera);

    const hits = ray.intersectObjects(st.meshes, false);
    if (!hits.length) { tooltip.style.display = "none"; return; }

    const hit  = hits[0];
    const face = hit.face;
    if (!face) { tooltip.style.display = "none"; return; }

    const isRH = hit.object === st.meshRH;
    const vi   = (isRH ? st.lhVertCount : 0) + face.a;
    const ri   = st.vertexLabels ? st.vertexLabels[vi] : 0;
    if (!ri) { tooltip.style.display = "none"; return; }

    const name     = st.regionNames[ri - 1] ?? `Region ${ri}`;
    const netKey   = st.regionNetwork[ri - 1] ?? "";
    const netLabel = YEO7[netKey]?.label ?? st.atlas?.networks?.[netKey]?.label ?? netKey ?? (isRH ? "Right hemisphere" : "Left hemisphere");

    let zStr = "", zColor = "";
    if (st.boldTrace) {
        const row = st.boldTrace.bold[+scrubber.value];
        if (row && ri <= row.length) {
            const z = row[ri - 1];
            zStr   = `z = ${z.toFixed(3)}`;
            zColor = z >= 0 ? "var(--positive)" : "var(--negative)";
        }
    }

    ttName.textContent = name.replace(/^7Networks_[LR]H_/, "");
    ttMeta.textContent = netLabel;
    ttZ.textContent    = zStr;
    ttZ.style.color    = zColor;
    tooltip.style.display = "block";
    tooltip.style.left    = `${e.clientX + 14}px`;
    tooltip.style.top     = `${e.clientY + 8}px`;
});
renderer.domElement.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });

// ---------------------------------------------------------------------------
// Network toggles — built from Schaefer-400 Yeo-7 keys in vertex_labels
// ---------------------------------------------------------------------------
const YEO7 = {
    Vis:         { label: "Visual",         color: "#7B5EA7" },
    SomMot:      { label: "Somatomotor",    color: "#5584C2" },
    DorsAttn:    { label: "Dorsal Attn",    color: "#3CB371" },
    SalVentAttn: { label: "Salience",       color: "#E08E45" },
    Limbic:      { label: "Limbic",         color: "#C2C25A" },
    Cont:        { label: "Control",        color: "#D4814E" },
    Default:     { label: "Default Mode",   color: "#CC4455" },
};

// ---------------------------------------------------------------------------
// Mobile 2D brain network canvas  (donut chart, Yeo-7 segments)
// ---------------------------------------------------------------------------
const _mbc = document.getElementById('mobile-brain-canvas');

function drawMobileFrame(t) {
    if (!_mbc) return;
    const ctx  = _mbc.getContext('2d');
    const dpr  = window.devicePixelRatio || 1;
    const cssW = _mbc.offsetWidth;
    const cssH = _mbc.offsetHeight;
    if (!cssW || !cssH) return;

    _mbc.width  = cssW * dpr;
    _mbc.height = cssH * dpr;
    ctx.scale(dpr, dpr);

    const w = cssW, h = cssH;
    ctx.clearRect(0, 0, w, h);

    // Compute per-network mean z from BOLD
    const netMeans = {};
    if (st.boldTrace && t !== undefined) {
        const row = st.boldTrace.bold[t];
        if (row) {
            const sums = {}, counts = {};
            const lim = Math.min(row.length, st.regionNetwork.length);
            for (let i = 0; i < lim; i++) {
                const net = st.regionNetwork[i];
                if (!net) continue;
                sums[net]   = (sums[net]   || 0) + row[i];
                counts[net] = (counts[net] || 0) + 1;
            }
            for (const net in sums) netMeans[net] = sums[net] / counts[net];
        }
    }

    const keys   = Object.keys(YEO7);
    const cx     = w / 2;
    const cy     = h * 0.50;
    const outerR = Math.min(w * 0.38, h * 0.72);
    const innerR = outerR * 0.44;
    const segAng = (2 * Math.PI) / keys.length;
    const gap    = 0.045;

    for (let i = 0; i < keys.length; i++) {
        const key   = keys[i];
        const net   = YEO7[key];
        const z     = netMeans[key] ?? 0;
        const sa    = i * segAng - Math.PI / 2 + gap / 2;
        const ea    = sa + segAng - gap;
        // Positive activation expands segment outward slightly
        const boost = st.boldTrace ? Math.max(0, z) * 0.13 : 0;
        const segR  = outerR * (1 + boost);
        const alpha = st.boldTrace ? Math.min(1, 0.28 + Math.abs(z) * 0.55) : 0.52;

        ctx.beginPath();
        ctx.arc(cx, cy, segR,   sa, ea);
        ctx.arc(cx, cy, innerR, ea, sa, true);
        ctx.closePath();
        ctx.fillStyle   = net.color;
        ctx.globalAlpha = alpha;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = 'rgba(13,14,20,0.55)';
        ctx.lineWidth   = 1.5;
        ctx.stroke();
    }

    // Centre label: time or "No scan"
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    if (st.boldTrace) {
        const sec = ((t ?? 0) * (st.boldTrace.tr_seconds ?? 0.5)).toFixed(1);
        ctx.font      = `bold 14px system-ui`;
        ctx.fillStyle = '#dde3f0';
        ctx.fillText(`${sec} s`, cx, cy - 7);
        ctx.font      = '9px system-ui';
        ctx.fillStyle = '#6b738f';
        ctx.fillText('brain time', cx, cy + 8);
    } else {
        ctx.font      = '11px system-ui';
        ctx.fillStyle = '#6b738f';
        ctx.fillText('Network map', cx, cy);
    }

    // Legend strip along the bottom
    const legY   = h - 5;
    const itemW  = w / keys.length;
    for (let i = 0; i < keys.length; i++) {
        const key = keys[i];
        const net = YEO7[key];
        const lx  = (i + 0.5) * itemW;
        ctx.beginPath();
        ctx.arc(lx, legY - 11, 3.5, 0, Math.PI * 2);
        ctx.fillStyle   = net.color;
        ctx.globalAlpha = 0.88;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.fillStyle    = '#6b738f';
        ctx.font         = '7px system-ui';
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'alphabetic';
        ctx.fillText(net.label.split(' ')[0].slice(0, 5), lx, legY);
    }
}

function buildNetworkToggles() {
    networkBar.innerHTML = "";
    // Prefer keys derived from parsed region names; fall back to atlas
    const keys = st.regionNetwork.length
        ? [...new Set(st.regionNetwork.filter(Boolean))]
        : Object.keys(st.atlas?.networks ?? {});
    if (!keys.length) return;

    for (const key of keys) {
        st.activeNetworks.add(key);
        const net   = YEO7[key] ?? { label: key, color: st.atlas?.networks?.[key]?.color ?? "#888" };
        const label = document.createElement("label");
        label.className = "net-toggle";
        label.style.color = net.color;
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.checked = true;
        cb.addEventListener("change", () => {
            cb.checked ? st.activeNetworks.add(key) : st.activeNetworks.delete(key);
            if (st.boldTrace) paintFrame(+scrubber.value);
        });
        const span = document.createElement("span");
        span.textContent = net.label;
        label.append(cb, span);
        networkBar.appendChild(label);
    }
}

// ---------------------------------------------------------------------------
// Right panel — narration + ROI breakdown
// ---------------------------------------------------------------------------
const TIER_MODELS = [
    "Gemma 4 E4B","Gemma 4 E4B","Gemma 4 E4B","Gemma 4 E4B",
    "Gemma 4 26B","Gemma 4 31B","Gemma 4 31B",
];

function renderNarration(result) {
    st.scanResult = result;
    narrationBody.innerHTML = "";
    narrationModel.textContent = TIER_MODELS[result.tier ?? st.tier] ?? "Gemma 4";

    if (result.status === "complete" && result.seconds_elapsed != null) {
        const tribeSec = result.seconds_elapsed || 0;
        const gemmaSec = st.narrationStartTime ? (Date.now() - st.narrationStartTime) / 1000 : 0;
        recordRun(result.id || st.scanId, st.lastFilename || 'unknown', tribeSec, gemmaSec);
        st.narrationStartTime = null;
    }

    if (result.peak_t != null || result.top_rois?.length) {
        const row = document.createElement("div");
        row.className = "stat-row";
        if (result.peak_t != null) {
            row.innerHTML += `<div class="stat"><div class="stat-val">${(result.peak_t * 0.5).toFixed(1)}s</div><div class="stat-key">Peak response</div></div>`;
        }
        if (result.top_rois?.length) {
            row.innerHTML += `<div class="stat"><div class="stat-val">${result.top_rois.length}</div><div class="stat-key">Top ROIs</div></div>`;
        }
        narrationBody.appendChild(row);
    }

    const p = document.createElement("p");
    p.className = result.narration ? "narration-text" : "narration-placeholder";
    p.textContent = result.narration ?? (
        result.status === "complete"   ? "No narration generated." :
        result.status === "narrating"  ? "Gemma is narrating…"     : "TRIBE v2 running…"
    );
    narrationBody.appendChild(p);

    if (result.top_rois?.length) {
        const sec = document.createElement("div");
        sec.className = "roi-section";
        sec.innerHTML = `<div class="roi-section-title">Top active regions</div>`;
        const n = Math.min(result.top_rois.length, 8);
        for (let i = 0; i < n; i++) {
            const roiId  = result.top_rois[i];
            const netKey = roiId.split("_")[2] ?? "";
            const color  = YEO7[netKey]?.color ?? st.atlas?.networks?.[netKey]?.color ?? "#888";
            const label  = roiId.replace(/^7Networks_[LR]H_/, "");
            const barPct = Math.round(90 - i * 8);
            sec.innerHTML += `
                <div class="roi-row">
                    <div class="roi-dot" style="background:${color}"></div>
                    <span class="roi-name" title="${esc(roiId)}">${esc(label)}</span>
                    <div class="roi-bar-wrap"><div class="roi-bar" style="width:${barPct}%"></div></div>
                </div>`;
        }
        narrationBody.appendChild(sec);
    }
}

// ---------------------------------------------------------------------------
// Tier pills
// ---------------------------------------------------------------------------
function setTierPill(tier) {
    document.querySelectorAll(".tier-pill").forEach(p =>
        p.classList.toggle("active", +p.dataset.tier === tier)
    );
    st.tier = tier;
    tierInput.value      = String(tier);
    tierDisplay.textContent = String(tier);
}

document.querySelectorAll(".tier-pill").forEach(btn => {
    btn.addEventListener("click", async () => {
        const tier = +btn.dataset.tier;
        setTierPill(tier);
        if (st.scanId && st.scanResult?.status === "complete") {
            narrationBody.innerHTML = `<p class="narration-placeholder">Requesting tier-${tier} narration…</p>`;
            try {
                const r = await fetch(`/api/scan/${st.scanId}/narrate?tier=${tier}`, { method: "POST" });
                if (!r.ok) throw new Error();
                const d = await r.json();
                renderNarration({ ...st.scanResult, narration: d.narration, tier });
            } catch {
                if (st.scanResult) renderNarration(st.scanResult);
            }
        }
    });
});

tierInput.addEventListener("input", () => setTierPill(+tierInput.value));

// ---------------------------------------------------------------------------
// Data loaders
// ---------------------------------------------------------------------------
async function loadBoldForScan(scanId) {
    try {
        const r = await fetch(`/api/scan/${encodeURIComponent(scanId)}/bold-simulate?n_t=100`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const trace = await r.json();
        st.boldTrace   = trace;
        scrubber.disabled = false;
        scrubber.max   = String(trace.n_t - 1);
        scrubber.value = "0";
        paintFrame(0);
        if (isMobile) drawMobileFrame(0);
        appendEvent(`BOLD: ${trace.n_t} TRs × ${trace.n_regions} regions`, "complete");
    } catch (err) {
        appendEvent(`BOLD load failed: ${err.message}`, "failed");
    }
}

async function loadScanResult(scanId) {
    try {
        const r = await fetch(`/api/scan/${encodeURIComponent(scanId)}`);
        if (r.ok) renderNarration(await r.json());
    } catch {}
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
let ws, reconnectDelay = 1000;

function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/ws`);
    ws.addEventListener("open", () => {
        setStatus("connected", "Connected");
        reconnectDelay = 1000;
    });
    ws.addEventListener("close", () => {
        setStatus("error", "Reconnecting…");
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    });
    ws.addEventListener("error", () => setStatus("error", "WS error"));
    ws.addEventListener("message", e => {
        try { onWs(JSON.parse(e.data)); } catch {}
    });
}

function onWs(msg) {
    switch (msg.type) {
        case "hello":
            appendEvent(`scheduler: ${msg.scheduler_state}, queue: ${msg.queue.queue_depth}`);
            setGpu(msg.scheduler_state);
            break;
        case "scheduler_state":
            setGpu(msg.state);
            appendEvent(`GPU → ${msg.state}`, msg.state === "tribe_active" ? "progress" : "info");
            break;
        case "scan_queued":
            appendEvent(`queued: ${msg.filename} (${msg.scan_id})`);
            showOverlay("queued");
            break;
        case "scan_progress":
            appendEvent(`${msg.scan_id}: ${msg.phase}`, "progress");
            if (msg.scan_id === st.scanId) showOverlay(msg.phase);
            break;
        case "scan_complete":
            appendEvent(`scan ${msg.scan_id} complete`, "complete");
            overlay.classList.add("hidden");
            loadBoldForScan(msg.scan_id);
            loadScanResult(msg.scan_id);
            break;
        case "scan_failed":
            appendEvent(`scan failed: ${msg.error?.message ?? "?"}`, "failed");
            overlay.classList.add("hidden");
            narrationBody.innerHTML = `<p class="narration-placeholder" style="color:var(--positive)">Scan failed.</p>`;
            break;
    }
}

function showOverlay(phase) {
    overlay.classList.remove("hidden");
    if (phase === "narrating") st.narrationStartTime = Date.now();
    const msgs = {
        queued:    "Queued — waiting for GPU…",
        running:   "TRIBE v2 running…<br><small style='opacity:.6'>Predicting cortical BOLD responses</small>",
        narrating: "Gemma narrating…<br><small style='opacity:.6'>Building interpretation</small>",
    };
    overlayInner.innerHTML = `
        <div style="font-size:28px;opacity:.4;margin-bottom:10px">⌬</div>
        <div style="text-align:center;font-size:13px">${msgs[phase] ?? phase}</div>`;
}

function setGpu(s) {
    const map = { idle:"GPU: idle", gemma_active:"GPU: Gemma ✓", tribe_active:"GPU: TRIBE ◆", swapping:"GPU: swapping…" };
    gpuBadge.textContent = map[s] ?? `GPU: ${s}`;
    gpuBadge.classList.toggle("active", s === "tribe_active");
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function setStatus(kind, msg) {
    statusDot.className  = `dot ${kind}`;
    statusEl.textContent = msg;
}

const _TS_FORMATS = [
    // cycle through these on each click
    (d) => d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"}),
    (d) => d.toLocaleDateString([], {weekday:"short", hour:"2-digit", minute:"2-digit"}),
    (d) => d.toLocaleDateString([], {month:"short", day:"numeric", year:"numeric", hour:"2-digit", minute:"2-digit"}),
    (d) => d.toLocaleDateString([], {weekday:"long", month:"long", day:"numeric", year:"numeric", hour:"2-digit", minute:"2-digit", second:"2-digit"}),
];

function appendEvent(message, kind = "info") {
    const now = new Date();
    const li  = document.createElement("li");
    li.className = `event-${kind}`;
    let fmtIdx = 0;
    const tsSpan = document.createElement("span");
    tsSpan.className = "event-ts";
    tsSpan.textContent = `[${_TS_FORMATS[0](now)}]`;
    tsSpan.title = "click to expand timestamp";
    tsSpan.addEventListener("click", (e) => {
        e.stopPropagation();
        fmtIdx = (fmtIdx + 1) % _TS_FORMATS.length;
        tsSpan.textContent = `[${_TS_FORMATS[fmtIdx](now)}]`;
    });
    const msgSpan = document.createElement("span");
    msgSpan.textContent = " " + message;
    li.append(tsSpan, msgSpan);
    eventLog.prepend(li);
    while (eventLog.childElementCount > 80) eventLog.removeChild(eventLog.lastChild);
}

function esc(s) {
    return String(s).replace(/[&<>"']/g,
        c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// ---------------------------------------------------------------------------
// Run cost tracking
// ---------------------------------------------------------------------------
const LOCAL_GPU_W       = 575;    // RTX 5090 TDP
const ELECTRICITY_RATE  = 0.15;   // USD per kWh
const CLOUD_A100_RATE   = 3.67;   // USD per hour for A100 on GCP
const CLOUD_TRIBE_S     = 30;     // estimated TRIBE inference seconds on cloud
const CLOUD_GEMMA_IN_K  = 0.6;    // input tokens in thousands
const CLOUD_GEMMA_OUT_K = 0.4;    // output tokens in thousands
const CLOUD_GEMMA_IN_C  = 0.035;  // cents per 1K input tokens (Gemma 27B on Vertex)
const CLOUD_GEMMA_OUT_C = 0.105;  // cents per 1K output tokens
const runHistory = [];

function localCostCents(totalSeconds) {
    return (LOCAL_GPU_W * totalSeconds / 3600 / 1000 * ELECTRICITY_RATE * 100);
}

function cloudCostCents(tribeSec) {
    const tribe  = (CLOUD_A100_RATE / 3600 * tribeSec) * 100;  // cents
    const gemma  = CLOUD_GEMMA_IN_K * CLOUD_GEMMA_IN_C + CLOUD_GEMMA_OUT_K * CLOUD_GEMMA_OUT_C;
    return tribe + gemma;
}

function recordRun(scanId, filename, tribeSec, gemmaSec) {
    const totalSec  = (tribeSec || 0) + (gemmaSec || 0);
    const localC    = localCostCents(totalSec);
    const cloudC    = cloudCostCents(tribeSec || CLOUD_TRIBE_S);
    runHistory.push({ scanId, filename, tribeSec, gemmaSec, localC, cloudC });
    updateCostTab();
    showRunCostBadge(localC, cloudC);
}

function updateCostTab() {
    const totalLocal = runHistory.reduce((s, r) => s + r.localC, 0);
    const totalCloud = runHistory.reduce((s, r) => s + r.cloudC, 0);
    const avgLocal   = runHistory.length ? totalLocal / runHistory.length : null;
    const avgCloud   = runHistory.length ? totalCloud / runHistory.length : null;

    const lcAvg = document.getElementById('lc-avg');
    const lcTotal = document.getElementById('lc-total');
    const ccAvg = document.getElementById('cc-avg');
    const ccTotal = document.getElementById('cc-total');
    if (lcAvg)   lcAvg.textContent   = avgLocal  != null ? avgLocal.toFixed(3)  + ' ¢' : '—';
    if (lcTotal) lcTotal.textContent = totalLocal > 0    ? totalLocal.toFixed(3) + ' ¢' : '—';
    if (ccAvg)   ccAvg.textContent   = avgCloud   != null ? avgCloud.toFixed(2)  + ' ¢' : '—';
    if (ccTotal) ccTotal.textContent = totalCloud > 0    ? totalCloud.toFixed(2) + ' ¢' : '—';

    const lbody = document.getElementById('local-history-body');
    const cbody = document.getElementById('cloud-history-body');
    if (lbody) {
        lbody.innerHTML = runHistory.slice(-10).reverse().map((r, i) => `
            <tr>
                <td>#${runHistory.length - i}</td>
                <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.filename)}</td>
                <td>${r.tribeSec ? r.tribeSec.toFixed(1) : '—'}</td>
                <td>${r.gemmaSec ? r.gemmaSec.toFixed(1) : '—'}</td>
                <td style="color:var(--good);font-weight:600">${r.localC.toFixed(3)}</td>
            </tr>`).join('');
    }
    if (cbody) {
        cbody.innerHTML = runHistory.slice(-10).reverse().map((r, i) => `
            <tr>
                <td>#${runHistory.length - i}</td>
                <td style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.filename)}</td>
                <td style="color:var(--good)">${r.localC.toFixed(3)}</td>
                <td style="color:var(--accent)">${r.cloudC.toFixed(2)}</td>
                <td style="color:var(--muted)">${r.localC < r.cloudC ? (r.cloudC - r.localC).toFixed(2) + ' ¢ saved' : '—'}</td>
            </tr>`).join('');
    }
}

function showRunCostBadge(localC, cloudC) {
    const badge = document.getElementById('run-cost-badge');
    if (!badge) return;
    badge.className = 'run-cost-badge';
    badge.innerHTML = `
        <span>Last run:</span>
        <span class="cost-local-val">⚡ ${localC.toFixed(3)} ¢ local</span>
        <span style="color:var(--muted)">vs</span>
        <span class="cost-cloud-val">☁ ${cloudC.toFixed(2)} ¢ cloud</span>`;
}

// ---------------------------------------------------------------------------
// Shared media submitter — used by file / camera / voice modes
// ---------------------------------------------------------------------------
async function submitMediaFile(file, { btnEl, resetLabel } = {}) {
    if (btnEl) { btnEl.disabled = true; btnEl.textContent = "Submitting…"; }
    narrationBody.innerHTML = `<p class="narration-placeholder">Scan accepted — awaiting results…</p>`;

    const fd = new FormData();
    fd.append("file",   file);
    fd.append("tier",   tierInput.value);
    fd.append("source", "webui");

    try {
        const resp = await fetch("/api/scan", { method: "POST", body: fd });
        const body = await resp.json();
        if (!resp.ok) {
            appendEvent(`rejected: ${body.message ?? body.error_code}`, "failed");
        } else {
            st.scanId = body.scan_id;
            st.lastFilename = file.name;
            appendEvent(`accepted: ${body.scan_id}`, "complete");
            showOverlay("queued");
        }
    } catch (err) {
        appendEvent(`network error: ${err.message}`, "failed");
    } finally {
        if (btnEl) { btnEl.disabled = false; btnEl.textContent = resetLabel ?? "Analyze"; }
    }
}

// ---------------------------------------------------------------------------
// File upload form
// ---------------------------------------------------------------------------
document.getElementById("scan-form").addEventListener("submit", async e => {
    e.preventDefault();
    const fileInput = document.getElementById("scan-file");
    if (!fileInput.files?.length) return;
    await submitMediaFile(fileInput.files[0], { btnEl: submitBtn, resetLabel: "Analyze" });
});

// ---------------------------------------------------------------------------
// Input mode tabs
// ---------------------------------------------------------------------------
const modeTabs   = document.querySelectorAll(".mode-tab");
const modePanels = document.querySelectorAll(".mode-panel");

function switchMode(mode) {
    modeTabs.forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
    modePanels.forEach(p => p.classList.toggle("active", p.id === `mode-${mode}`));
    if (mode !== "camera") stopCamera();
    if (mode !== "voice")  stopVoice();
}

modeTabs.forEach(b => b.addEventListener("click", () => switchMode(b.dataset.mode)));

// ---------------------------------------------------------------------------
// Camera mode
// ---------------------------------------------------------------------------
let _camStream  = null;
let _camBlob    = null;

const camVideo      = document.getElementById("camera-video");
const camCanvas     = document.getElementById("camera-canvas");
const camOpenBtn    = document.getElementById("camera-open-btn");
const camCaptureBtn = document.getElementById("camera-capture-btn");
const camRetakeBtn  = document.getElementById("camera-retake-btn");
const camSubmitBtn  = document.getElementById("camera-submit-btn");

async function openCamera() {
    if (_camStream) return;
    try {
        _camStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 960 } },
        });
        camVideo.srcObject = _camStream;
        camOpenBtn.hidden    = true;
        camCaptureBtn.hidden = false;
        appendEvent("camera open");
    } catch (err) {
        appendEvent(`camera denied: ${err.message}`, "failed");
    }
}

function stopCamera() {
    if (!_camStream) return;
    _camStream.getTracks().forEach(t => t.stop());
    _camStream = null;
    camVideo.srcObject = null;
    _camBlob         = null;
    camOpenBtn.hidden    = false;
    camCaptureBtn.hidden = true;
    camRetakeBtn.hidden  = true;
    camSubmitBtn.hidden  = true;
    camVideo.hidden  = false;
    camCanvas.hidden = true;
}

function capturePhoto() {
    const w = camVideo.videoWidth, h = camVideo.videoHeight;
    camCanvas.width = w; camCanvas.height = h;
    camCanvas.getContext("2d").drawImage(camVideo, 0, 0, w, h);
    camCanvas.toBlob(blob => {
        _camBlob = blob;
        camVideo.hidden      = true;
        camCanvas.hidden     = false;
        camCaptureBtn.hidden = true;
        camRetakeBtn.hidden  = false;
        camSubmitBtn.hidden  = false;
    }, "image/jpeg", 0.92);
    // Release camera after capture (turns off the LED)
    _camStream?.getTracks().forEach(t => t.stop());
    _camStream = null;
}

camOpenBtn?.addEventListener("click", openCamera);
camCaptureBtn?.addEventListener("click", capturePhoto);
camRetakeBtn?.addEventListener("click", () => {
    camCanvas.hidden     = true;
    camVideo.hidden      = false;
    camRetakeBtn.hidden  = true;
    camSubmitBtn.hidden  = true;
    _camBlob = null;
    openCamera();
});
camSubmitBtn?.addEventListener("click", () => {
    if (!_camBlob) return;
    submitMediaFile(
        new File([_camBlob], "capture.jpg", { type: "image/jpeg" }),
        { btnEl: camSubmitBtn, resetLabel: "Analyze photo" },
    );
});

// ---------------------------------------------------------------------------
// Voice recording mode
// ---------------------------------------------------------------------------
let _voiceStream   = null;
let _voiceRecorder = null;
let _voiceChunks   = [];
let _voiceBlob     = null;
let _voiceTimer    = null;
let _voiceSecs     = 0;
let _voiceAudioCtx = null;
let _voiceAnalyser = null;
let _voiceVisRaf   = null;

const voiceVis       = document.getElementById("voice-vis");
const voiceStateEl   = document.getElementById("voice-state-label");
const voiceTimerEl   = document.getElementById("voice-timer");
const voiceRecordBtn = document.getElementById("voice-record-btn");
const voiceStopBtn   = document.getElementById("voice-stop-btn");
const voiceSubmitBtn = document.getElementById("voice-submit-btn");

function fmtTime(s) { return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }

async function startVoice() {
    _voiceChunks = [];
    _voiceBlob   = null;
    voiceSubmitBtn.hidden = true;

    try {
        _voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
        appendEvent(`mic denied: ${err.message}`, "failed");
        return;
    }

    // Frequency analyser for visualizer
    _voiceAudioCtx = new AudioContext();
    const src = _voiceAudioCtx.createMediaStreamSource(_voiceStream);
    _voiceAnalyser = _voiceAudioCtx.createAnalyser();
    _voiceAnalyser.fftSize = 64;
    src.connect(_voiceAnalyser);

    // Pick best supported MIME type
    const mime = ["audio/webm;codecs=opus","audio/webm","audio/ogg;codecs=opus","audio/mp4"]
        .find(t => MediaRecorder.isTypeSupported(t));
    _voiceRecorder = new MediaRecorder(_voiceStream, mime ? { mimeType: mime } : undefined);
    _voiceRecorder.ondataavailable = e => { if (e.data.size > 0) _voiceChunks.push(e.data); };
    _voiceRecorder.onstop = () => {
        _voiceBlob = new Blob(_voiceChunks, { type: _voiceRecorder.mimeType || "audio/webm" });
        voiceStateEl.textContent  = "Recording ready";
        voiceSubmitBtn.hidden     = false;
        _drawVoiceFlat();
    };
    _voiceRecorder.start(250);

    voiceStateEl.textContent  = "Recording…";
    voiceRecordBtn.hidden     = true;
    voiceStopBtn.hidden       = false;

    _voiceSecs = 0;
    voiceTimerEl.textContent  = "0:00";
    _voiceTimer = setInterval(() => {
        voiceTimerEl.textContent = fmtTime(++_voiceSecs);
        if (_voiceSecs >= 30) stopVoice(); // hard cap at 30 s (Whisper window)
    }, 1000);

    _startVoiceViz();
}

function stopVoice() {
    clearInterval(_voiceTimer);
    cancelAnimationFrame(_voiceVisRaf);
    _voiceStream?.getTracks().forEach(t => t.stop());
    if (_voiceRecorder?.state === "recording") _voiceRecorder.stop();
    voiceRecordBtn.hidden = false;
    voiceStopBtn.hidden   = true;
    if (_voiceRecorder?.state !== "inactive") voiceStateEl.textContent = "Processing…";
}

function _startVoiceViz() {
    const cvs = voiceVis;
    const ctx = cvs.getContext("2d");
    const buf = new Uint8Array(_voiceAnalyser.frequencyBinCount);
    function draw() {
        _voiceVisRaf = requestAnimationFrame(draw);
        _voiceAnalyser.getByteFrequencyData(buf);
        ctx.clearRect(0, 0, cvs.width, cvs.height);
        const bw = cvs.width / buf.length;
        for (let i = 0; i < buf.length; i++) {
            const h = (buf[i] / 255) * cvs.height;
            ctx.fillStyle = `hsl(${220 + i * 1.5}, 65%, 58%)`;
            ctx.fillRect(i * bw, cvs.height - h, Math.max(bw - 1, 1), h);
        }
    }
    draw();
}

function _drawVoiceFlat() {
    const cvs = voiceVis;
    const ctx = cvs.getContext("2d");
    ctx.clearRect(0, 0, cvs.width, cvs.height);
    ctx.fillStyle = "rgba(91,141,239,0.25)";
    ctx.fillRect(0, cvs.height / 2 - 1, cvs.width, 2);
}

voiceRecordBtn?.addEventListener("click", startVoice);
voiceStopBtn?.addEventListener("click", stopVoice);
voiceSubmitBtn?.addEventListener("click", () => {
    if (!_voiceBlob) return;
    const mtype = _voiceBlob.type || "audio/webm";
    const ext   = mtype.includes("ogg") ? ".ogg" : mtype.includes("mp4") ? ".m4a" : ".webm";
    submitMediaFile(
        new File([_voiceBlob], `recording${ext}`, { type: mtype }),
        { btnEl: voiceSubmitBtn, resetLabel: "Analyze recording" },
    );
});

// ---------------------------------------------------------------------------
// Text input mode
// ---------------------------------------------------------------------------
const textInput     = document.getElementById("text-input");
const textSubmitBtn = document.getElementById("text-submit-btn");

textSubmitBtn?.addEventListener("click", async () => {
    const text = textInput?.value.trim();
    if (!text) { appendEvent("enter some text first", "failed"); return; }

    textSubmitBtn.disabled     = true;
    textSubmitBtn.textContent  = "Submitting…";
    narrationBody.innerHTML    = `<p class="narration-placeholder">Analyzing text stimulus…</p>`;

    const fd = new FormData();
    fd.append("text",   text);
    fd.append("tier",   tierInput.value);
    fd.append("source", "webui");

    try {
        const resp = await fetch("/api/text-scan", { method: "POST", body: fd });
        const body = await resp.json();
        if (!resp.ok) {
            appendEvent(`rejected: ${body.message ?? body.detail ?? "error"}`, "failed");
        } else {
            st.scanId = body.scan_id;
            appendEvent(`text scan accepted: ${body.scan_id}`, "complete");
            showOverlay("narrating"); // text skips TRIBE, goes straight to Gemma
        }
    } catch (err) {
        appendEvent(`error: ${err.message}`, "failed");
    } finally {
        textSubmitBtn.disabled    = false;
        textSubmitBtn.textContent = "Analyze text";
    }
});

// ---------------------------------------------------------------------------
// Resize + render loop
// ---------------------------------------------------------------------------
function resize() {
    const w = root.clientWidth || 400, h = root.clientHeight || 400;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);

function animate() {
    requestAnimationFrame(animate);
    controls.update();
    if (!isMobile) renderer.render(scene, camera);
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
(async () => {
    resize();
    animate();
    connect();

    // 1. Vertex label map
    try {
        const r = await fetch("/assets/vertex_labels.json");
        if (r.ok) {
            const data = await r.json();
            const verts     = data.vertex_labels ?? (Array.isArray(data) ? data : []);
            const allLabels = data.labels ?? [];
            st.vertexLabels  = new Int32Array(verts);
            st.regionNames   = allLabels.slice(1);   // strip "Background" at index 0
            st.regionNetwork = st.regionNames.map(n => n.split("_")[2] ?? "");
            // Pre-populate activeNetworks from Schaefer keys so painting works before atlas loads
            for (const k of new Set(st.regionNetwork.filter(Boolean))) st.activeNetworks.add(k);
            buildNetworkToggles();
            appendEvent(`vertex map: ${verts.length} verts, ${st.regionNames.length} regions`);
        }
    } catch (err) {
        appendEvent(`vertex labels: ${err.message}`, "failed");
    }

    // 2. Atlas (network colours + toggle labels)
    try {
        const r = await fetch("/api/atlas");
        if (r.ok) {
            st.atlas = await r.json();
            buildNetworkToggles();
            appendEvent(`atlas: ${st.atlas.regions.length} ROIs`);
        }
    } catch {}

    // 3. Brain GLB
    try {
        appendEvent("loading brain mesh…");
        await loadBrainMesh();
        appendEvent("brain mesh ready", "complete");
    } catch (err) {
        appendEvent(`brain GLB: ${err.message}`, "failed");
    }

    overlayInner.innerHTML = `
        <div style="font-size:32px;opacity:.4;margin-bottom:10px">⌬</div>
        <div>Submit a media file to begin.<br>
             <span style="font-size:12px;opacity:.6">TRIBE v2 predicts cortical BOLD. Gemma narrates.</span></div>`;
    overlay.classList.remove("hidden");

    // Mobile: default to cloud tab, draw empty network canvas
    if (isMobile) {
        const cloudTab = document.querySelector('[data-pipeline="cloud"]');
        if (cloudTab) {
            pipelineTabs.forEach(b => b.classList.toggle('active', b === cloudTab));
            currentPipeline = 'cloud';
            pipelineBadge.className   = 'pipeline-badge cloud';
            pipelineBadge.textContent = '☁ Cloud · Vertex AI';
        }
        drawMobileFrame(undefined);
    }
})();

window.loadBoldForScan = loadBoldForScan;
window.loadScanResult = loadScanResult;

// URL param: ?scan=<id> auto-loads that scan on page load.
// Used by the public showcase + share links so a specific completed scan can be linked.
(function autoLoadFromUrl() {
    try {
        const params = new URLSearchParams(window.location.search);
        const scanId = params.get("scan");
        if (!scanId) return;
        // wait one frame so the WS + initial fetches settle, then load the scan
        setTimeout(() => {
            try { loadScanResult(scanId); } catch (e) { console.warn("loadScanResult failed:", e); }
            try { loadBoldForScan(scanId); } catch (e) { console.warn("loadBoldForScan failed:", e); }
        }, 250);
    } catch (e) { /* no-op */ }
})();
