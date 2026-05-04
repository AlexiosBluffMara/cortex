/* Cortex shared top-nav — injects a consistent route bar into every public
 * page so users can hop between Demo / Gallery / Personas / Specs / Status.
 * Pulled in by gallery.html / personas.html / specs.html / status.html;
 * the demo page (index.html) has its own inline nav baked into the topbar.
 *
 * Idempotent: bail out if .cortex-nav is already in the DOM.
 */
(function () {
  if (document.querySelector(".cortex-nav")) return;

  // ── Style ─────────────────────────────────────────────────────────────────
  const css = `
  .cortex-nav-host {
    position: sticky; top: 0; z-index: 999;
    display: flex; align-items: center; gap: 16px;
    padding: 10px 18px;
    background: rgba(11, 13, 20, 0.78);
    backdrop-filter: blur(20px) saturate(160%);
    -webkit-backdrop-filter: blur(20px) saturate(160%);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    font-family: "Open Sans", system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  }
  .cortex-nav-host a.brand {
    display: flex; align-items: center; gap: 8px;
    color: #F2F4F8; text-decoration: none;
    font-weight: 800; font-size: 15px; letter-spacing: -0.02em;
  }
  .cortex-nav-host a.brand .mark {
    background: linear-gradient(135deg, #CC0000 0%, #861F41 50%, #6B0F2A 100%);
    -webkit-background-clip: text; background-clip: text;
    color: transparent; font-size: 20px;
    filter: drop-shadow(0 1px 8px rgba(204,0,0,0.5));
  }
  .cortex-nav {
    display: flex; gap: 4px; align-items: center;
    padding: 4px;
    background: rgba(255,255,255,0.03);
    border-radius: 999px;
    border: 1px solid rgba(255,255,255,0.07);
  }
  .cortex-nav .nav-link {
    display: inline-flex; align-items: center;
    padding: 6px 14px;
    border-radius: 999px;
    font-size: 12px; font-weight: 600;
    color: #97A0AE; text-decoration: none;
    transition: color 160ms cubic-bezier(0.16,1,0.3,1),
                background 200ms cubic-bezier(0.16,1,0.3,1),
                transform 180ms cubic-bezier(0.34,1.56,0.64,1);
    letter-spacing: 0.01em;
  }
  .cortex-nav .nav-link:hover { color: #F2F4F8; background: rgba(255,255,255,0.04); }
  .cortex-nav .nav-link[aria-current="page"] {
    background: linear-gradient(135deg, #CC0000 0%, #861F41 50%, #6B0F2A 100%);
    color: #fff;
    box-shadow: 0 0 0 1px rgba(204,0,0,0.32), 0 6px 24px rgba(204,0,0,0.18);
  }
  @media (max-width: 720px) {
    .cortex-nav-host { padding: 8px 12px; gap: 8px; }
    .cortex-nav .nav-link { padding: 5px 10px; font-size: 11px; }
  }
  @view-transition { navigation: auto; }
  ::view-transition-old(root) {
    animation: cd-vt-out 220ms cubic-bezier(0.16,1,0.3,1) both;
  }
  ::view-transition-new(root) {
    animation: cd-vt-in 320ms cubic-bezier(0.16,1,0.3,1) both;
  }
  @keyframes cd-vt-out {
    to { opacity: 0; transform: translateY(-6px) scale(.995); filter: blur(2px); }
  }
  @keyframes cd-vt-in {
    from { opacity: 0; transform: translateY(8px) scale(1.005); filter: blur(2px); }
    to   { opacity: 1; transform: none; filter: none; }
  }
  `;
  const style = document.createElement("style");
  style.id = "cortex-nav-style";
  style.textContent = css;
  document.head.appendChild(style);

  // ── DOM ───────────────────────────────────────────────────────────────────
  const ROUTES = [
    { path: "/demo",     label: "Demo",     id: "demo" },
    { path: "/gallery",  label: "Gallery",  id: "gallery" },
    { path: "/personas", label: "Personas", id: "personas" },
    { path: "/specs",    label: "Specs",    id: "specs" },
    { path: "/status",   label: "Status",   id: "status" },
  ];

  const path = location.pathname.replace(/\/$/, "") || "/";
  const route = path === "/" ? "demo"
              : path.startsWith("/demo")     ? "demo"
              : path.startsWith("/gallery")  ? "gallery"
              : path.startsWith("/personas") ? "personas"
              : path.startsWith("/specs")    ? "specs"
              : path.startsWith("/status")   ? "status"
              : null;

  const host = document.createElement("header");
  host.className = "cortex-nav-host";
  host.setAttribute("data-cortex-nav", "1");

  const brand = document.createElement("a");
  brand.className = "brand";
  brand.href = "/";
  brand.innerHTML = `<span class="mark">⌬</span><span>Cortex</span>`;
  host.appendChild(brand);

  const nav = document.createElement("nav");
  nav.className = "cortex-nav";
  nav.setAttribute("aria-label", "Site sections");
  ROUTES.forEach(r => {
    const a = document.createElement("a");
    a.className = "nav-link";
    a.href = r.path;
    a.dataset.route = r.id;
    a.textContent = r.label;
    if (r.id === route) a.setAttribute("aria-current", "page");
    nav.appendChild(a);
  });
  host.appendChild(nav);

  // Insert at the very top of <body>, replacing any existing on-page topbar
  // wouldn't be nice — keep the existing topbar BELOW our shared nav.
  if (document.body) {
    document.body.insertBefore(host, document.body.firstChild);
  } else {
    document.addEventListener("DOMContentLoaded", () => {
      document.body.insertBefore(host, document.body.firstChild);
    });
  }
})();
