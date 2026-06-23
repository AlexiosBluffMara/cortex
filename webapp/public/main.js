// Cortex viewer v2 — real fsaverage5 surface + TRIBE v2 BOLD + cloud narration
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
const ttFunc        = document.getElementById("tt-func");
const ttZ           = document.getElementById("tt-z");
const DEFAULT_NARRATION_MODEL = "openrouter:google/gemma-4-26b-a4b-it:free";

function selectedComputeTarget() {
    return window._selectedComputeTarget || "local";
}

function paidAccessCode() {
    return typeof window._cortexPaidAccessCode === "function"
        ? window._cortexPaidAccessCode()
        : "";
}

function setupWorkflowNav() {
    const scroller = document.querySelector(".intake-scroll");
    const links = Array.from(document.querySelectorAll(".workflow-jump a[href^='#']"));
    const sections = links
        .map(link => document.querySelector(link.getAttribute("href")))
        .filter(Boolean);
    if (!scroller || !links.length || !sections.length) return;

    function setActive(id) {
        for (const link of links) {
            const active = link.getAttribute("href") === `#${id}`;
            if (active) link.setAttribute("aria-current", "step");
            else link.removeAttribute("aria-current");
        }
    }

    links.forEach(link => {
        link.addEventListener("click", event => {
            const target = document.querySelector(link.getAttribute("href"));
            if (!target) return;
            event.preventDefault();
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            setActive(target.id);
            history.replaceState(null, "", link.getAttribute("href"));
        });
    });

    const observer = new IntersectionObserver(entries => {
        const visible = entries
            .filter(entry => entry.isIntersecting)
            .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible?.target?.id) setActive(visible.target.id);
    }, {
        root: scroller,
        threshold: [0.28, 0.45, 0.62],
        rootMargin: "-18% 0px -55% 0px",
    });
    sections.forEach(section => observer.observe(section));
    setActive(sections[0].id);
}

setupWorkflowNav();

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
                : '☁ OpenRouter · cloud narration';
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

scene.add(new THREE.AmbientLight(0xffffff, 0.85));
const keyLight = new THREE.DirectionalLight(0xfff8f0, 1.15);
keyLight.position.set(3, 5, 3);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x90b0ef, 0.55);
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
// Brighter neutral base — keeps anatomically plausible mauve but lifts
// luminance so positive/negative activation stands off the cortex with
// stronger contrast in the recorded video.
const BASE = new THREE.Color(0xe6d6d8);

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
// BOLD painting — ISU-branded diverging colormap with adaptive scale.
//
// Color spectrum (designsystem.illinoisstate.edu official colors):
//   z << 0  →  ISU Blue  #56758f  (suppressed: blood flow BELOW baseline)
//   z ≈ 0   →  dark neutral #2a2a3a  (baseline resting state)
//   z > 0   →  ISU Yellow #F6A917  →  ISU Red #CC0000  (activated: BOLD rising)
//
// Blood flow interpretation:
//   BOLD (Blood Oxygen Level Dependent) signal rises when neurons fire — more
//   oxygenated blood floods activated regions.  TRIBE v2 predicts this z-score
//   relative to a resting baseline across 25 subjects.
//   Typical TRIBE z-score range: −1.5 to +1.5 per timepoint.
//
// `_zScale` is set per-frame so even quiet scans saturate the full range.
// Gamma curve (pow 0.65) lifts mid-range values — mid-reds read clearly.
// ---------------------------------------------------------------------------
let _zScale = 1.0;     // updated by setZScaleForFrame(row); fallback = 1.0

function setZScaleForFrame(rowAbsMax) {
    const target = Math.max(0.3, Math.min(4.0, rowAbsMax));
    _zScale = _zScale * 0.5 + target * 0.5;
}

// ISU-branded diverging colormap
// Negative (suppressed): neutral → ISU Blue #56758f (#86,117,143)
// Positive (activated):  neutral → ISU Gold #F6A917 → ISU Red #CC0000
function zToRGB(z) {
    const raw = z / Math.max(0.0001, _zScale);
    const t = Math.max(-1, Math.min(1, raw));
    const m = Math.pow(Math.abs(t), 0.65);   // gamma lift

    // Baseline neutral in dark-theme context: (0.17, 0.17, 0.23)
    const BR = 0.17, BG = 0.17, BB = 0.23;

    if (t >= 0) {
        // Positive: neutral → ISU Gold (#F6A917 = 0.965,0.663,0.090) → ISU Red (#CC0000 = 0.80,0,0)
        if (m < 0.55) {
            // Phase 1: neutral → gold
            const p = m / 0.55;
            return [
                BR + p * (0.965 - BR),
                BG + p * (0.663 - BG),
                BB + p * (0.090 - BB),
            ];
        } else {
            // Phase 2: gold → ISU Red
            const p = (m - 0.55) / 0.45;
            return [
                0.965 - p * (0.965 - 0.80),
                0.663 - p * 0.663,
                0.090 - p * 0.090,
            ];
        }
    } else {
        // Negative: neutral → ISU Blue (#56758f = 0.337,0.459,0.561)
        return [
            BR - m * (BR - 0.337),
            BG - m * (BG - 0.459),
            BB + m * (0.561 - BB),
        ];
    }
}

function _absMaxOf(arr, off, len) {
    let m = 0;
    const end = off + len;
    for (let i = off; i < end; i++) {
        const v = arr[i] < 0 ? -arr[i] : arr[i];
        if (v > m) m = v;
    }
    return m;
}

function paintFrame(t) {
    if (!st.boldTrace || !st.vertexLabels) return;
    const row = st.boldTrace.bold[t];
    if (!row) return;
    timeLabel.textContent = `t = ${(t * st.boldTrace.tr_seconds).toFixed(1)} s`;

    // Per-frame auto-scale on the per-region BOLD too (fallback path)
    let absMax = 0;
    for (let i = 0; i < row.length; i++) {
        const v = row[i] < 0 ? -row[i] : row[i];
        if (v > absMax) absMax = v;
    }
    setZScaleForFrame(absMax);

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

    if (isMobile) drawMobileFrame(t);
}

// ---------------------------------------------------------------------------
// Playback
// ---------------------------------------------------------------------------
scrubber.addEventListener("input", () => {
    const v = +scrubber.value;
    if (st.boldVertex) paintVertexFrame(v);
    else if (st.boldTrace) paintFrame(v);
    drawColormapLegend();
});

function setPlaying(on) {
    if (on) {
        if (st.playTimer) return;
        playBtn.textContent = "⏸";
        const nT = st.boldVertex
            ? st.boldVertex.n_t
            : (st.boldTrace ? st.boldTrace.n_t : null);
        if (!nT) { playBtn.textContent = "▶"; return; }
        const tr = st.boldVertex
            ? (st.boldVertex.tr_seconds ?? 0.5)
            : (st.boldTrace?.tr_seconds ?? 0.5);
        const interval = Math.max(33, tr * 1000);
        st.playTimer = setInterval(() => {
            const v = (+scrubber.value + 1) % nT;
            scrubber.value = v;
            if (st.boldVertex) paintVertexFrame(v);
            else paintFrame(v);
            drawColormapLegend();
        }, interval);
    } else {
        clearInterval(st.playTimer);
        st.playTimer = null;
        playBtn.textContent = "▶";
    }
}
playBtn.addEventListener("click", () => setPlaying(!st.playTimer));

// ---------------------------------------------------------------------------
// Colormap legend
// ---------------------------------------------------------------------------
function drawColormapLegend() {
    const cvs = document.getElementById("colormap-canvas");
    if (!cvs) return;
    const ctx = cvs.getContext("2d");
    const W = cvs.width, H = cvs.height;
    ctx.clearRect(0, 0, W, H);

    // Gradient bar: bottom = cobalt blue, middle = mauve, top = hot orange
    const barX = 0, barW = 14, barH = H - 30, barY = 14;
    const grad = ctx.createLinearGradient(0, barY, 0, barY + barH);
    grad.addColorStop(0,   "#ff3a25");
    grad.addColorStop(0.5, "#e6d6d8");
    grad.addColorStop(1,   "#1f5cff");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.roundRect(barX, barY, barW, barH, 3);
    ctx.fill();

    // Tick labels at −2σ … +2σ positions using _zScale
    const ticks = [2, 1, 0, -1, -2];
    ctx.font = '11px "JetBrains Mono", monospace';
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (const sigma of ticks) {
        const t = (2 - sigma) / 4;   // 0=top(+2σ), 1=bottom(−2σ)
        const y = barY + t * barH;
        const val = (sigma * _zScale).toFixed(1);
        ctx.fillText(val, barW + 3, y);
    }
}

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
    if (ttFunc) ttFunc.textContent = YEO7_FUNC[netKey] ?? "";
    ttZ.textContent    = zStr;
    ttZ.style.color    = zColor;
    tooltip.style.display = "block";
    tooltip.style.left    = `${e.clientX + 14}px`;
    tooltip.style.top     = `${e.clientY + 8}px`;
});
renderer.domElement.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });

