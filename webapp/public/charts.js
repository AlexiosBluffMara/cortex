/* charts.js — data-panel renderers that complement the 3D brain.
 *
 * Three views, hot-swappable via the tab strip:
 *   1. Top-10 ROIs        D3 horizontal bar chart (mean |z| per region)
 *   2. 3D BOLD ribbon     Three.js — one ribbon per Yeo-7 network, time runs into the screen,
 *                         ribbon height = mean |z| at that timepoint
 *   3. Network summary    D3 ranked bars for the strongest Yeo-7 networks
 *
 * Reads from window.lastScanResult and window.tribeBoldData (set by main.js
 * after a scan completes). Re-renders on the `cortex:scan-complete` event.
 */
import * as THREE from "https://esm.sh/three@0.176.0";
import { OrbitControls } from "https://esm.sh/three@0.176.0/examples/jsm/controls/OrbitControls.js";

const NETWORKS = ["Vis", "SomMot", "DorsAttn", "SalVentAttn", "Limbic", "Cont", "Default"];
const NETWORK_COLORS = {
  Vis:           "#1f5cff",     // visual         — blue
  SomMot:        "#ff3a25",     // somatomotor    — red
  DorsAttn:      "#4ad596",     // dorsal attn    — green
  SalVentAttn:   "#F6A917",     // ventral attn   — gold
  Limbic:        "#b85ff0",     // limbic         — magenta
  Cont:          "#56a8ff",     // control        — light blue
  Default:       "#ee2d3f",     // default mode   — cardinal
};
const NETWORK_FULL = {
  Vis:         "Visual",
  SomMot:      "Somatomotor",
  DorsAttn:    "Dorsal Attention",
  SalVentAttn: "Ventral Attention / Salience",
  Limbic:      "Limbic",
  Cont:        "Frontoparietal Control",
  Default:     "Default Mode",
};

function classifyRoi(roi) {
  if (!roi) return "Default";
  const tag = String(roi);
  for (const net of NETWORKS) {
    if (tag.includes("_" + net + "_")) return net;
  }
  return "Default";
}

