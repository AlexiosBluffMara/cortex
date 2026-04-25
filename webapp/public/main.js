// Cortex viewer — minimal Three.js placeholder + WebSocket bridge.
// Loaded by webapp/public/index.html. The full Schaefer-400 cortex viewer
// replaces this once webapp/src/ is wired up via Vite.

import * as THREE from "https://unpkg.com/three@0.176.0/build/three.module.js";

const dot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const eventLog = document.getElementById("event-log");
const overlay = document.getElementById("viewer-overlay");
const submitButton = document.getElementById("scan-submit");
const tierInput = document.getElementById("scan-tier");
const tierDisplay = document.getElementById("tier-display");

tierInput.addEventListener("input", () => {
    tierDisplay.textContent = tierInput.value;
});

function setStatus(state, text) {
    dot.className = `dot ${state}`;
    statusText.textContent = text;
}

function appendEvent(message, kind = "info") {
    const li = document.createElement("li");
    li.className = `event-${kind}`;
    const ts = new Date().toLocaleTimeString();
    li.textContent = `[${ts}] ${message}`;
    eventLog.prepend(li);
    while (eventLog.childElementCount > 60) eventLog.removeChild(eventLog.lastChild);
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

let ws;
let reconnectDelay = 1000;

function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/ws`);

    ws.addEventListener("open", () => {
        setStatus("connected", "Connected");
        reconnectDelay = 1000;
    });
    ws.addEventListener("close", () => {
        setStatus("error", `Disconnected — reconnecting in ${reconnectDelay / 1000}s`);
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    });
    ws.addEventListener("error", () => setStatus("error", "WebSocket error"));
    ws.addEventListener("message", (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleEvent(msg);
        } catch (err) {
            console.warn("malformed ws message", event.data, err);
        }
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
            break;
        case "scan_failed":
            appendEvent(`scan ${msg.scan_id} failed: ${msg.error?.message ?? "unknown"}`, "failed");
            break;
        default:
            appendEvent(`event: ${msg.type}`);
    }
}

connect();

// ---------------------------------------------------------------------------
// Three.js placeholder scene
// (replaced with the Schaefer-400 cortex when webapp/src/main.js lands)
// ---------------------------------------------------------------------------

const root = document.getElementById("three-root");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0d12);

const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
camera.position.set(2.6, 1.4, 2.6);
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
root.appendChild(renderer.domElement);

const ambient = new THREE.AmbientLight(0xffffff, 0.55);
const point = new THREE.PointLight(0x6ea8ff, 1.2, 12);
point.position.set(3, 4, 3);
scene.add(ambient, point);

// Wireframe icosahedron — placeholder for the cortex mesh
const geometry = new THREE.IcosahedronGeometry(1.0, 2);
const material = new THREE.MeshStandardMaterial({
    color: 0x6ea8ff,
    metalness: 0.2,
    roughness: 0.4,
    flatShading: true,
    transparent: true,
    opacity: 0.85,
});
const cortex = new THREE.Mesh(geometry, material);
const wireframe = new THREE.LineSegments(
    new THREE.WireframeGeometry(geometry),
    new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.18 })
);
cortex.add(wireframe);
scene.add(cortex);

function resize() {
    const { clientWidth: w, clientHeight: h } = root;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

function animate() {
    cortex.rotation.x += 0.0035;
    cortex.rotation.y += 0.005;
    renderer.render(scene, camera);
    requestAnimationFrame(animate);
}
animate();

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
        }
    } catch (err) {
        appendEvent(`network error: ${err.message}`, "failed");
    } finally {
        submitButton.disabled = false;
        submitButton.textContent = "Analyze";
    }
});