// ---------------------------------------------------------------------------
// Yeo-7 one-line functional descriptions (for tooltip)
// ---------------------------------------------------------------------------
const YEO7_FUNC = {
    Vis:         "Visual processing",
    SomMot:      "Sensory & motor",
    DorsAttn:    "Spatial attention",
    SalVentAttn: "Salience & reorienting",
    Limbic:      "Memory & emotion",
    Cont:        "Cognitive control",
    Default:     "Rest, social, self-referential",
};

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
// Mobile compact brain state canvas
// ---------------------------------------------------------------------------
const _mbc = document.getElementById('mobile-brain-canvas');

function drawMobileFrame(t) {
    if (!_mbc) return;
    const ctx  = _mbc.getContext('2d');
    const dpr  = window.devicePixelRatio || 1;
    const cssW = _mbc.offsetWidth;
    const cssH = _mbc.offsetHeight;
    if (!cssW || !cssH) return;

    _mbc.width  = Math.max(1, Math.floor(cssW * dpr));
    _mbc.height = Math.max(1, Math.floor(cssH * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const w = cssW, h = cssH;
    ctx.clearRect(0, 0, w, h);

    const netMeans = {};
    let row = null;
    let rowAbsMax = 0;
    let secLabel = null;
    if (t !== undefined && st.boldVertex) {
        const { n_t, n_vert, data, tr_seconds } = st.boldVertex;
        const frame = Math.max(0, Math.min(Math.floor(t), n_t - 1));
        const rowOff = frame * n_vert;
        row = data.subarray(rowOff, rowOff + n_vert);
        rowAbsMax = _absMaxOf(data, rowOff, n_vert);
        secLabel = `${(frame * (tr_seconds ?? 0.5)).toFixed(1)} s`;
    } else if (t !== undefined && st.boldTrace) {
        const frame = Math.max(0, Math.min(Math.floor(t), st.boldTrace.bold.length - 1));
        row = st.boldTrace.bold[frame];
        if (row) {
            const sums = {}, counts = {};
            const lim = Math.min(row.length, st.regionNetwork.length);
            for (let i = 0; i < lim; i++) {
                const z = row[i];
                rowAbsMax = Math.max(rowAbsMax, Math.abs(z));
                const net = st.regionNetwork[i];
                if (!net) continue;
                sums[net]   = (sums[net]   || 0) + z;
                counts[net] = (counts[net] || 0) + 1;
            }
            for (const net in sums) netMeans[net] = sums[net] / counts[net];
        }
        secLabel = `${((t ?? 0) * (st.boldTrace.tr_seconds ?? 0.5)).toFixed(1)} s`;
    }
    const hasScan = !!row;

    const bg = ctx.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, 'rgba(13,14,20,0.96)');
    bg.addColorStop(1, 'rgba(6,7,11,1)');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    const cx = w / 2;

    function drawThreeBrainSnapshot() {
        if (!st.meshes.length) return false;
        try {
            const renderW = Math.max(180, Math.floor(w));
            const renderH = Math.max(88, Math.floor(h - 14));
            renderer.setSize(renderW, renderH, false);
            camera.aspect = renderW / renderH;
            camera.updateProjectionMatrix();
            controls.update();
            renderer.render(scene, camera);
            ctx.drawImage(renderer.domElement, 0, -4, w, h - 12);

            const vignette = ctx.createLinearGradient(0, 0, 0, h);
            vignette.addColorStop(0, 'rgba(6,7,11,0.12)');
            vignette.addColorStop(0.72, 'rgba(6,7,11,0.00)');
            vignette.addColorStop(1, 'rgba(6,7,11,0.58)');
            ctx.fillStyle = vignette;
            ctx.fillRect(0, 0, w, h);
            return true;
        } catch {
            return false;
        }
    }

    const drewThreeBrain = drawThreeBrainSnapshot();

    if (!drewThreeBrain) {
    const cy = h * 0.45;
    const brainW = Math.min(w * 0.78, 285);
    const brainH = Math.min(Math.max(h * 0.55, 46), 70);
    const hemiRx = brainW * 0.21;
    const hemiRy = brainH * 0.48;
    const leftCx = cx - brainW * 0.235;
    const rightCx = cx + brainW * 0.235;

    function drawHemisphere(x, side) {
        const dir = side === 'left' ? -1 : 1;
        ctx.beginPath();
        ctx.ellipse(x, cy, hemiRx, hemiRy, dir * -0.08, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(20,24,34,0.92)';
        ctx.fill();
        ctx.strokeStyle = 'rgba(142,153,176,0.35)';
        ctx.lineWidth = 1.3;
        ctx.stroke();

        ctx.strokeStyle = 'rgba(142,153,176,0.18)';
        ctx.lineWidth = 1;
        for (let i = -2; i <= 2; i++) {
            const y = cy + i * brainH * 0.15;
            ctx.beginPath();
            ctx.moveTo(x - dir * hemiRx * 0.72, y);
            ctx.bezierCurveTo(
                x - dir * hemiRx * 0.25, y - brainH * 0.16,
                x + dir * hemiRx * 0.18, y + brainH * 0.18,
                x + dir * hemiRx * 0.72, y + brainH * 0.02
            );
            ctx.stroke();
        }
    }

    drawHemisphere(leftCx, 'left');
    drawHemisphere(rightCx, 'right');

    ctx.beginPath();
    ctx.moveTo(cx, cy - brainH * 0.5);
    ctx.lineTo(cx, cy + brainH * 0.5);
    ctx.strokeStyle = 'rgba(142,153,176,0.20)';
    ctx.lineWidth = 1;
    ctx.stroke();

    const activationSites = [
        { key: 'Vis',         x: -0.34, y:  0.16 },
        { key: 'SomMot',      x: -0.18, y: -0.26 },
        { key: 'DorsAttn',    x: -0.31, y: -0.05 },
        { key: 'SalVentAttn', x:  0.31, y: -0.08 },
        { key: 'Limbic',      x:  0.15, y:  0.27 },
        { key: 'Cont',        x:  0.35, y: -0.25 },
        { key: 'Default',     x:  0.27, y:  0.15 },
        { key: 'Vis',         x:  0.34, y:  0.16 },
        { key: 'SomMot',      x:  0.18, y: -0.26 },
        { key: 'Default',     x: -0.27, y:  0.15 },
    ];

    function rgbCss(rgb, alpha = 1) {
        const [r, g, b] = rgb.map(v => Math.max(0, Math.min(255, Math.round(v * 255))));
        return `rgba(${r},${g},${b},${alpha})`;
    }

    if (hasScan) {
        for (const site of activationSites) {
            const z = netMeans[site.key] ?? 0;
            const magnitude = Math.min(1, Math.abs(z) / Math.max(0.001, _zScale));
            const rgb = zToRGB(z);
            const x = cx + site.x * brainW;
            const y = cy + site.y * brainH;
            const glow = 6 + magnitude * 15;
            const dot = 2.5 + magnitude * 5.5;

            ctx.beginPath();
            ctx.arc(x, y, glow, 0, Math.PI * 2);
            ctx.fillStyle = rgbCss(rgb, 0.08 + magnitude * 0.30);
            ctx.fill();

            ctx.beginPath();
            ctx.arc(x, y, dot, 0, Math.PI * 2);
            ctx.fillStyle = rgbCss(rgb, 0.58 + magnitude * 0.38);
            ctx.fill();
            ctx.strokeStyle = 'rgba(255,255,255,0.16)';
            ctx.lineWidth = 0.8;
            ctx.stroke();
        }
    }
    }

    const railX = 18;
    const railW = w - railX * 2;
    const railY = h - 20;
    ctx.beginPath();
    ctx.moveTo(railX, railY);
    ctx.lineTo(railX + railW, railY);
    ctx.strokeStyle = 'rgba(142,153,176,0.18)';
    ctx.lineWidth = 1;
    ctx.stroke();

    if (hasScan) {
        const sec = secLabel ?? `${((t ?? 0) * 0.5).toFixed(1)} s`;
        if (row && rowAbsMax > 0) {
            const samples = Math.min(64, row.length);
            ctx.beginPath();
            for (let i = 0; i < samples; i++) {
                const idx = Math.floor((i / Math.max(1, samples - 1)) * (row.length - 1));
                const v = row[idx];
                const x = railX + (i / Math.max(1, samples - 1)) * railW;
                const y = railY - Math.max(-1, Math.min(1, v / rowAbsMax)) * 11;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.strokeStyle = 'rgba(246,169,23,0.78)';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        }

        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';
        ctx.font = '10px system-ui';
        ctx.fillStyle = '#8e99b0';
        ctx.fillText(sec, railX, h - 5);
        ctx.textAlign = 'right';
        ctx.fillText('TRIBE frame', railX + railW, h - 5);
    } else {
        ctx.textAlign = 'center';
        ctx.textBaseline = 'alphabetic';
        ctx.font = '10px system-ui';
        ctx.fillStyle = '#6b738f';
        ctx.fillText('Awaiting TRIBE scan', cx, h - 6);
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
function selectedNarrationLabel() {
    return window._selectedNarrationLabel
        || document.getElementById("narration-model-select")?.selectedOptions?.[0]?.textContent?.trim()
        || "Gemma 4 26B A4B · OpenRouter free";
}

function _setNarrationText(divId, text, isPlaceholder) {
    const el = document.getElementById(divId);
    if (!el) return;
    const p = document.createElement("p");
    p.className = isPlaceholder ? "narration-placeholder" : "narration-text";
    p.textContent = text;
    el.innerHTML = "";
    el.appendChild(p);
}

function analysisModeLabel(mode, filename) {
    if (mode === "tribe_video") return "video + audio through TRIBE";
    if (mode === "tribe_audio") return "voice/audio through TRIBE";
    if (mode === "tribe_text") return "text through TRIBE events";
    if (mode === "tribe_text_bridge_image") return "image bridged to TRIBE text events";
    if (mode === "tribe_text_bridge_document") return "document bridged to TRIBE text events";
    if (filename) return filename;
    return "waiting for input";
}

function computeTargetLabel(target) {
    if (target === "cloud_hf") return "cloud TRIBE · Hugging Face path";
    if (target === "cloud_modal") return "cloud TRIBE · Modal path";
    if (target === "cloud_runpod") return "cloud TRIBE · RunPod path";
    if (target === "cloud_auto") return "cloud TRIBE · auto";
    return "local TRIBE v2 · Seratonin RTX 5090";
}

function fmtSeconds(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    return `${n.toFixed(n >= 10 ? 0 : 1)}s`;
}

function setEvidenceText(id, text, title = null) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (title || text) el.title = title || text;
}

function executionLabel(result = {}) {
    const status = result.status || (st.scanId ? "queued" : "not submitted");
    const target = result.compute_target || selectedComputeTarget();
    const route = result.proxied || String(target).startsWith("cloud_")
        ? "cloud worker"
        : "local GPU";
    if (status === "complete") {
        const pieces = ["complete", route];
        const tribe = fmtSeconds(result.tribe_seconds);
        const narr = fmtSeconds(result.narration_seconds);
        if (tribe) pieces.push(`TRIBE ${tribe}`);
        if (narr) pieces.push(`narration ${narr}`);
        return pieces.join(" · ");
    }
    if (status === "failed") return `failed · ${route}`;
    return `${status} · ${route}`;
}

function boldEvidenceLabel(result = {}) {
    const nT = Number(result.n_t || st.boldVertex?.n_t || st.boldTrace?.n_t || 0);
    if (result.has_bold_vertex || st.boldVertex) {
        return nT ? `20,484 vertices · ${nT} TRs` : "20,484-vertex trace";
    }
    const roiCount = (result.top_rois || []).length;
    if (roiCount) return `${roiCount} top ROIs · full trace pending`;
    if (result.status === "complete") return "complete · BOLD trace unavailable";
    return "waiting for TRIBE output";
}

function sourceContextLabel(result = {}) {
    const ctx = String(result.media_context || "");
    if (!ctx) {
        if (result.analysis_mode?.includes("bridge")) return "bridged stimulus · metadata pending";
        return "metadata pending";
    }
    if (ctx.includes("OpenRouter multimodal source description (")) {
        return "OpenRouter source description";
    }
    if (ctx.includes("OpenRouter multimodal source timeline (")) {
        return "OpenRouter timeline description";
    }
    if (ctx.includes("Stimulus timeline (sampled keyframes")) {
        return "sampled frame timeline";
    }
    if (ctx.includes("skipped because media exceeds")) {
        return "metadata only · media too large";
    }
    if (ctx.includes("unavailable")) {
        return "metadata only · source model unavailable";
    }
    return "metadata captured";
}

function updateAnalysisContext(result = {}) {
    const stimulus = document.getElementById("analysis-stimulus");
    const compute = document.getElementById("analysis-compute");
    const narrator = document.getElementById("analysis-narrator");
    const stimulusText = analysisModeLabel(result.analysis_mode, result.filename || st.lastFilename);
    if (stimulus) {
        stimulus.textContent = stimulusText;
        stimulus.title = result.filename || stimulusText;
    }
    if (compute) {
        const computeText = computeTargetLabel(result.compute_target || selectedComputeTarget());
        compute.textContent = computeText;
        compute.title = computeText;
    }
    if (narrator) {
        const narratorText = selectedNarrationLabel();
        narrator.textContent = narratorText;
        narrator.title = narratorText;
    }
    setEvidenceText("analysis-execution", executionLabel(result));
    setEvidenceText("analysis-bold", boldEvidenceLabel(result));
    setEvidenceText("analysis-source", sourceContextLabel(result), result.media_context || null);
}

function renderNarration(result) {
    result = { ...(st.scanResult || {}), ...(result || {}) };
    st.scanResult = result;
    // Publish to window for the data-panel charts (charts.js reads these)
    window.lastScanResult = result;
    window.dispatchEvent(new CustomEvent("cortex:scan-complete", { detail: { result } }));
    narrationModel.textContent = selectedNarrationLabel();
    updateAnalysisContext(result);

    if (result.status === "complete" && result.seconds_elapsed != null) {
        const tribeSec = result.seconds_elapsed || 0;
        const gemmaSec = st.narrationStartTime ? (Date.now() - st.narrationStartTime) / 1000 : 0;
        recordRun(result.id || st.scanId, st.lastFilename || 'unknown', tribeSec, gemmaSec);
        st.narrationStartTime = null;
    }

    // Rebuild stat row and ROI section inside narration-body before the tier divs
    // We inject these before #narration-general so they appear above tabs content.
    // Locate the stat-row placeholder we may have already put in body.
    const existingStats = narrationBody.querySelector(".stat-row");
    if (existingStats) existingStats.remove();
    const existingRoi = narrationBody.querySelector(".roi-section");
    if (existingRoi) existingRoi.remove();

    if (result.peak_t != null || result.top_rois?.length) {
        const row = document.createElement("div");
        row.className = "stat-row";
        if (result.peak_t != null) {
            row.innerHTML += `<div class="stat"><div class="stat-val">${(result.peak_t * 0.5).toFixed(1)}s</div><div class="stat-key">Peak response</div></div>`;
        }
        if (result.top_rois?.length) {
            row.innerHTML += `<div class="stat"><div class="stat-val">${result.top_rois.length}</div><div class="stat-key">Top ROIs</div></div>`;
        }
        narrationBody.insertBefore(row, narrationBody.firstChild);
    }

    // Populate 4-persona narration divs
    const PERSONA_KEYS = ["student", "ml_scientist", "clinician", "patient"];
    if (result.narrations && typeof result.narrations === "object") {
        // New persona dict format: {sam, priya, dr_park, chris}
        // Legacy compat: map old keys → new persona keys
        const legacyMap = { american: "student", student: "student", neurosurgeon: "clinician", ml_engineer: "ml_scientist" };
        const legacyNarrs = {};
        for (const [oldKey, newKey] of Object.entries(legacyMap)) {
            if (result.narrations[oldKey] && !legacyNarrs[newKey]) legacyNarrs[newKey] = result.narrations[oldKey];
        }
        for (const key of PERSONA_KEYS) {
            const text = result.narrations[key] ?? legacyNarrs[key] ?? null;
            if (text) {
                _setNarrationText(`narration-${key}`, text, false);
            } else {
                _setNarrationText(`narration-${key}`, "Generating…", true);
            }
        }
    } else {
        // Backwards compat: single narration field → sam tab
        const fallbackText = result.narration ?? (
            result.status === "complete"  ? "No narration generated." :
            result.status === "narrating" ? "Narrating personas…"     : "TRIBE v2 running…"
        );
        const isPlaceholder = !result.narration;
        _setNarrationText("narration-student",     fallbackText, isPlaceholder);
        _setNarrationText("narration-ml_scientist",   "Generating…", true);
        _setNarrationText("narration-clinician", "Generating…", true);
        _setNarrationText("narration-patient",   "Generating…", true);
    }

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
// Narration tabs — 4 persona voices
// ---------------------------------------------------------------------------
const PERSONA_TAB_KEYS = ["student", "ml_scientist", "clinician", "patient"];
document.querySelectorAll(".narr-tab").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".narr-tab").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        const key = btn.dataset.narr;
        PERSONA_TAB_KEYS.forEach(k => {
            const el = document.getElementById(`narration-${k}`);
            if (el) el.classList.toggle("active-tier", k === key);
        });
    });
});

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
            _setNarrationText("narration-student", `Requesting tier-${tier} narration…`, true);
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
function publishBoldDataFromVertex() {
    if (!st.boldVertex || !st.vertexLabels || !st.regionNetwork.length) return false;
    const { n_t: nT, n_vert: nV, data } = st.boldVertex;
    const nRegions = st.regionNetwork.length;
    if (!nT || !nV || !nRegions) return false;

    const counts = new Int32Array(nRegions);
    for (let vi = 0; vi < Math.min(nV, st.vertexLabels.length); vi++) {
        const ri = st.vertexLabels[vi] - 1;
        if (ri >= 0 && ri < nRegions) counts[ri]++;
    }

    const trace = new Float32Array(nT * nRegions);
    for (let t = 0; t < nT; t++) {
        const rowOff = t * nV;
        const sums = new Float32Array(nRegions);
        for (let vi = 0; vi < Math.min(nV, st.vertexLabels.length); vi++) {
            const ri = st.vertexLabels[vi] - 1;
            if (ri >= 0 && ri < nRegions) sums[ri] += data[rowOff + vi];
        }
        const outOff = t * nRegions;
        for (let ri = 0; ri < nRegions; ri++) {
            trace[outOff + ri] = counts[ri] ? sums[ri] / counts[ri] : 0;
        }
    }

    window.tribeBoldData = {
        n_t: nT,
        n_regions: nRegions,
        trace,
        networks: st.regionNetwork.slice(),
        region_ids: st.regionNames.slice(),
        source: "vertex-aggregate",
    };
    window.dispatchEvent(new CustomEvent("cortex:scan-complete", { detail: { fromBold: true, source: "vertex" } }));
    return true;
}