function shortRoi(roi) {
  return String(roi || "")
    .replace("7Networks_", "")
    .replace(/_/g, " ")
    .replace(/^([LR]H)\s+/, "$1·")
    .trim();
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. Top-10 ROI horizontal bar chart (D3)
// ─────────────────────────────────────────────────────────────────────────────
function renderRoiBars() {
  const mount = document.getElementById("chart-rois");
  if (!mount || typeof d3 === "undefined") return;
  mount.innerHTML = "";

  const result = window.lastScanResult;
  const rois = result?.top_rois || [];
  const z = result?.top_roi_z || result?.top_z || [];

  if (!rois.length) {
    mount.innerHTML = '<div class="empty-state">Submit a scan to see the top regions.</div>';
    return;
  }

  // Use ranks as the value if z-scores aren't surfaced
  const data = rois.slice(0, 10).map((roi, i) => ({
    roi,
    short: shortRoi(roi),
    network: classifyRoi(roi),
    value: z[i] ?? (10 - i),       // fallback: rank-based value
    rank: i + 1,
  }));

  const W = mount.clientWidth || 320;
  const H = mount.clientHeight || 280;
  const m = { top: 6, right: 56, bottom: 8, left: 110 };
  const innerW = Math.max(50, W - m.left - m.right);
  const innerH = Math.max(50, H - m.top - m.bottom);

  const svg = d3.select(mount).append("svg")
    .attr("width", W).attr("height", H)
    .attr("viewBox", `0 0 ${W} ${H}`);

  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);

  const x = d3.scaleLinear()
    .domain([0, d3.max(data, d => d.value) * 1.05])
    .range([0, innerW]);
  const y = d3.scaleBand()
    .domain(data.map(d => d.short))
    .range([0, innerH])
    .padding(0.18);

  // Gradient defs for that 3D shading look
  const defs = svg.append("defs");
  for (const [net, color] of Object.entries(NETWORK_COLORS)) {
    const grad = defs.append("linearGradient")
      .attr("id", `grad-${net}`).attr("x1", "0%").attr("x2", "100%");
    grad.append("stop").attr("offset", "0%").attr("stop-color", d3.color(color).darker(0.5));
    grad.append("stop").attr("offset", "100%").attr("stop-color", d3.color(color).brighter(0.4));
  }

  g.append("g").selectAll("rect")
    .data(data).enter().append("rect")
    .attr("class", "roi-bar")
    .attr("x", 0)
    .attr("y", d => y(d.short))
    .attr("height", y.bandwidth())
    .attr("rx", 3)
    .attr("fill", d => `url(#grad-${d.network})`)
    .attr("stroke", d => d3.color(NETWORK_COLORS[d.network]).brighter(0.5))
    .attr("stroke-width", 0.6)
    .attr("width", 0)
    .transition().duration(420)
    .delay((d, i) => i * 35)
    .attr("width", d => x(d.value));

  g.append("g").selectAll("text.roi-label")
    .data(data).enter().append("text")
    .attr("class", "roi-label")
    .attr("x", -8)
    .attr("y", d => y(d.short) + y.bandwidth() / 2 + 3)
    .attr("text-anchor", "end")
    .text(d => d.short);

  g.append("g").selectAll("text.roi-value")
    .data(data).enter().append("text")
    .attr("class", "roi-value")
    .attr("x", d => x(d.value) + 4)
    .attr("y", d => y(d.short) + y.bandwidth() / 2 + 3)
    .text(d => typeof d.value === "number" ? d.value.toFixed(2) : d.value);
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. 3D BOLD ribbon (Three.js) — one ribbon per Yeo-7 network
// ─────────────────────────────────────────────────────────────────────────────
let _ribbon = { renderer: null, scene: null, camera: null, controls: null, anim: null };

function initRibbonScene(mount) {
  // Cleanup if already initialized
  if (_ribbon.renderer) {
    try { _ribbon.renderer.dispose(); } catch {}
    try { mount.removeChild(_ribbon.renderer.domElement); } catch {}
    if (_ribbon.anim) cancelAnimationFrame(_ribbon.anim);
  }
  const W = mount.clientWidth || 320;
  const H = mount.clientHeight || 240;
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(W, H);
  renderer.setClearColor(0x0a0b0e, 1);
  mount.appendChild(renderer.domElement);

  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, W / H, 0.1, 100);
  camera.position.set(0, 1.4, 4.5);
  scene.add(new THREE.AmbientLight(0xffffff, 0.9));
  const dir = new THREE.DirectionalLight(0xffffff, 0.7);
  dir.position.set(2, 3, 4); scene.add(dir);
  const dir2 = new THREE.DirectionalLight(0x4285F4, 0.3);
  dir2.position.set(-3, -1, -2); scene.add(dir2);

  // Subtle grid floor
  const grid = new THREE.GridHelper(6, 12, 0x262834, 0x14151c);
  grid.position.y = -0.05; scene.add(grid);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.minDistance = 1.5; controls.maxDistance = 12;
  controls.target.set(0, 0.6, 0);
  controls.update();

  _ribbon = { renderer, scene, camera, controls, anim: null };

  function tick() {
    controls.update();
    renderer.render(scene, camera);
    _ribbon.anim = requestAnimationFrame(tick);
  }
  tick();
  return _ribbon;
}

