// Cortex viewer — interactive Three.js cortex visualization.
//
// Architecture choice: every piece below is independent of whether the
// underlying mesh is the real Schaefer-400 / brain.glb or the procedural
// stand-in we ship today. The single function `buildBrainMesh()` is the
// swap-in point for a real cortical surface mesh; everything else (raycaster,
// time scrubber, network toggles, WebSocket replay) operates against the
// atlas data structure and never needs to change.
//
// Atlas data shape (from /api/atlas):
//   { networks: { "<net>": { label, color }, ... },
//     regions:  [ { id, name, network, hemi, brodmann, xyz: [x,y,z] }, ... ] }
//
// BOLD trace shape (from /api/scan/<id>/bold-simulate):
//   { n_t, n_regions, region_ids, bold: number[n_t][n_regions] }

import * as THREE from "https://unpkg.com/three@0.176.0/build/three.module.js";

// ---------------------------------------------------------------------------
// DOM handles
// ---------------------------------------------------------------------------

const dot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const eventLog = document.getElementById("event-log");
const overlay = document.getElementById("viewer-overlay");
const submitButton = document.getElementById("scan-submit");
const tierInput = document.getElementById("scan-tier");
const tierDisplay = document.getElementById("tier-display");
const sidebar = document.getElementById("region-sidebar");
const scrubber = document.getElementById("time-scrubber");
const scrubberLabel = document.getElementById("time-scrubber-label");
const networkToggles = document.getElementById("network-toggles");

tierInput.addEventListener("input", () => { tierDisplay.textContent = tierInput.value; });

function setStatus(state, text) { dot.className = `dot ${state}`; statusText.textContent = text; }

function appendEvent(message, kind = "info") {
    const li = document.createElement("li");
    li.className = `event-${kind}`;
    li.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    eventLog.prepend(li);
    while (eventLog.childElementCount > 60) eventLog.removeChild(eventLog.lastChild);
}

// ---------------------------------------------------------------------------
// State (mutable)
// ---------------------------------------------------------------------------

const state = {
    atlas: null,                       // loaded from /api/atlas
    regionMeshes: new Map(),           // region_id -> THREE.Mesh
    activeNetworks: new Set(),         // networks currently rendered
    bold: null,                        // current BOLD trace
    boldRegionIndex: new Map(),        // region_id -> index in bold rows
    selectedRegionId: null,
};

// ---------------------------------------------------------------------------
// Three.js scene
// ---------------------------------------------------------------------------