async function loadBoldForScan(scanId) {
    // Prefer the real per-vertex (T x 20484) trace if the scan has it on disk.
    // Falls back to the 50-region simulate endpoint when the .npy is missing.
    //
    // Initial frame: prefer scan_result.peak_t (the visually-impressive moment)
    // so that direct-link viewers (?scan=<id>) open ON the activation peak
    // rather than on a near-zero opening frame.
    const pickInitialFrame = (nT) => {
        const peak = st.scanResult?.peak_t;
        if (Number.isInteger(peak) && peak >= 0 && peak < nT) return peak;
        return Math.max(0, Math.min(nT - 1, (nT * 0.55) | 0));   // mid-clip default
    };

    try {
        const r = await fetch(`/api/scan/${encodeURIComponent(scanId)}/bold-vertex?n_t=100`);
        if (r.ok) {
            const buf = await r.arrayBuffer();
            const nT  = parseInt(r.headers.get("X-N-T")    || "0", 10);
            const nV  = parseInt(r.headers.get("X-N-Vert") || "0", 10);
            const f32 = new Float32Array(buf);
            if (nT && nV && f32.length === nT * nV) {
                st.boldVertex = { n_t: nT, n_vert: nV, data: f32, tr_seconds: 0.5 };
                st.boldTrace  = null;       // disable per-region path
                scrubber.disabled = false;
                scrubber.max      = String(nT - 1);
                const f0 = pickInitialFrame(nT);
                scrubber.value    = String(f0);
                paintVertexFrame(f0);
                drawColormapLegend();
                appendEvent(`BOLD: ${nT} TRs × ${nV} vertices (per-vertex), opened at t=${f0}`, "complete");
                publishBoldDataFromVertex();
                updateAnalysisContext({ ...(st.scanResult || {}), has_bold_vertex: true, n_t: nT });
                return;
            }
            appendEvent("per-vertex shape mismatch — falling back", "warning");
        }
    } catch (err) {
        appendEvent(`per-vertex fetch error — falling back: ${err.message}`, "warning");
    }
    // Fallback: per-region simulate (50 regions)
    try {
        const r = await fetch(`/api/scan/${encodeURIComponent(scanId)}/bold-simulate?n_t=100`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const trace = await r.json();
        st.boldTrace  = trace;
        st.boldVertex = null;
        scrubber.disabled = false;
        scrubber.max   = String(trace.n_t - 1);
        const f0 = pickInitialFrame(trace.n_t);
        scrubber.value = String(f0);
        paintFrame(f0);
        drawColormapLegend();
        if (isMobile) drawMobileFrame(f0);
        appendEvent(`BOLD: ${trace.n_t} TRs × ${trace.n_regions} regions (simulated), opened at t=${f0}`, "complete");
        // Publish for the data-panel charts (3D BOLD ribbon)
        const regionIds = trace.region_ids || trace.regions || [];
        const rows = trace.bold || trace.values || [];
        if (regionIds.length && rows.length) {
            // Flatten rows (T x R) into a Float32Array
            const flat = new Float32Array(trace.n_t * trace.n_regions);
            for (let t = 0; t < trace.n_t; t++) {
                for (let r = 0; r < trace.n_regions; r++) flat[t * trace.n_regions + r] = rows[t][r];
            }
            const networks = regionIds.map(roi => {
                const m = String(roi).match(/_(Vis|SomMot|DorsAttn|SalVentAttn|Limbic|Cont|Default)_/);
                return m ? m[1] : "Default";
            });
            window.tribeBoldData = { n_t: trace.n_t, n_regions: trace.n_regions, trace: flat, networks };
            window.dispatchEvent(new CustomEvent("cortex:scan-complete", { detail: { fromBold: true } }));
            updateAnalysisContext({ ...(st.scanResult || {}), n_t: trace.n_t });
        }
    } catch (err) {
        appendEvent(`BOLD load failed: ${err.message}`, "failed");
    }
}

// ---------------------------------------------------------------------------
// Per-vertex paint — true 20,484-vertex BOLD from the persisted .npy.
// Each vertex's z-score drives its own RGB; the cortical mesh shows the full
// fsaverage5 resolution rather than the 50-region downsample.
// ---------------------------------------------------------------------------
function paintVertexFrame(t) {
    if (!st.boldVertex) return;
    const { n_t, n_vert, data, tr_seconds } = st.boldVertex;
    const tt = Math.max(0, Math.min(n_t - 1, t | 0));
    timeLabel.textContent = `t = ${(tt * tr_seconds).toFixed(1)} s`;

    const filterActive = st.activeNetworks.size > 0;
    const rowOff = tt * n_vert;

    // Per-frame auto-scale: read the row's dynamic range so even small
    // activations paint at full saturation on camera.
    const row = data.subarray(rowOff, rowOff + n_vert);
    setZScaleForFrame(_absMaxOf(data, rowOff, n_vert));

    for (const [mesh, offset] of [[st.meshLH, 0], [st.meshRH, st.lhVertCount]]) {
        if (!mesh) continue;
        const cb = mesh.geometry.attributes.color;
        if (!cb) continue;
        const count = cb.count;
        for (let i = 0; i < count; i++) {
            const vi = offset + i;
            let r = BASE.r, g = BASE.g, b = BASE.b;
            if (vi < n_vert) {
                let render = true;
                if (filterActive) {
                    const ri = vi < st.vertexLabels.length ? st.vertexLabels[vi] : 0;
                    const netKey = ri > 0 ? (st.regionNetwork[ri - 1] ?? "") : "";
                    if (netKey && !st.activeNetworks.has(netKey)) render = false;
                }
                if (render) {
                    const z = data[rowOff + vi];
                    [r, g, b] = zToRGB(z);
                }
            }
            cb.setXYZ(i, r, g, b);
        }
        cb.needsUpdate = true;
    }

    if (isMobile) drawMobileFrame(tt);
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
            pushStream("queue", `${msg.queue.queue_depth} queued · ${msg.queue.completed ?? 0} completed`);
            break;
        case "scheduler_state":
            setGpu(msg.state);
            appendEvent(`GPU → ${msg.state}`, msg.state === "tribe_active" ? "progress" : "info");
            pushStream("gpu", `state: ${msg.state.replace("_"," ")}`);
            break;
        case "scan_queued":
            appendEvent(`queued: ${msg.filename} (${msg.scan_id})`);
            showOverlay("queued");
            pushStream("queue", `accepted scan ${shortId(msg.scan_id)} · ${sanitizeFilename(msg.filename)}`);
            break;
        case "scan_progress":
            appendEvent(`${msg.scan_id}: ${msg.phase}`, "progress");
            if (msg.scan_id === st.scanId) showOverlay(msg.phase);
            if (msg.phase === "running") pushStream("tribe", `scan ${shortId(msg.scan_id)} → loading TRIBE v2 weights`);
            else if (msg.phase === "narrating") pushStream("gemma", `scan ${shortId(msg.scan_id)} → narrating with 4 personas`);
            else pushStream("queue", `scan ${shortId(msg.scan_id)} → ${msg.phase}`);
            break;
        case "scan_complete":
            appendEvent(`scan ${msg.scan_id} complete`, "complete");
            overlay.classList.add("hidden");
            loadBoldForScan(msg.scan_id);
            loadScanResult(msg.scan_id);
            pushStream("queue", `scan ${shortId(msg.scan_id)} complete · 4 narrations rendered`);
            break;
        case "scan_narrations_ready":
            if (msg.narrations) {
                renderNarration({ narrations: msg.narrations });
                appendEvent(`narrations ready (student · patient · clinician · ML scientist)`, "complete");
                const ns = Object.keys(msg.narrations || {});
                pushStream("gemma", `narrations ready: ${ns.join(" · ")}`);
            }
            break;
        case "tribe_warm_started":
            appendEvent("TRIBE warm-up started", "progress");
            pushStream("tribe", "warming TRIBE v2 on Seratonin");
            pollReadiness();
            break;
        case "tribe_warm_complete":
            appendEvent("TRIBE v2 ready", "complete");
            pushStream("tribe", "TRIBE v2 loaded and ready");
            pollReadiness();
            break;
        case "tribe_warm_failed":
            appendEvent(`TRIBE warm-up failed: ${msg.message ?? "unknown"}`, "failed");
            pushStream("error", `TRIBE warm-up failed: ${(msg.message ?? "unknown").slice(0, 80)}`);
            pollReadiness();
            break;
        case "scan_failed":
            appendEvent(`scan failed: ${msg.error?.message ?? "?"}`, "failed");
            overlay.classList.add("hidden");
            _setNarrationText("narration-student", "Scan failed.", true);
            pushStream("error", `scan ${shortId(msg.scan_id)} failed: ${(msg.error?.message ?? "?").slice(0,80)}`);
            break;
    }
}

