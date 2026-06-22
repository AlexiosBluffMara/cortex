import * as THREE from "https://esm.sh/three@0.176.0";
import { GLTFLoader } from "https://esm.sh/three@0.176.0/examples/jsm/loaders/GLTFLoader.js";

const TEST_IMAGE_URL = "/api/gallery/test-image";
const TARGET_FRAMES = 48;
const FRAME_MS = 430;
const BASE = new THREE.Color(0xe6d6d8);

const ATLAS_TO_SCHAEFER = {
  visual: "Vis",
  auditory: "SomMot",
  default_mode: "Default",
  frontoparietal: "Cont",
  somatomotor: "SomMot",
  dorsal_attention: "DorsAttn",
  ventral_attention: "SalVentAttn",
  limbic: "Limbic",
};

const state = {
  ready: null,
  renderer: null,
  scene: null,
  camera: null,
  brainRoot: null,
  meshes: [],
  meshLH: null,
  meshRH: null,
  lhVertCount: 0,
  vertexLabels: null,
  regionNames: [],
  regionNetwork: [],
  atlasNetworkById: new Map(),
};

const previews = new Map();
const visible = new Set();
let observer = null;
let rafStarted = false;
let lastTick = 0;

function escapeAttr(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

function hashString(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function centroidX(mesh) {
  const p = mesh.geometry.attributes.position.array;
  let s = 0;
  for (let i = 0; i < p.length; i += 3) s += p[i];
  return s / (p.length / 3);
}

function addColorBuffer(geom) {
  const n = geom.attributes.position.count;
  const arr = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    arr[i * 3] = BASE.r;
    arr[i * 3 + 1] = BASE.g;
    arr[i * 3 + 2] = BASE.b;
  }
  geom.setAttribute("color", new THREE.BufferAttribute(arr, 3));
}

function zToRGB(z, scale) {
  const raw = z / Math.max(0.0001, scale);
  const t = Math.max(-1, Math.min(1, raw));
  const m = Math.pow(Math.abs(t), 0.65);
  const BR = 0.17, BG = 0.17, BB = 0.23;

  if (t >= 0) {
    if (m < 0.55) {
      const p = m / 0.55;
      return [
        BR + p * (0.965 - BR),
        BG + p * (0.663 - BG),
        BB + p * (0.090 - BB),
      ];
    }
    const p = (m - 0.55) / 0.45;
    return [
      0.965 - p * (0.965 - 0.80),
      0.663 - p * 0.663,
      0.090 - p * 0.090,
    ];
  }

  return [
    BR - m * (BR - 0.337),
    BG - m * (BG - 0.459),
    BB + m * (0.561 - BB),
  ];
}

function absMax(row) {
  let m = 0;
  for (let i = 0; i < row.length; i++) {
    const v = row[i] < 0 ? -row[i] : row[i];
    if (v > m) m = v;
  }
  return Math.max(0.25, Math.min(4, m));
}

function schaeferNetwork(label) {
  return String(label || "").split("_")[2] || "";
}

async function initAssets() {
  if (state.ready) return state.ready;

  state.ready = (async () => {
    const [vertexRes, atlasRes, gltf] = await Promise.all([
      fetch("/assets/vertex_labels.json"),
      fetch("/api/atlas"),
      new Promise((resolve, reject) => {
        new GLTFLoader().load("/assets/brain_fsaverage5.glb", resolve, undefined, reject);
      }),
    ]);
    if (!vertexRes.ok) throw new Error("vertex labels unavailable");
    if (!atlasRes.ok) throw new Error("atlas unavailable");

    const vertexData = await vertexRes.json();
    const verts = vertexData.vertex_labels ?? (Array.isArray(vertexData) ? vertexData : []);
    const labels = vertexData.labels ?? [];
    state.vertexLabels = new Int32Array(verts);
    state.regionNames = labels.slice(1);
    state.regionNetwork = state.regionNames.map(schaeferNetwork);

    const atlas = await atlasRes.json();
    state.atlasNetworkById = new Map(
      (atlas.regions || []).map(r => [r.id, ATLAS_TO_SCHAEFER[r.network] || "Default"])
    );

    state.scene = new THREE.Scene();
    state.scene.background = new THREE.Color(0x050509);
    state.camera = new THREE.PerspectiveCamera(38, 16 / 9, 0.05, 100);
    state.camera.position.set(0.12, 0.12, 2.75);
    state.camera.lookAt(0, 0, 0);

    state.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      preserveDrawingBuffer: true,
    });
    state.renderer.setPixelRatio(1);
    state.renderer.setClearColor(0x050509, 1);

    state.scene.add(new THREE.AmbientLight(0xffffff, 0.85));
    const key = new THREE.DirectionalLight(0xfff8f0, 1.2);
    key.position.set(3, 5, 3);
    state.scene.add(key);
    const fill = new THREE.DirectionalLight(0x90b0ef, 0.55);
    fill.position.set(-4, -1, -3);
    state.scene.add(fill);
    const rim = new THREE.DirectionalLight(0xffffff, 0.2);
    rim.position.set(0, -4, -3);
    state.scene.add(rim);

    state.brainRoot = gltf.scene;
    state.scene.add(state.brainRoot);

    const meshes = [];
    gltf.scene.traverse(n => { if (n.isMesh) meshes.push(n); });
    if (!meshes.length) throw new Error("brain mesh unavailable");
    meshes.sort((a, b) => centroidX(a) - centroidX(b));

    for (const mesh of meshes) {
      const geom = mesh.geometry.clone();
      addColorBuffer(geom);
      mesh.geometry.dispose();
      mesh.geometry = geom;
      mesh.material = new THREE.MeshStandardMaterial({
        vertexColors: true,
        metalness: 0.04,
        roughness: 0.68,
        side: THREE.FrontSide,
      });
    }

    state.meshes = meshes;
    state.meshLH = meshes[0] || null;
    state.meshRH = meshes[1] || null;
    state.lhVertCount = meshes[0]?.geometry.attributes.position.count || 0;
  })();

  return state.ready;
}