const root = document.getElementById("three-root");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0d12);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
// 3/4 anterior view — shows both hemispheres, the dorsal aspect, and the
// brain stem ovoid in the same frame.
camera.position.set(2.6, 1.4, 2.4);
camera.lookAt(0, -0.05, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
root.appendChild(renderer.domElement);

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const keyLight = new THREE.PointLight(0xffffff, 1.0, 12);
keyLight.position.set(3, 4, 3);
scene.add(keyLight);
const fillLight = new THREE.PointLight(0x6ea8ff, 0.35, 10);
fillLight.position.set(-3, -1, -2);
scene.add(fillLight);

// ---------------------------------------------------------------------------
// Brain mesh — procedural stand-in (better than a smooth sphere; not as good
// as a real fsaverage cortical surface). Replace `buildBrainMesh()` when the
// real brain.glb + Schaefer-400 lookup is dropped in. Everything else
// (raycaster, time scrubber, network toggles) operates against the atlas
// data structure and doesn't care about the mesh shape.
//
// The displacement is multi-octave sinusoidal noise computed once at build
// time and baked into the BufferGeometry — no per-frame cost. Reads as
// gyri/sulci silhouette without the heaviness of a real 100K-vertex mesh.
// ---------------------------------------------------------------------------

function buildBrainMesh() {
    const group = new THREE.Group();
    group.name = "brain-shell";

    function _displaceForGyri(geom) {
        // Multi-octave displacement along the surface normal. The frequencies
        // and amplitudes are picked so the result reads as "wrinkly cortex"
        // rather than "lumpy potato" — major sulci at f1, secondary at f2,
        // texture at f3.
        const positions = geom.attributes.position;
        const normals = geom.attributes.normal;
        const FREQS = [6.5, 13.0, 26.0];
        const AMPS = [0.045, 0.022, 0.011];

        for (let i = 0; i < positions.count; i++) {
            const x = positions.getX(i);
            const y = positions.getY(i);
            const z = positions.getZ(i);
            const nx = normals.getX(i);
            const ny = normals.getY(i);
            const nz = normals.getZ(i);

            let d = 0;
            for (let k = 0; k < 3; k++) {
                const f = FREQS[k];
                d += AMPS[k] * (
                    Math.sin(x * f + k * 1.7) *
                    Math.cos(y * f * 0.95 + k * 0.6) *
                    Math.sin(z * f * 1.05 + k * 2.3)
                );
            }
            positions.setXYZ(i, x + nx * d, y + ny * d, z + nz * d);
        }
        positions.needsUpdate = true;
        geom.computeVertexNormals();
        return geom;
    }

    function _makeHemisphere() {
        // Subdivision level 5 → ~10,242 vertices, matching fsaverage5 density.
        // Same vertex count TRIBE v2 outputs per hemisphere.
        const geom = new THREE.IcosahedronGeometry(1.0, 5);
        // Brain proportions: slightly elongated antero-posterior, narrower
        // mediolateral, flattened dorsoventral.
        geom.scale(1.05, 0.82, 1.15);
        return _displaceForGyri(geom);
    }

    const corticalMaterial = new THREE.MeshStandardMaterial({
        color: 0xd9c8d6,         // pinkish-grey brain tissue
        metalness: 0.04,
        roughness: 0.72,
        transparent: true,
        opacity: 0.22,
        depthWrite: false,
        side: THREE.DoubleSide,
        flatShading: false,
    });

    const left = new THREE.Mesh(_makeHemisphere(), corticalMaterial);
    left.position.set(-0.07, 0, 0);

    const right = new THREE.Mesh(_makeHemisphere(), corticalMaterial);
    // Mirror right hemisphere across X for left-right asymmetry.
    right.scale.x = -1;
    right.position.set(0.07, 0, 0);

    // Wireframe overlay — emphasizes the gyri/sulci texture.
    const wireMat = new THREE.LineBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.06,
    });
    const leftWire = new THREE.LineSegments(new THREE.WireframeGeometry(left.geometry), wireMat);
    leftWire.position.copy(left.position);
    const rightWire = new THREE.LineSegments(new THREE.WireframeGeometry(right.geometry), wireMat);
    rightWire.scale.x = -1;
    rightWire.position.copy(right.position);

    // Brain stem hint — small ovoid at the back-bottom-center, gives the
    // silhouette a recognizable cortex-vs-stem distinction without modeling
    // the cerebellum.
    const stemGeom = new THREE.SphereGeometry(0.16, 24, 18);
    stemGeom.scale(1.0, 1.4, 1.4);
    const stem = new THREE.Mesh(stemGeom, corticalMaterial);
    stem.position.set(0, -0.55, -0.05);

    group.add(left, right, leftWire, rightWire, stem);
    return group;
}

scene.add(buildBrainMesh());

// ---------------------------------------------------------------------------
// ROI markers
// ---------------------------------------------------------------------------

const ROI_GEOM = new THREE.SphereGeometry(0.06, 16, 12);

function placeRegionMarkers(atlas) {
    const networkColors = atlas.networks;

    for (const region of atlas.regions) {
        const colorHex = networkColors[region.network]?.color || "#888888";
        const colorRGB = new THREE.Color(colorHex);

        const material = new THREE.MeshStandardMaterial({
            color: colorRGB,
            emissive: colorRGB,
            emissiveIntensity: 0.25,
            metalness: 0.1,
            roughness: 0.45,
            transparent: true,
            opacity: 0.95,
        });

        const mesh = new THREE.Mesh(ROI_GEOM, material);
        const [x, y, z] = region.xyz;
        mesh.position.set(x, y, z);
        mesh.userData = region;
        mesh.name = region.id;
        scene.add(mesh);

        state.regionMeshes.set(region.id, mesh);
    }
}

// ---------------------------------------------------------------------------
// Network toggles
// ---------------------------------------------------------------------------

function renderNetworkToggles(atlas) {
    networkToggles.innerHTML = "";
    for (const [networkKey, network] of Object.entries(atlas.networks)) {
        state.activeNetworks.add(networkKey);

        const label = document.createElement("label");
        label.className = "network-toggle";
        label.style.setProperty("--swatch", network.color);

        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.network = networkKey;
        cb.addEventListener("change", () => {
            if (cb.checked) state.activeNetworks.add(networkKey);
            else state.activeNetworks.delete(networkKey);
            applyNetworkVisibility();
        });

        const span = document.createElement("span");
        span.className = "network-name";
        span.textContent = network.label;

        label.append(cb, span);
        networkToggles.appendChild(label);
    }
}

function applyNetworkVisibility() {
    for (const mesh of state.regionMeshes.values()) {
        mesh.visible = state.activeNetworks.has(mesh.userData.network);
    }
}

// ---------------------------------------------------------------------------
// Click-to-inspect via raycasting
// ---------------------------------------------------------------------------

const raycaster = new THREE.Raycaster();
const ndcPointer = new THREE.Vector2();