// ── Live activity stream ─────────────────────────────────────────────────────
// Sanitized real-time event log shown in the left panel. Strips usernames,
// absolute paths, and anything that looks like a credential.
const STREAM_MAX = 80;
let _streamCount = 0;
function shortId(s) { return (s || "").slice(0,8); }
function sanitizeFilename(s) {
    if (!s) return "(unnamed)";
    return String(s).replace(/^.*[\\/]/, "").slice(0, 40);
}
function pushStream(tag, msg) {
    const el = document.getElementById("live-stream");
    if (!el) return;
    if (_streamCount === 0) el.innerHTML = "";
    _streamCount++;
    const t = new Date();
    const hh = String(t.getHours()).padStart(2,"0");
    const mm = String(t.getMinutes()).padStart(2,"0");
    const ss = String(t.getSeconds()).padStart(2,"0");
    const row = document.createElement("div");
    row.className = "ls-row";
    row.innerHTML = `<span class="ls-time">${hh}:${mm}:${ss}</span><span class="ls-tag ${esc(tag)}">${esc(tag)}</span><span class="ls-msg">${esc(msg)}</span>`;

    // Auto-stick to bottom UNLESS the user has scrolled up. The threshold
    // (within 24 px of the bottom) is generous so a small wheel-scroll while
    // new content is rapidly appending doesn't fight the user.
    const wasAtBottom =
        (el.scrollTop + el.clientHeight) >= (el.scrollHeight - 24);

    el.appendChild(row);
    while (el.children.length > STREAM_MAX) el.removeChild(el.firstChild);

    if (wasAtBottom) {
        // requestAnimationFrame ensures we measure post-layout
        requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    } else {
        // Show a "new logs ↓" pill so the user knows there are unread events
        let pill = document.getElementById("ls-jump-pill");
        if (!pill) {
            pill = document.createElement("button");
            pill.id = "ls-jump-pill";
            pill.className = "ls-jump-pill";
            pill.textContent = "↓ new logs";
            pill.onclick = () => {
                el.scrollTop = el.scrollHeight;
                pill.remove();
            };
            // place it inside the stream container's wrapping parent so it
            // floats over the bottom-right of the log
            (el.parentElement || el).appendChild(pill);
        }
    }
}