async function loadPreviewData(preview) {
  if (preview.loadPromise) return preview.loadPromise;

  preview.loadPromise = (async () => {
    await initAssets();

    if (preview.hasBoldVertex) try {
      const r = await fetch(`/api/scan/${encodeURIComponent(preview.scanId)}/bold-vertex?n_t=${TARGET_FRAMES}`);
      if (r.ok) {
        const nT = parseInt(r.headers.get("X-N-T") || "0", 10);
        const nV = parseInt(r.headers.get("X-N-Vert") || "0", 10);
        const data = new Float32Array(await r.arrayBuffer());
        if (nT && nV && data.length === nT * nV) {
          preview.kind = "per-vertex";
          preview.nT = nT;
          preview.nV = nV;
          preview.data = data;
          setStatus(preview, `${nT} frames x ${nV} vertices`);
          return;
        }
      }
    } catch {
      // Regional fallback below.
    }

    const sim = await fetch(`/api/scan/${encodeURIComponent(preview.scanId)}/bold-simulate?n_t=${TARGET_FRAMES}`);
    if (!sim.ok) throw new Error(`BOLD unavailable (${sim.status})`);
    const trace = await sim.json();
    preview.kind = "regional";
    preview.nT = trace.n_t || (trace.bold || []).length || TARGET_FRAMES;
    preview.regionRows = prepareRegionalRows(trace);
    setStatus(preview, `${preview.nT} regional frames`);
  })().catch(err => {
    preview.error = err;
    showFallback(preview, err.message);
  });

  return preview.loadPromise;
}

function prepareRegionalRows(trace) {
  const regionIds = trace.region_ids || [];
  const regionNetworks = regionIds.map(id => state.atlasNetworkById.get(id) || "Default");
  return (trace.bold || []).map(row => {
    const sums = Object.create(null);
    const counts = Object.create(null);
    for (let i = 0; i < row.length; i++) {
      const key = regionNetworks[i] || "Default";
      sums[key] = (sums[key] || 0) + row[i];
      counts[key] = (counts[key] || 0) + 1;
    }
    const means = Object.create(null);
    for (const key of Object.keys(sums)) means[key] = sums[key] / counts[key];
    return means;
  });
}

function setStatus(preview, text) {
  if (preview.statusEl) preview.statusEl.textContent = text;
}

function resizeCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const w = Math.max(240, Math.round(rect.width * dpr));
  const h = Math.max(135, Math.round(rect.height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  return { w, h };
}

function paintVertex(preview, frame) {
  const rowOff = frame * preview.nV;
  const row = preview.data.subarray(rowOff, rowOff + preview.nV);
  const scale = absMax(row);

  for (const [mesh, offset] of [[state.meshLH, 0], [state.meshRH, state.lhVertCount]]) {
    if (!mesh) continue;
    const cb = mesh.geometry.attributes.color;
    for (let i = 0; i < cb.count; i++) {
      const vi = offset + i;
      const z = vi < preview.nV ? preview.data[rowOff + vi] : 0;
      const [r, g, b] = zToRGB(z, scale);
      cb.setXYZ(i, r, g, b);
    }
    cb.needsUpdate = true;
  }
}

function paintRegional(preview, frame) {
  const row = preview.regionRows[frame] || {};
  let scale = 0.25;
  for (const value of Object.values(row)) {
    const abs = Math.abs(value);
    if (abs > scale) scale = abs;
  }
  scale = Math.max(0.25, Math.min(4, scale));

  for (const [mesh, offset] of [[state.meshLH, 0], [state.meshRH, state.lhVertCount]]) {
    if (!mesh) continue;
    const cb = mesh.geometry.attributes.color;
    for (let i = 0; i < cb.count; i++) {
      const vi = offset + i;
      const ri = vi < state.vertexLabels.length ? state.vertexLabels[vi] : 0;
      const net = ri > 0 ? state.regionNetwork[ri - 1] : "";
      const z = row[net] ?? 0;
      const [r, g, b] = zToRGB(z, scale);
      cb.setXYZ(i, r, g, b);
    }
    cb.needsUpdate = true;
  }
}

function renderPreview(preview, now) {
  if (!preview.canvas.isConnected || preview.error) return;
  if (!preview.data && !preview.regionRows) {
    loadPreviewData(preview);
    return;
  }

  const { w, h } = resizeCanvas(preview.canvas);
  state.renderer.setSize(w, h, false);
  state.camera.aspect = w / h;
  state.camera.updateProjectionMatrix();

  const step = Math.floor((now - preview.startedAt) / FRAME_MS);
  const base = Number.isFinite(preview.peakFrame) ? preview.peakFrame : preview.phase;
  const frame = Math.abs(base + step) % preview.nT;

  if (preview.kind === "per-vertex") paintVertex(preview, frame);
  else paintRegional(preview, frame);

  state.brainRoot.rotation.x = -0.06;
  state.brainRoot.rotation.y = 0.35 + Math.sin((now + preview.phase * 53) * 0.00045) * 0.32;
  state.brainRoot.rotation.z = 0.02;

  state.renderer.render(state.scene, state.camera);
  const ctx = preview.canvas.getContext("2d");
  ctx.clearRect(0, 0, preview.canvas.width, preview.canvas.height);
  ctx.drawImage(state.renderer.domElement, 0, 0, preview.canvas.width, preview.canvas.height);

  const seconds = (frame * 0.5).toFixed(1);
  setStatus(preview, `${preview.kind} · t=${seconds}s`);
}

function showFallback(preview, message) {
  const sourceUrl = preview.wrap.dataset.sourceUrl || TEST_IMAGE_URL;
  const sourceKind = preview.wrap.dataset.sourceKind || "image";
  const label = sourceKind === "image" ? "source image fallback" : "test image fallback";
  preview.wrap.classList.add("source-image");
  preview.wrap.innerHTML = `<img src="${escapeAttr(sourceUrl || TEST_IMAGE_URL)}" alt=""><span class="media-badge">${label}</span><span class="media-status">${escapeAttr(message || "brain render failed")}</span>`;
}

function createPreview(canvas) {
  const wrap = canvas.closest(".brain-preview-wrap");
  const scanId = canvas.dataset.scanId || wrap?.dataset.scanId;
  if (!wrap || !scanId) return null;

  const peakRaw = parseInt(wrap.dataset.peakFrame || "", 10);
  const preview = {
    canvas,
    wrap,
    scanId,
    statusEl: wrap.querySelector(".media-status"),
    peakFrame: Number.isFinite(peakRaw) ? peakRaw : null,
    hasBoldVertex: wrap.dataset.hasBoldVertex === "true",
    phase: hashString(scanId) % TARGET_FRAMES,
    startedAt: performance.now() - (hashString(scanId) % 2500),
    kind: "loading",
    nT: TARGET_FRAMES,
    nV: 0,
    data: null,
    regionRows: null,
    loadPromise: null,
    error: null,
  };
  previews.set(canvas, preview);
  return preview;
}

function wireCanvases() {
  for (const [canvas, preview] of previews.entries()) {
    if (!canvas.isConnected) {
      visible.delete(preview);
      previews.delete(canvas);
    }
  }

  if (!observer) {
    observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const preview = previews.get(entry.target);
        if (!preview) continue;
        if (entry.isIntersecting) {
          visible.add(preview);
          loadPreviewData(preview);
        } else {
          visible.delete(preview);
        }
      }
    }, { rootMargin: "500px 0px" });
  }

  document.querySelectorAll("[data-brain-preview]").forEach(canvas => {
    if (previews.has(canvas)) return;
    const preview = createPreview(canvas);
    if (!preview) return;
    observer.observe(canvas);
  });

  startLoop();
}

function startLoop() {
  if (rafStarted) return;
  rafStarted = true;
  requestAnimationFrame(function tick(now) {
    if (now - lastTick > 220) {
      lastTick = now;
      for (const preview of visible) {
        if (preview.canvas.isConnected) renderPreview(preview, now);
      }
    }
    requestAnimationFrame(tick);
  });
}

window.addEventListener("cortex:gallery-rendered", wireCanvases);
window.addEventListener("resize", () => {
  for (const preview of visible) renderPreview(preview, performance.now());
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", wireCanvases);
} else {
  wireCanvases();
}