renderer.domElement.addEventListener("click", (event) => {
    const rect = renderer.domElement.getBoundingClientRect();
    ndcPointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    ndcPointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndcPointer, camera);

    const candidates = Array.from(state.regionMeshes.values()).filter((m) => m.visible);
    const hits = raycaster.intersectObjects(candidates, false);
    if (hits.length === 0) return;
    selectRegion(hits[0].object.userData.id);
});

// Hover labels — floating tooltip following the cursor when over an ROI.
const tooltip = document.createElement("div");
tooltip.className = "roi-tooltip";
tooltip.style.display = "none";
document.body.appendChild(tooltip);

renderer.domElement.addEventListener("mousemove", (event) => {
    const rect = renderer.domElement.getBoundingClientRect();
    ndcPointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    ndcPointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndcPointer, camera);

    const candidates = Array.from(state.regionMeshes.values()).filter((m) => m.visible);
    const hits = raycaster.intersectObjects(candidates, false);
    if (hits.length === 0) {
        tooltip.style.display = "none";
        renderer.domElement.style.cursor = "default";
        return;
    }
    const region = hits[0].object.userData;
    const network = state.atlas?.networks?.[region.network];
    tooltip.style.display = "block";
    tooltip.style.left = `${event.clientX + 12}px`;
    tooltip.style.top = `${event.clientY + 8}px`;
    tooltip.style.borderColor = network?.color || "#888";
    tooltip.innerHTML = `
        <div class="roi-tooltip-title">${escapeHtml(region.name)}</div>
        <div class="roi-tooltip-net" style="color:${network?.color || "#888"}">
            ${escapeHtml(network?.label || region.network)}
        </div>
    `;
    renderer.domElement.style.cursor = "pointer";
});

renderer.domElement.addEventListener("mouseleave", () => {
    tooltip.style.display = "none";
    renderer.domElement.style.cursor = "default";
});