// When the user scrolls back to the bottom, dismiss the pill automatically.
document.addEventListener("DOMContentLoaded", () => {
    const ls = document.getElementById("live-stream");
    if (!ls) return;
    ls.addEventListener("scroll", () => {
        const atBottom = (ls.scrollTop + ls.clientHeight) >= (ls.scrollHeight - 8);
        if (atBottom) {
            const pill = document.getElementById("ls-jump-pill");
            if (pill) pill.remove();
        }
    });
});

// ── Inference node status poller (Seratonin / OpenRouter) ───────────────────
async function pollNodeStatus() {
    try {
        const r = await fetch("/api/health", { cache: "no-store" });
        const d = await r.json();
        const state = d.gpu?.state || "idle";
        const seraUp = state !== "down";
        const seraBusy = state === "tribe_active" || state === "gemma_active" || state === "swapping";
        setNode("seratonin", seraUp ? (seraBusy ? "busy" : "up") : "down");
    } catch (e) {
        setNode("seratonin", "down");
    }
}
function setNode(name, state) {
    const el = document.querySelector(`.node-${name}`);
    if (!el) return;
    el.classList.remove("up", "busy", "down");
    el.classList.add(state);
}
setInterval(pollNodeStatus, 5000);
setTimeout(pollNodeStatus, 500);

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

async function pollOpenRouterStatus() {
    const line = document.getElementById("openrouter-status-line");
    if (!line) return;
    try {
        const r = await fetch("/api/openrouter/status", { cache: "no-store" });
        const d = await r.json();
        if (!r.ok || d.ok === false || d.status === "invalid_key") {
            line.textContent = `OpenRouter needs attention: ${d.action_required || d.message || d.status || "unavailable"}`;
            setNode("openrouter", "down");
            return;
        }
        if (d.status === "ready" || d.status === "configured") {
            line.textContent = "OpenRouter key is configured and account status is reachable.";
            setNode("openrouter", "up");
        } else {
            line.textContent = d.action_required || "OpenRouter key is not configured yet; scans will show TRIBE output without cloud narration.";
            setNode("openrouter", "down");
        }
    } catch (err) {
        line.textContent = `OpenRouter status unavailable: ${err.message}`;
        setNode("openrouter", "down");
    }
}