function renderBoldRibbon() {
  const mount = document.getElementById("chart-ribbon");
  if (!mount) return;

  const bold = window.tribeBoldData;  // {n_t, n_regions, trace: Float32Array shape (n_t, n_regions), networks: [str]}
  if (!bold || !bold.trace || !bold.networks) {
    mount.innerHTML = '<div class="empty-state">Submit a scan to see the BOLD time series as a 3D ribbon.</div>';
    return;
  }

  const { renderer, scene } = initRibbonScene(mount);
  if (!renderer) return;

  // Aggregate trace by network: for each timepoint, average |z| over regions in that network
  const nT = bold.n_t;
  const nR = bold.n_regions;
  const series = {};
  NETWORKS.forEach(n => series[n] = new Float32Array(nT));

  const counts = {}; NETWORKS.forEach(n => counts[n] = 0);
  for (let r = 0; r < nR; r++) {
    const net = bold.networks[r] || "Default";
    counts[net] = (counts[net] || 0) + 1;
  }
  for (let t = 0; t < nT; t++) {
    const sums = {}; NETWORKS.forEach(n => sums[n] = 0);
    for (let r = 0; r < nR; r++) {
      const v = Math.abs(bold.trace[t * nR + r]);
      const net = bold.networks[r] || "Default";
      sums[net] += v;
    }
    NETWORKS.forEach(n => {
      series[n][t] = (counts[n] > 0) ? sums[n] / counts[n] : 0;
    });
  }

  // Build one ribbon per network — extruded in Z (time), height = z-score
  const tSpan = 4.5;     // total Z extent
  const xSpacing = 0.32; // horizontal spacing between ribbons
  const baseY = 0;
  const heightScale = 6; // multiply z-scores so they're visible

  NETWORKS.forEach((net, idx) => {
    const xCenter = (idx - (NETWORKS.length - 1) / 2) * xSpacing;
    const halfWidth = xSpacing * 0.42;
    const colorHex = NETWORK_COLORS[net];

    // Build a ribbon as a grid of vertices: (nT-1)*2 quads
    const positions = [];
    const colors = [];
    const indices = [];
    const baseColor = new THREE.Color(colorHex);

    for (let t = 0; t < nT; t++) {
      const z = -tSpan/2 + (t / Math.max(1, nT - 1)) * tSpan;
      const h = baseY + Math.max(0, series[net][t]) * heightScale;
      // Two vertices: left edge (top), right edge (top)
      positions.push(xCenter - halfWidth, h, z);
      positions.push(xCenter + halfWidth, h, z);
      // Color modulated by intensity
      const intensity = Math.min(1, series[net][t] * 4);
      const c = baseColor.clone().multiplyScalar(0.55 + 0.55 * intensity);
      colors.push(c.r, c.g, c.b, c.r, c.g, c.b);
    }
    for (let t = 0; t < nT - 1; t++) {
      const a = t * 2, b = t * 2 + 1, c = t * 2 + 2, d = t * 2 + 3;
      indices.push(a, b, c, b, d, c);
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geom.setAttribute("color",    new THREE.Float32BufferAttribute(colors, 3));
    geom.setIndex(indices);
    geom.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true, side: THREE.DoubleSide,
      metalness: 0.15, roughness: 0.55,
      emissive: baseColor.clone().multiplyScalar(0.18),
    });
    const mesh = new THREE.Mesh(geom, mat);
    scene.add(mesh);

    // Label
    const lblCanvas = document.createElement("canvas");
    lblCanvas.width = 256; lblCanvas.height = 64;
    const cx = lblCanvas.getContext("2d");
    cx.fillStyle = "rgba(13,14,20,0.85)";
    cx.fillRect(0, 0, 256, 64);
    cx.fillStyle = colorHex;
    cx.font = "bold 22px JetBrains Mono, monospace";
    cx.textAlign = "center"; cx.textBaseline = "middle";
    cx.fillText(net, 128, 32);
    const tex = new THREE.CanvasTexture(lblCanvas);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }));
    sprite.position.set(xCenter, -0.18, -tSpan/2 - 0.25);
    sprite.scale.set(0.35, 0.09, 1);
    scene.add(sprite);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Yeo-7 network summary (D3) — ranked contribution bars