function selectRegion(regionId) {
    state.selectedRegionId = regionId;
    const region = state.atlas.regions.find((r) => r.id === regionId);
    if (!region) return;
    const network = state.atlas.networks[region.network];

    sidebar.classList.add("open");
    sidebar.innerHTML = `
        <button class="sidebar-close" aria-label="Close">×</button>
        <h3>${escapeHtml(region.name)}</h3>
        <div class="region-tag" style="background:${network.color}; color:#fff">
            ${escapeHtml(network.label)}
        </div>
        <dl class="region-meta">
            <dt>Region ID</dt><dd><code>${escapeHtml(region.id)}</code></dd>
            <dt>Hemisphere</dt><dd>${escapeHtml(region.hemi || "?")}</dd>
            <dt>Brodmann</dt><dd>${escapeHtml(region.brodmann || "—")}</dd>
            <dt>Coordinates</dt><dd>(${region.xyz.map((v) => v.toFixed(2)).join(", ")})</dd>
        </dl>
        <p class="region-narration" id="region-narration">
            Click <strong>Run analysis</strong> on a clip to see this region's
            Gemma-narrated activation here.
        </p>
    `;
    sidebar.querySelector(".sidebar-close").addEventListener("click", () => {
        sidebar.classList.remove("open");
        state.selectedRegionId = null;
    });
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------------------
// Time scrubber — applies BOLD activation to region opacity/emissive
// ---------------------------------------------------------------------------

scrubber.addEventListener("input", () => {
    if (state.bold) applyBoldFrame(parseInt(scrubber.value, 10));
});

// Auto-play: advances the scrubber once every ~120 ms. Toggled by a button.
let playTimer = null;
const playButton = document.getElementById("play-toggle");
function setPlaying(playing) {
    if (playing) {
        if (playTimer) return;
        playButton?.classList.add("playing");
        if (playButton) playButton.textContent = "⏸";
        playTimer = setInterval(() => {
            if (!state.bold) return;
            const max = parseInt(scrubber.max, 10);
            let v = parseInt(scrubber.value, 10) + 1;
            if (v > max) v = 0;
            scrubber.value = String(v);
            applyBoldFrame(v);
        }, 120);
    } else {
        if (playTimer) clearInterval(playTimer);
        playTimer = null;
        playButton?.classList.remove("playing");
        if (playButton) playButton.textContent = "▶";
    }
}
playButton?.addEventListener("click", () => setPlaying(playTimer === null));

function applyBoldFrame(t) {
    if (!state.bold) return;
    const row = state.bold.bold[t];
    if (!row) return;
    scrubberLabel.textContent = `t = ${(t * state.bold.tr_seconds).toFixed(1)}s`;

    for (const region of state.atlas.regions) {
        const mesh = state.regionMeshes.get(region.id);
        if (!mesh) continue;
        const idx = state.boldRegionIndex.get(region.id);
        if (idx === undefined) continue;
        const v = Math.max(-1, Math.min(1, row[idx]));            // clamp [-1,1]
        const intensity = 0.15 + 0.95 * Math.max(0, v);            // map to emissive
        mesh.material.emissiveIntensity = intensity;
        mesh.material.opacity = 0.55 + 0.45 * Math.max(0, v);
        // Subtle grow / shrink so even color-blind viewers get a cue
        const scale = 0.9 + 0.45 * Math.max(0, v);
        mesh.scale.setScalar(scale);
    }
}

async function loadBoldForScan(scanId) {
    try {
        const r = await fetch(`/api/scan/${encodeURIComponent(scanId)}/bold-simulate?n_t=100`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const trace = await r.json();
        state.bold = trace;
        state.boldRegionIndex = new Map(trace.region_ids.map((id, i) => [id, i]));
        scrubber.disabled = false;
        scrubber.max = String(trace.n_t - 1);
        scrubber.value = "0";
        applyBoldFrame(0);
        appendEvent(`BOLD loaded for ${scanId} (${trace.n_t} TRs × ${trace.n_regions} regions)`, "complete");
    } catch (err) {
        appendEvent(`BOLD load failed: ${err.message}`, "failed");
    }
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

let ws;
let reconnectDelay = 1000;

function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/ws`);

    ws.addEventListener("open", () => { setStatus("connected", "Connected"); reconnectDelay = 1000; });
    ws.addEventListener("close", () => {
        setStatus("error", `Disconnected — retrying in ${reconnectDelay / 1000}s`);
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    });
    ws.addEventListener("error", () => setStatus("error", "WebSocket error"));
    ws.addEventListener("message", (event) => {
        try { handleEvent(JSON.parse(event.data)); }
        catch (err) { console.warn("malformed ws message", event.data, err); }
    });
}

function handleEvent(msg) {
    switch (msg.type) {
        case "hello":
            appendEvent(`scheduler ${msg.scheduler_state}, queue depth ${msg.queue.queue_depth}`);
            break;
        case "scheduler_state":
            appendEvent(`GPU state → ${msg.state}`, msg.state === "tribe_active" ? "progress" : "info");
            setStatus(msg.state === "tribe_active" ? "scanning" : "connected", `GPU: ${msg.state}`);
            break;
        case "scan_queued":
            appendEvent(`scan queued: ${msg.filename} (${msg.scan_id})`);
            break;
        case "scan_progress":
            appendEvent(`scan ${msg.scan_id}: ${msg.phase}`, "progress");
            break;
        case "scan_complete":
            appendEvent(`scan ${msg.scan_id} complete ✓`, "complete");
            loadBoldForScan(msg.scan_id);
            break;
        case "scan_failed":
            appendEvent(`scan ${msg.scan_id} failed: ${msg.error?.message ?? "unknown"}`, "failed");
            break;
        default:
            appendEvent(`event: ${msg.type}`);
    }
}

// ---------------------------------------------------------------------------
// Resize + animation loop
// ---------------------------------------------------------------------------

function resize() {
    const { clientWidth: w, clientHeight: h } = root;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);

let theta = 0;
function animate() {
    theta += 0.0015;
    camera.position.x = Math.sin(theta) * 2.6;
    camera.position.z = Math.cos(theta) * 2.6;
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
    requestAnimationFrame(animate);
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

(async function bootstrap() {
    try {
        const resp = await fetch("/api/atlas");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        state.atlas = await resp.json();
        placeRegionMarkers(state.atlas);
        renderNetworkToggles(state.atlas);
        appendEvent(`atlas loaded: ${state.atlas.regions.length} regions, ${Object.keys(state.atlas.networks).length} networks`);
    } catch (err) {
        appendEvent(`atlas load failed: ${err.message}`, "failed");
    }

    resize();
    animate();
    connect();
})();

// ---------------------------------------------------------------------------
// Upload form
// ---------------------------------------------------------------------------

document.getElementById("scan-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.getElementById("scan-file");
    if (!fileInput.files?.length) return;

    submitButton.disabled = true;
    submitButton.textContent = "Submitting…";
    overlay.textContent = "Uploading…";

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("tier", tierInput.value);
    formData.append("source", "webui");

    try {
        const resp = await fetch("/api/scan", { method: "POST", body: formData });
        const body = await resp.json();
        if (!resp.ok) {
            appendEvent(`upload rejected: ${body.message ?? body.error_code}`, "failed");
            overlay.textContent = body.message ?? "Upload failed";
        } else {
            appendEvent(`scan accepted: ${body.scan_id}`, "complete");
            overlay.textContent = `Analyzing scan ${body.scan_id}…`;
            // Optimistically load simulated BOLD so the scrubber works during processing
            loadBoldForScan(body.scan_id);
        }
    } catch (err) {
        appendEvent(`network error: ${err.message}`, "failed");
    } finally {
        submitButton.disabled = false;
        submitButton.textContent = "Analyze";
    }
});