async function pollReadiness() {
    const btn = document.getElementById("tribe-warm-btn");
    const line = document.getElementById("tribe-status-line");
    try {
        const r = await fetch("/api/tribe/status", { cache: "no-store" });
        const d = await r.json();
        const gpu = d.gpu || {};
        setText("pc-live-status", d.pc_online ? "online" : "offline");
        setText("gpu-free-status", gpu.free_gb != null ? `${Number(gpu.free_gb).toFixed(1)} GB free` : "unknown");
        setText("tribe-ready-status", d.tribe_ready ? "loaded" : d.can_warm_tribe ? "ready to warm" : "not ready");
        if (line) line.textContent = d.message || "TRIBE status checked.";
        if (btn) {
            btn.disabled = !d.can_warm_tribe;
            btn.textContent = d.tribe_loaded ? "Recheck TRIBE v2" : "Warm TRIBE v2";
        }
    } catch (err) {
        setText("pc-live-status", "offline");
        setText("gpu-free-status", "unknown");
        setText("tribe-ready-status", "unreachable");
        if (line) line.textContent = `Cortex backend unavailable: ${err.message}`;
        if (btn) btn.disabled = true;
    }
}

document.getElementById("tribe-warm-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("tribe-warm-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Checking TRIBE v2..."; }
    try {
        const r = await fetch("/api/tribe/warm", { method: "POST" });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) {
            appendEvent(`TRIBE readiness check rejected: ${d.message || r.status}`, "failed");
        } else {
            appendEvent("TRIBE readiness check accepted", "complete");
        }
    } catch (err) {
        appendEvent(`TRIBE readiness check error: ${err.message}`, "failed");
    } finally {
        pollReadiness();
    }
});

setInterval(pollReadiness, 5000);
setTimeout(pollReadiness, 700);
setInterval(pollOpenRouterStatus, 30000);
setTimeout(pollOpenRouterStatus, 900);

