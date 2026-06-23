# `redteamkitchen.com` — public landing site

Static HTML for the Red Team Kitchen umbrella brand
(Alexios Bluff Mara LLC). No build step, no JavaScript framework — just
HTML, one shared stylesheet, and a single SVG favicon. Every page links
to Google Fonts and to `/assets/style.css`.

```
website/
  index.html              landing page (umbrella)
  cortex.html             /cortex   — Brain Cinema short-form
  mercury.html            /mercury  — Six-Door Office short-form
  research.html           /research — research posture, ACCESS-CI
  contact.html            /contact  — email + ISU directory links
  assets/
    style.css             shared stylesheet (cardinal + code)
    favicon.svg           original RTK monogram (NOT an ISU mark)
    placeholder.png       Cortex viewer screenshot placeholder
  _redirects              Cloudflare Pages pretty-path rewrites
  _headers                Cloudflare Pages security headers (CSP)
  README.md               this file
```

## Brand notes

- **Colors and fonts** come from Illinois State University's published
  brand values (Cardinal Red `#CC0000`, ISU Gold `#F6A917`, ISU Blue
  `#56758f`; Open Sans / PT Serif / JetBrains Mono via Google Fonts).
  These values are public and free to reference.
- **No ISU logos, wordmarks, or seals** — those are trademarks. The site
  uses text-only references plus the published palette.
- **Framing**: "research conducted in association with Illinois State
  University," not "official ISU project." ABM LLC is not officially
  affiliated with ISU.
- **Footer disclaimer** is on every page: "Brand accent colors reference
  Illinois State University's published values; ISU logos and wordmarks
  are not used."

## Deploy to Cloudflare Pages

DNS for `redteamkitchen.com` already lives on Cloudflare, so SSL +
custom domain provisioning is automatic.

1. Push the `website/` directory to `main` on the cortex repo:

   ```bash
   cd /d/cortex
   git add website/
   git commit -m "website: redteamkitchen.com landing site (Cloudflare Pages)"
   git push origin main
   ```

2. In the Cloudflare dashboard:
   - **Workers & Pages → Create → Pages → Connect to Git**
   - Pick `AlexiosBluffMara/cortex` and authorize the GitHub app for the
     org if prompted.
   - **Project name:** `redteamkitchen` (or anything — only used for the
     `*.pages.dev` preview hostname).
   - **Production branch:** `main`
   - **Framework preset:** None.
   - **Build command:** *(leave blank)*
   - **Build output directory:** `website`
   - Click **Save and Deploy**. First deploy takes ~30 seconds.

3. Cloudflare Pages now auto-deploys on every push to `main` that touches
   `website/`. Preview deployments fire on pull requests automatically.

4. Add the custom domain:
   - **Pages project → Custom domains → Set up a custom domain**
   - Add `redteamkitchen.com` (apex). Cloudflare auto-provisions the
     CNAME flattening record and the SSL cert because DNS is already on
     Cloudflare.
   - Add `www.redteamkitchen.com` the same way.

5. Verify the routes work:
   - `https://redteamkitchen.com/` → landing page
   - `https://redteamkitchen.com/cortex` → cortex.html (via `_redirects`)
   - `https://redteamkitchen.com/mercury` → mercury.html
   - `https://redteamkitchen.com/research` → research.html
   - `https://redteamkitchen.com/contact` → contact.html

## Cortex subdomain architecture

`redteamkitchen.com` is the durable marketing/gallery surface on Cloudflare
Pages. `cortex.redteamkitchen.com` is the live Cortex app surface and should be
Cloudflare-controlled too, either as its own Pages project with a Worker route
on `/api/*` or as a Cloudflare Tunnel route to Seratonin while the PC is online.

What to verify after a Pages deploy:

- `dig redteamkitchen.com`        → Cloudflare Pages IPs
- `dig www.redteamkitchen.com`    → Cloudflare Pages IPs
- `dig cortex.redteamkitchen.com` → Cloudflare-owned Pages/Tunnel target, not a retired Firebase/Tailscale route

If Seratonin is offline, the marketing site and exported gallery should still
load. New live scans require Seratonin or a configured cloud TRIBE worker.

## Replacing the screenshot placeholder

`website/assets/placeholder.png` is a generic stand-in for the Cortex
viewer. To swap in a real screenshot:

```bash
cp /path/to/real-cortex-screenshot.png /d/cortex/website/assets/placeholder.png
cd /d/cortex
git add website/assets/placeholder.png
git commit -m "website: real Cortex viewer screenshot"
git push origin main
```

Cloudflare Pages will redeploy in ~30 seconds.

## Local preview

No build step needed — any static file server works.

```bash
cd /d/cortex/website
python -m http.server 8000
# open http://localhost:8000
```

Or with `npx`:

```bash
cd /d/cortex/website
npx --yes serve -p 8000 .
```

## License

Site copy and CSS: MIT (matches Mercury). Reuse with attribution to
Alexios Bluff Mara LLC.
</content>
</invoke>