// ─────────────────────────────────────────────────────────────────────────────
function renderNetworkSummary() {
  const mount = document.getElementById("chart-networks");
  if (!mount || typeof d3 === "undefined") return;
  mount.innerHTML = "";

  const result = window.lastScanResult;
  const rois = result?.top_rois || [];

  if (!rois.length) {
    mount.innerHTML = '<div class="empty-state">Submit a scan to see the network summary.</div>';
    return;
  }

  // Count + value per network from the top ROIs. Earlier ROIs get more weight.
  const tally = {};
  NETWORKS.forEach(n => tally[n] = 0);
  rois.forEach((roi, i) => {
    const net = classifyRoi(roi);
    tally[net] = (tally[net] || 0) + (rois.length - i);   // weight by rank
  });
  const maxVal = Math.max(1, ...Object.values(tally));
  const data = NETWORKS
    .map(n => ({ net: n, raw: tally[n], val: tally[n] / maxVal }))
    .filter(d => d.raw > 0)
    .sort((a, b) => b.raw - a.raw);

  const W = mount.clientWidth || 320;
  const H = mount.clientHeight || 280;
  const m = { top: 18, right: 20, bottom: 16, left: 92 };
  const rowH = Math.max(24, Math.min(34, (H - m.top - m.bottom) / Math.max(data.length, 1)));
  const innerW = Math.max(80, W - m.left - m.right);

  const svg = d3.select(mount).append("svg")
    .attr("width", W).attr("height", H)
    .attr("viewBox", `0 0 ${W} ${H}`);

  const g = svg.append("g").attr("transform", `translate(${m.left},${m.top})`);

  g.selectAll("rect.network-track")
    .data(data).enter().append("rect")
    .attr("class", "network-track")
    .attr("x", 0)
    .attr("y", (_, i) => i * rowH + 6)
    .attr("width", innerW)
    .attr("height", Math.max(8, rowH - 14))
    .attr("rx", 4)
    .attr("fill", "rgba(255,255,255,0.07)");

  g.selectAll("rect.network-bar")
    .data(data).enter().append("rect")
    .attr("class", "network-bar")
    .attr("x", 0)
    .attr("y", (_, i) => i * rowH + 6)
    .attr("height", Math.max(8, rowH - 14))
    .attr("rx", 4)
    .attr("fill", d => NETWORK_COLORS[d.net])
    .attr("width", 0)
    .transition().duration(500)
    .delay((_, i) => i * 55)
    .attr("width", d => Math.max(8, innerW * d.val));

  g.selectAll("text.network-label")
    .data(data).enter().append("text")
    .attr("class", "roi-label")
    .attr("x", -10)
    .attr("y", (_, i) => i * rowH + rowH / 2)
    .attr("text-anchor", "end")
    .attr("dy", "0.32em")
    .text(d => d.net);

  g.selectAll("text.network-value")
    .data(data).enter().append("text")
    .attr("class", "roi-value")
    .attr("x", d => Math.min(innerW - 4, Math.max(34, innerW * d.val + 7)))
    .attr("y", (_, i) => i * rowH + rowH / 2)
    .attr("dy", "0.32em")
    .text(d => `${Math.round(d.val * 100)}%`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Tab wiring + render dispatch
// ─────────────────────────────────────────────────────────────────────────────
function activateTab(name) {
  document.querySelectorAll(".data-tab").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === name)
  );
  document.querySelectorAll(".data-pane").forEach(p =>
    p.classList.toggle("active", p.id === "pane-" + name)
  );
  // Render the now-visible chart (no-op if data not present)
  if (name === "rois")   renderRoiBars();
  if (name === "ribbon") renderBoldRibbon();
  if (name === "networks") renderNetworkSummary();
}

document.querySelectorAll(".data-tab").forEach(btn => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

window.cortexCharts = {
  renderAll() { renderRoiBars(); renderBoldRibbon(); renderNetworkSummary(); },
  renderRoiBars, renderBoldRibbon, renderNetworkSummary, activateTab,
};

// Re-render on scan completion
window.addEventListener("cortex:scan-complete", () => {
  setTimeout(() => window.cortexCharts.renderAll(), 50);
});

// Also re-render on window resize (so 3D scene fits)
let _resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => window.cortexCharts.renderAll(), 250);
});

// First paint: try once after DOM ready (will show empty states if no scan yet)
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => window.cortexCharts.renderAll());
} else {
  setTimeout(() => window.cortexCharts.renderAll(), 100);
}