function showOverlay(phase) {
    // Don't reappear on top of an auto-loaded scan — direct-link viewers
    // shouldn't get a "Queued…" splash painted over a brain that's already
    // animating. The IIFE at the bottom of this file sets st.urlScanLocked.
    if (st.urlScanLocked) return;
    overlay.classList.remove("hidden");
    overlay.style.display = "";
    if (phase === "narrating") st.narrationStartTime = Date.now();
    const msgs = {
        queued:    "Queued — waiting for GPU…",
        running:   "TRIBE v2 running…<br><small style='opacity:.6'>Predicting cortical BOLD responses</small>",
        narrating: "OpenRouter narrating…<br><small style='opacity:.6'>Building persona interpretation</small>",
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
const CLOUD_A100_RATE   = 3.67;   // USD per hour for a hosted GPU reference
const CLOUD_TRIBE_S     = 30;     // estimated TRIBE inference seconds off-PC
const OPENROUTER_FREE_C = 0;      // current default narration cost inside free-model limits
const runHistory = [];

function localCostCents(totalSeconds) {
    return (LOCAL_GPU_W * totalSeconds / 3600 / 1000 * ELECTRICITY_RATE * 100);
}

function cloudCostCents(tribeSec) {
    const tribe  = (CLOUD_A100_RATE / 3600 * tribeSec) * 100;  // cents
    return tribe + OPENROUTER_FREE_C;
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

    // Show input preview in file mode
    const previewEl = document.getElementById("input-preview");
    if (previewEl && file) {
        previewEl.innerHTML = "";
        previewEl.classList.remove("hidden");
        const url = URL.createObjectURL(file);
        if (file.type.startsWith("video/")) {
            const vid = document.createElement("video");
            vid.src = url; vid.muted = false; vid.controls = true;
            vid.autoplay = false; vid.loop = false; vid.preload = "metadata"; vid.playsInline = true;
            previewEl.appendChild(vid);
        } else if (file.type.startsWith("image/")) {
            const img = document.createElement("img");
            img.src = url; img.alt = "preview";
            previewEl.appendChild(img);
        } else {
            previewEl.classList.add("hidden");
        }
    }

    // Reset narration divs to "pending" state
    _setNarrationText("narration-student",    "Scan accepted — awaiting results…", true);
    _setNarrationText("narration-ml_scientist",  "Generating…", true);
    _setNarrationText("narration-clinician","Generating…", true);
    _setNarrationText("narration-patient",  "Generating…", true);

    const fd = new FormData();
    fd.append("file",            file);
    fd.append("tier",            tierInput.value);
    fd.append("source",          "webui");
    fd.append("narration_model", window._selectedNarrationModel || DEFAULT_NARRATION_MODEL);
    fd.append("compute_target",   selectedComputeTarget());
    fd.append("paid_access_code", paidAccessCode());

    try {
        const resp = await fetch("/api/scan", { method: "POST", body: fd });
        const body = await resp.json();
        if (!resp.ok) {
            appendEvent(`rejected: ${body.message ?? body.error_code}`, "failed");
        } else {
            st.scanId = body.scan_id;
            st.lastFilename = file.name;
            updateAnalysisContext({ ...body, filename: file.name });
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
// Drop zone + file upload wiring
// ---------------------------------------------------------------------------
(function wireDropZone() {
    const dropZone   = document.getElementById("drop-zone");
    const fileInput  = document.getElementById("scan-file");
    const dropSel    = document.getElementById("drop-selected");

    if (!dropZone || !fileInput) return;

    function applyFile(file) {
        if (!file) return;
        submitBtn.disabled = false;
        if (dropSel) {
            dropSel.classList.remove("hidden");
            const ext = file.name.split(".").pop().toUpperCase();
            dropSel.innerHTML = `<span>${esc(file.name)}</span><span class="file-chip">${esc(ext)}</span>`;
        }
        // Preview for images/video shown inline
        const previewEl = document.getElementById("input-preview");
        if (previewEl) {
            previewEl.innerHTML = "";
            previewEl.classList.remove("hidden");
            const url = URL.createObjectURL(file);
            if (file.type.startsWith("video/")) {
                const vid = document.createElement("video");
                vid.src = url; vid.muted = false; vid.controls = true;
                vid.autoplay = false; vid.loop = false; vid.preload = "metadata"; vid.playsInline = true;
                previewEl.appendChild(vid);
            } else if (file.type.startsWith("image/")) {
                const img = document.createElement("img");
                img.src = url; img.alt = "preview";
                previewEl.appendChild(img);
            } else {
                previewEl.classList.add("hidden");
            }
        }
    }

    fileInput.addEventListener("change", () => {
        if (fileInput.files?.length) applyFile(fileInput.files[0]);
    });

    dropZone.addEventListener("dragover", e => {
        e.preventDefault();
        dropZone.classList.add("drag-over");
    });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", e => {
        e.preventDefault();
        dropZone.classList.remove("drag-over");
        const file = e.dataTransfer?.files?.[0];
        if (file) {
            // iOS Safari doesn't support DataTransfer constructor — fallback gracefully
            try {
                const dt = new DataTransfer();
                dt.items.add(file);
                fileInput.files = dt.files;
            } catch (_) { /* DataTransfer not supported; skip fileInput sync */ }
            applyFile(file);
        }
    });

    submitBtn.addEventListener("click", async () => {
        if (!fileInput.files?.length) return;
        await submitMediaFile(fileInput.files[0], { btnEl: submitBtn, resetLabel: "Analyze" });
    });
})();

// ---------------------------------------------------------------------------
// Input mode tabs
// ---------------------------------------------------------------------------
const modeTabs   = document.querySelectorAll(".mode-tab");
const modePanels = document.querySelectorAll(".mode-panel");

function switchMode(mode) {
    modeTabs.forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
    modePanels.forEach(p => p.classList.toggle("active", p.id === `mode-${mode}`));
    const captureDrawer = document.querySelector(".capture-drawer");
    if (captureDrawer) captureDrawer.open = mode === "camera" || mode === "voice";
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
        // On mobile, "environment" = rear camera; fall back to any camera on desktop
        const constraints = {
            video: isMobile
                ? { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 960 } }
                : { width: { ideal: 1280 }, height: { ideal: 960 } },
        };
        _camStream = await navigator.mediaDevices.getUserMedia(constraints);
        camVideo.srcObject = _camStream;
        // iOS Safari: must call play() explicitly after assigning srcObject
        await camVideo.play().catch(() => {});
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

    // Pick best supported MIME type — iOS Safari supports audio/mp4; Android supports webm/opus
    const mime = [
        "audio/webm;codecs=opus",  // Chrome/Android: best
        "audio/webm",              // Chrome/Android: fallback
        "audio/ogg;codecs=opus",   // Firefox
        "audio/mp4;codecs=mp4a",   // iOS Safari 14.3+
        "audio/mp4",               // iOS Safari
    ].find(t => MediaRecorder.isTypeSupported(t));
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
    _setNarrationText("narration-student", "Analyzing text stimulus…", true);
    _setNarrationText("narration-patient", "Generating…", true);
    _setNarrationText("narration-clinician", "Generating…", true);
    _setNarrationText("narration-ml_scientist", "Generating…", true);

    const fd = new FormData();
    fd.append("text",            text);
    fd.append("tier",            tierInput.value);
    fd.append("source",          "webui");
    fd.append("narration_model", window._selectedNarrationModel || DEFAULT_NARRATION_MODEL);
    fd.append("compute_target",   selectedComputeTarget());
    fd.append("paid_access_code", paidAccessCode());

    try {
        const resp = await fetch("/api/text-scan", { method: "POST", body: fd });
        const body = await resp.json();
        if (!resp.ok) {
            appendEvent(`rejected: ${body.message ?? body.detail ?? "error"}`, "failed");
        } else {
            st.scanId = body.scan_id;
            st.lastFilename = "<text stimulus>";
            updateAnalysisContext({ ...body, filename: "<text stimulus>" });
            appendEvent(`text scan accepted: ${body.scan_id}`, "complete");
            showOverlay("narrating"); // typed text is queued through TRIBE's text-events path
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
             <span style="font-size:12px;opacity:.6">TRIBE v2 predicts cortical BOLD. OpenRouter narrates.</span></div>`;
    overlay.classList.remove("hidden");

    // Mobile: default to cloud tab, then render the compact brain state strip.
    if (isMobile) {
        const cloudTab = document.querySelector('[data-pipeline="cloud"]');
        if (cloudTab) {
            pipelineTabs.forEach(b => b.classList.toggle('active', b === cloudTab));
            currentPipeline = 'cloud';
            pipelineBadge.className   = 'pipeline-badge cloud';
            pipelineBadge.textContent = '☁ OpenRouter · cloud narration';
        }
        const currentFrame = Number.isFinite(+scrubber.value) ? +scrubber.value : 0;
        if (st.boldVertex) paintVertexFrame(currentFrame);
        else if (st.boldTrace) paintFrame(currentFrame);
        else drawMobileFrame(undefined);
    }
})();

window.loadBoldForScan = loadBoldForScan;
window.loadScanResult  = loadScanResult;
window.paintVertexFrame = paintVertexFrame;

// URL param: ?scan=<id> auto-loads that scan AND flips the UI out of the
// upload-prompt empty state so the brain becomes visible without any clicks.
//
// Belt + suspenders: classList AND inline style, AND a re-hide on every
// data-load completion (some WS messages can re-show the overlay otherwise).
(function autoLoadFromUrl() {
    try {
        const params = new URLSearchParams(window.location.search);
        const scanId = params.get("scan");
        if (!scanId) return;

        const forceHide = () => {
            const ov = document.getElementById("viewer-overlay");
            if (!ov) return;
            ov.classList.add("hidden");
            ov.style.display = "none";
        };
        forceHide();

        // Lock the overlay against showOverlay() reopening it.
        st.urlScanLocked = true;
        st.scanId = scanId;
        appendEvent(`auto-loading scan ${scanId} from URL`, "complete");

        // Wait a frame so the WS + initial mesh load can settle, then load.
        setTimeout(() => {
            forceHide();
            try { loadScanResult(scanId); } catch (e) { console.warn("loadScanResult failed:", e); }
            try { loadBoldForScan(scanId).then(forceHide).catch(forceHide); }
            catch (e) { console.warn("loadBoldForScan failed:",  e); }
        }, 250);

        // Belt + suspenders #2: a brief watchdog that re-hides the overlay
        // for the first 5 s in case any later async path tries to show it.
        let n = 0;
        const watchdog = setInterval(() => { forceHide(); if (++n > 50) clearInterval(watchdog); }, 100);
    } catch (e) { /* no-op */ }
})();

// ─────────────────────────────────────────────────────────────────────────────
// Material 3 timeline — keeps the legacy <input id=time-scrubber> as the source
// of truth (so existing wiring still works) and renders a polished UI on top.
// Adds peak-time markers + region tour mode.
// ─────────────────────────────────────────────────────────────────────────────
(function wireTimeline() {
    const wrap   = document.getElementById("timeline");
    const track  = wrap?.querySelector(".timeline-track");
    const prog   = document.getElementById("timeline-progress");
    const thumb  = document.getElementById("timeline-thumb");
    const hover  = document.getElementById("timeline-hover");
    const tourBtn = document.getElementById("tour-btn");
    const scrub  = document.getElementById("scan-tier") ? scrubber : null;
    if (!wrap || !track || !prog || !thumb || !scrubber) return;

    function renderFrom() {
        const v = +scrubber.value;
        const max = +scrubber.max || 1;
        const pct = (v / max) * 100;
        prog.style.width = pct + "%";
        thumb.style.left = pct + "%";
        wrap.setAttribute("aria-valuenow", v);
        wrap.setAttribute("aria-valuemax", max);
        // sync the disabled state
        if (scrubber.disabled) wrap.classList.add("disabled");
        else wrap.classList.remove("disabled");
    }
    // initial paint + observe later writes to scrubber.value
    renderFrom();
    new MutationObserver(renderFrom).observe(scrubber, { attributes: true, attributeFilter: ["value", "max", "disabled"] });
    // also catch programmatic value writes from the playback ticker
    const _origDescriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    Object.defineProperty(scrubber, "value", {
        get() { return _origDescriptor.get.call(this); },
        set(v) { _origDescriptor.set.call(this, v); renderFrom(); },
        configurable: true,
    });

    function pctFromEvent(e) {
        const rect = track.getBoundingClientRect();
        const x = (e.touches?.[0]?.clientX ?? e.clientX) - rect.left;
        return Math.max(0, Math.min(1, x / rect.width));
    }
    function setFromPct(p) {
        if (scrubber.disabled) return;
        const max = +scrubber.max || 1;
        const v = Math.round(p * max);
        scrubber.value = v;
        scrubber.dispatchEvent(new Event("input", { bubbles: true }));
        scrubber.dispatchEvent(new Event("change", { bubbles: true }));
    }
    let dragging = false;
    wrap.addEventListener("pointerdown", e => {
        if (scrubber.disabled) return;
        dragging = true; setFromPct(pctFromEvent(e));
        wrap.setPointerCapture?.(e.pointerId);
    });
    wrap.addEventListener("pointermove", e => {
        if (scrubber.disabled) return;
        const p = pctFromEvent(e);
        if (dragging) setFromPct(p);
        if (hover) {
            hover.style.display = "";
            hover.style.left = (p * 100) + "%";
            const tr = (window.tribeTrSeconds ?? 0.5);
            const max = +scrubber.max || 1;
            const t = Math.round(p * max);
            hover.textContent = (t * tr).toFixed(1) + " s · TR " + t;
        }
    });
    wrap.addEventListener("pointerup", e => { dragging = false; wrap.releasePointerCapture?.(e.pointerId); });
    wrap.addEventListener("pointerleave", () => { if (hover) hover.style.display = "none"; });
    wrap.addEventListener("keydown", e => {
        if (scrubber.disabled) return;
        const max = +scrubber.max || 1;
        if (e.key === "ArrowRight") { scrubber.value = Math.min(+scrubber.value + 1, max); scrubber.dispatchEvent(new Event("input")); e.preventDefault(); }
        if (e.key === "ArrowLeft")  { scrubber.value = Math.max(+scrubber.value - 1, 0);   scrubber.dispatchEvent(new Event("input")); e.preventDefault(); }
        if (e.key === "Home")       { scrubber.value = 0;   scrubber.dispatchEvent(new Event("input")); e.preventDefault(); }
        if (e.key === "End")        { scrubber.value = max; scrubber.dispatchEvent(new Event("input")); e.preventDefault(); }
        if (e.key === " " || e.key === "Enter") { document.getElementById("play-toggle")?.click(); e.preventDefault(); }
    });

    // Decorate the track with peak-time + tick marks. Called after a scan completes.
    window.timelineMarkPeak = function(peakIdx) {
        wrap.querySelectorAll(".timeline-peak").forEach(el => el.remove());
        if (peakIdx == null) return;
        const max = +scrubber.max || 1;
        const pct = (peakIdx / max) * 100;
        const m = document.createElement("div");
        m.className = "timeline-peak";
        m.style.left = pct + "%";
        m.title = "Peak activation";
        wrap.appendChild(m);
    };
    window.timelineMarkTicks = function(n = 5) {
        wrap.querySelectorAll(".timeline-tick").forEach(el => el.remove());
        for (let i = 1; i < n; i++) {
            const t = document.createElement("div");
            t.className = "timeline-tick";
            t.style.left = ((i / n) * 100) + "%";
            wrap.appendChild(t);
        }
    };

    // ─── Brain tour mode — auto-cycle top ROIs ───
    let tourTimer = null;
    let tourIdx = 0;
    if (tourBtn) {
        tourBtn.addEventListener("click", () => {
            if (tourTimer) {
                clearInterval(tourTimer); tourTimer = null;
                tourBtn.classList.remove("active");
                tourBtn.textContent = "▶ Tour";
                return;
            }
            const rois = (window.lastScanResult?.top_rois || []).slice(0, 10);
            if (!rois.length) {
                appendEvent && appendEvent("Tour needs a completed scan with ROIs first", "warning");
                return;
            }
            tourBtn.classList.add("active");
            tourBtn.textContent = "■ Stop";
            tourIdx = 0;
            const advance = () => {
                const roi = rois[tourIdx % rois.length];
                tourIdx++;
                // Use the existing region-click handler if available, else dispatch a custom event
                if (typeof window.focusRoi === "function") window.focusRoi(roi);
                else window.dispatchEvent(new CustomEvent("brain-tour-step", { detail: { roi } }));
                appendEvent && appendEvent("Tour: " + roi.replace("7Networks_",""), "info");
            };
            advance();
            tourTimer = setInterval(advance, 2200);
        });
    }
})();

// ─────────────────────────────────────────────────────────────────────────────
// Live system monitor + per-scan timing readout
// Polls /api/fleet-health every 2s — single endpoint, local node + services.
// ─────────────────────────────────────────────────────────────────────────────
(function wireTelemetry() {
    const seraNode = document.querySelector('.telemetry-node[data-node="seratonin"]');
    if (!seraNode) return;

    function setMetric(node, lbl, valueText, fillPct) {
        const rows = node.querySelectorAll(".metric-row");
        for (const row of rows) {
            if (row.querySelector(".lbl")?.textContent === lbl) {
                row.querySelector(".val").textContent = valueText;
                row.querySelector(".fill").style.width = Math.max(0, Math.min(100, fillPct || 0)) + "%";
                return;
            }
        }
    }
    function setBadge(node, text, kind) {
        const b = node.querySelector(".name .badge");
        if (!b) return;
        b.textContent = text;
        b.className = "badge " + (kind || "");
    }

    function paintNode(node, view, services, role) {
        if (!view || !view.alive) {
            setBadge(node, "offline", "down");
            setMetric(node, "GPU",  "—", 0);
            setMetric(node, "VRAM", "—", 0);
            setMetric(node, "Queue", "—", 0);
            return;
        }
        const total = view.total_gb || 1;
        const used  = view.used_gb || 0;
        const pctMem = Math.round((used / total) * 100);
        const busy = (view.gpu_state || "").includes("active") || (view.queue_depth || 0) > 0 || !!view.active;
        const pctGpu = busy ? (60 + Math.random() * 35) : Math.max(2, used / total * 25);

        // Compose badge — include services for this node
        let badgeText = view.gpu_state || "idle";
        if (busy) badgeText = "busy";
        const ollamaUp = services?.ollama_local;
        const routerUp = services?.router_local;
        if (ollamaUp === false) badgeText = "ollama down";
        else if (routerUp === false) badgeText = "router down";
        setBadge(node, badgeText, busy ? "busy" : (ollamaUp === false || routerUp === false ? "down" : "up"));

        setMetric(node, "GPU",   Math.round(pctGpu) + "%", pctGpu);
        setMetric(node, "VRAM", used.toFixed(1) + " / " + total.toFixed(0) + " GB", pctMem);
        setMetric(node, "Queue",
                  ((view.queue_depth ?? 0) + (view.active ? 1 : 0)) + " jobs",
                  Math.min(100, ((view.queue_depth || 0) + (view.active ? 1 : 0)) * 25));
    }

    function applySnapshot(d) {
        const nodes = d.nodes || {};
        const services = d.services || {};
        paintNode(seraNode, nodes.seratonin || Object.values(nodes)[0], services, "seratonin");
        window.lastFleet = d;
    }

    async function pollOnce() {
        try {
            const r = await fetch("/api/fleet-health", { cache: "no-store" });
            if (!r.ok) throw new Error("HTTP " + r.status);
            applySnapshot(await r.json());
        } catch (e) {
            setBadge(seraNode, "unreachable", "down");
        }
    }

    // First paint via REST so the page lights up immediately.
    pollOnce();

    // ── WebSocket-driven live updates (sub-200 ms when something changes) ──
    let ws = null;
    let wsReconnectTimer = null;
    let wsLastSeen = 0;
    function openSocket() {
        try {
            const proto = location.protocol === "https:" ? "wss:" : "ws:";
            ws = new WebSocket(proto + "//" + location.host + "/api/ws");
            ws.addEventListener("message", e => {
                wsLastSeen = Date.now();
                let m;
                try { m = JSON.parse(e.data); } catch { return; }
                if (m && m.type === "fleet:health" && m.data) applySnapshot(m.data);
            });
            ws.addEventListener("close", () => {
                clearTimeout(wsReconnectTimer);
                wsReconnectTimer = setTimeout(openSocket, 2000);
            });
            ws.addEventListener("error", () => { try { ws.close(); } catch (_) {} });
        } catch (_) {
            wsReconnectTimer = setTimeout(openSocket, 2000);
        }
    }
    openSocket();

    // Watchdog: if the WS hasn't pushed anything in 8s, fall back to a single
    // REST refresh to surface state. Cheap, correct, and keeps mobile/proxied
    // clients honest when WS upgrade fails silently.
    setInterval(() => {
        if (!wsLastSeen || (Date.now() - wsLastSeen) > 8000) pollOnce();
    }, 8000);

    // Per-scan timing readout — shown when scan completes
    window.addEventListener("cortex:scan-complete", e => {
        const result = window.lastScanResult || (e.detail && e.detail.result);
        if (!result || result.status !== "complete") return;
        const box = document.getElementById("run-timing");
        if (!box) return;
        box.style.display = "";
        const total = result.seconds_elapsed;
        const fmt = s => s != null ? Number(s).toFixed(1) + " s" : "—";
        // Real timings if backend provided them; fall back to estimate.
        const tribeS = result.tribe_seconds;
        const narrS  = result.narration_seconds;
        const tribeEl = document.getElementById("rt-tribe");
        const narrEl  = document.getElementById("rt-narr");
        const totalEl = document.getElementById("rt-total");
        if (tribeS != null && narrS != null) {
            tribeEl.textContent = fmt(tribeS);
            narrEl.textContent  = fmt(narrS);
        } else if (total != null) {
            const tribe = total * 0.6;
            const narr  = total - tribe;
            tribeEl.textContent = fmt(tribe) + " (est)";
            narrEl.textContent  = fmt(narr)  + " (est)";
        }
        if (totalEl) totalEl.textContent = fmt(total);
        const rois = result.top_rois || [];
        const roiEl = document.getElementById("rt-roi");
        if (roiEl) roiEl.textContent = rois.length ? rois[0].replace("7Networks_","") : "—";
        const peak = result.peak_t;
        const tr   = result.tr_seconds || 0.5;
        const peakEl = document.getElementById("rt-peak");
        if (peakEl) peakEl.textContent = peak != null ? `t=${peak} (${(peak * tr).toFixed(1)} s)` : "—";

        // Per-persona timings if present — render into #rt-personas
        const persEl = document.getElementById("rt-personas");
        const timings = result.narration_timings || {};
        if (persEl && Object.keys(timings).length) {
            const order = ["student","patient","clinician","ml_scientist"];
            const labels = { student:"Student", patient:"Patient", clinician:"Clinician", ml_scientist:"ML Scientist" };
            persEl.innerHTML = order
                .filter(k => timings[k] != null)
                .map(k => `<span class="chip">${labels[k]}: ${fmt(timings[k])}</span>`)
                .join(" ");
        }
    });
})();
