# Cortex — Posting & Video Production Schedule

## Deadlines
- **Kaggle Gemma 4 Good**: May 18, 2026
- **Nous Research × Kimi Creative Hackathon**: May 18, 2026 (confirm with Discord)
- **Best Twitter posting window**: Tuesday–Thursday, 9–11 AM CDT

---

## Video Production Plan

### VIDEO 1 — "Local Demo" (the raw 5090 walkthrough)
**Purpose**: Show judges and viewers the unfiltered thing. This is your "private IMAX theater" moment.
**Length**: 2–3 minutes
**Shot list** (record in this order):

1. **(0:00–0:15) Hook shot** — Terminal window with `mercury gateway run -v` connecting. Cut to browser tab loading `localhost:8765`. Text overlay: "No cloud. No API. Just the GPU in this room."

2. **(0:15–0:45) Upload** — Show the drag-drop file picker. Drop a short video clip (NASA Artemis rollout footage works, or a 15-second clip of a face talking). Drag, drop, watch the progress appear: "Analyzing media... Running TRIBE v2... Generating narrations (General / College / Clinical)..."

3. **(0:45–1:45) Brain Cinema moment** — The Three.js viewer loads. The brain rotates. Scrub to t=7 (the peak frame). Show the colormap: blue → orange → red. Click on the visual cortex region — tooltip shows "Primary Visual Cortex — Processes raw visual input before higher-order interpretation." Then zoom in. Then press play and let it run for ~15 seconds in real-time.

4. **(1:45–2:15) Three tiers** — Click through General / College / Clinical tabs. Linger 3 seconds on each. The text itself is the demo. No voiceover needed here — just the text appearing.

5. **(2:15–2:30) Outro** — Terminal showing the model stats: "TRIBE v2 — 20,484 vertices, 2 Hz, 360 timepoints." Cut to GitHub page. Text overlay: "Free. Open source. github.com/AlexiosBluffMara/cortex"

**Recording tips**:
- 1440p or 4K, 60fps
- Use OBS with windowed capture (not full screen) — keeps UI crisp
- No audio needed for tweet version; add light ambient music for YouTube
- Capture the terminal in a separate track for B-roll

---

### VIDEO 2 — "Cloud Demo" (the public site walkthrough)
**Purpose**: Show it works from anywhere — the gallery, past scans, submit from a phone.
**Length**: 90 seconds
**Shot list**:

1. **(0:00–0:10)** Open `https://cortex.redteamkitchen.com` on a phone (or show two devices: phone + laptop). "Same URL. Different continent. Same 5090."

2. **(0:10–0:30)** Show the gallery page — past scan cards with brain thumbnails, region pills, timestamps. Scroll through. Click one. Scan profile opens.

3. **(0:30–0:60)** Sign in with Google button (use the @philanthropytraders.com account). Upload a new file. Watch the "Queued → Processing → Complete" flow. Show the loading state updating in real time via WebSocket.

4. **(0:60–1:30)** New scan completes. Gallery refreshes. New card appears with thumbnail. Click it. Three.js viewer loads from GCS data. Show all three narration tabs.

---

### VIDEO 3 — "Architecture Explainer" (for the Discord + Kaggle submission)
**Purpose**: Judges who know ML want to see you understand your own stack.
**Length**: 60 seconds, screenshare only, no face cam
**Shot list**:

1. Show the ARCHITECTURE.md diagram, walk through it verbally: "Local 5090 is primary. Cloudflare tunnel routes the internet to port 8765. Cloud Run is the always-on public face. If the 5090 goes down, the gallery stays up. Scans queue. Gemini Flash handles narration. L4 GPU handles inference. $0 idle cost."

2. Show the budget alerts in the GCP Console.

3. Show the Firestore scan records live.

4. Show the Cloud Run service metrics (scale-to-zero graph).

---

## Posting Schedule

### T-7 days (May 11, Sunday)
- [ ] Record VIDEO 1 (local demo). Target 2:30 runtime.
- [ ] Record VIDEO 2 (cloud demo). Target 1:30 runtime.
- [ ] Finalize `cortex.redteamkitchen.com` DNS (add CNAME in Squarespace / complete Cloudflare transfer)
- [ ] Create Google OAuth Client ID in GCP Console → run `./gcp/cloud-gpu-config.sh oauth <CLIENT_ID>`
- [ ] Hard-refresh browser, confirm gallery page looks right with auth button

### T-5 days (May 13, Tuesday — PRIME TWITTER WINDOW)
**9:00 AM CDT — Post tweet thread** (exact copy in SUBMISSION_COPY.md):
- [ ] Tweet 1 + VIDEO 1 attached (hero, full pipeline)
- [ ] Reply with Tweet 2 (within 60 seconds of Tweet 1)
- [ ] Reply with Tweet 3 (2 minutes after Tweet 2)
- [ ] Reply with Tweet 4 with `[LIVE_URL]` = `https://cortex.redteamkitchen.com` (2 min after Tweet 3)
- [ ] If second account available: Quote RT Tweet 1 to seed engagement

**Same hour — Post Nous Discord submission**:
- [ ] Paste the Nous Discord post from SUBMISSION_COPY.md into the hackathon submission channel
- [ ] Link to tweet thread in the Discord post
- [ ] Make sure Snowy The Bot is online in #bot-test-3 for live demos

### T-3 days (May 15, Thursday)
- [ ] Post Kaggle discussion post (SUBMISSION_COPY.md section)
- [ ] Tag @paperswithcode on Twitter with the Kaggle post link
- [ ] Send VIDEO 3 (architecture explainer) as a follow-up reply to the original tweet thread

### T-1 day (May 17, Saturday)
- [ ] Final check: `cortex.redteamkitchen.com` loads, gallery shows scans, submit works with domain auth
- [ ] Verify Nous Discord submission is in the right channel
- [ ] Verify Kaggle submission is filed under the correct competition

### Day of (May 18)
- [ ] Submit final GitHub commit with clean README, ARCHITECTURE.md, SUBMISSION_COPY.md
- [ ] Kaggle: submit the final notebook/code link
- [ ] Nous: confirm submission is received

---

## Twitter Tag Strategy

Post Tweet 1 with VIDEO 1. Use exactly these tags at the END (not inline):

```
@NousResearch @KaggleCompete
```

Tag these accounts in replies or via @mention in Quote RTs — do NOT cram into the original thread:
- `@AIatMeta` — TRIBE v2 is Meta's model, high engagement probability
- `@_akhaliq` — posts cool AI demos daily, 100K+ followers, often replies
- `@paperswithcode` — if you have a linked paper or model card
- `@OpenNeuro_org` — neuroimaging community, will engage on the science
- `@brainhack_org` — active community, will share
- `@karpathy` — neuro-adjacent interests; long shot but worth the tag
- `@GoogleDeepMind` — Gemma 4 is their model

**Timing rule**: Space replies 2–3 minutes apart. Each reply re-serves the parent tweet to new users. Posting all 4 simultaneously kills the compounding effect.

---

## Email / Domain Alias Setup (post Cloudflare transfer)

Once `redteamkitchen.com` is on Cloudflare:
1. Go to `dash.cloudflare.com` → Email → Email Routing → Enable
2. Add routing rules:
   - `hello@redteamkitchen.com` → forward to `soumitlahiri@philanthropytraders.com`
   - `soumit@redteamkitchen.com` → forward to `soumitlahiri@philanthropytraders.com`
   - `snowy@redteamkitchen.com` → forward to `soumitlahiri@philanthropytraders.com` (bot-facing)
   - Catch-all: `*@redteamkitchen.com` → forward to `soumitlahiri@philanthropytraders.com`
3. Add MX records (Cloudflare adds these automatically when Email Routing is enabled)
4. Test with a send from external account

**For sending as `soumit@redteamkitchen.com`** (so replies come from the branded domain):
- In Gmail → Settings → Accounts → Add another email address
- SMTP: `smtp.gmail.com`, port 587, use your existing Gmail credentials
- From name: "Soumit Lahiri (Red Team Kitchen)"

---

## Domain Transfer: Squarespace → Cloudflare

### Why transfer?
- Squarespace minimum TTL = 30 minutes (kills rapid DNS debugging)
- Cloudflare minimum TTL = 1 second (instant propagation)
- Cloudflare registrar = at-cost pricing (~$9.15/yr for .com vs Squarespace markup)
- Cloudflare = free email routing, CDN, DDoS, tunnel, analytics in one place

### Transfer steps (takes 5–7 days for full transfer; nameserver change is instant):

**STEP 1: Immediately (tonight) — Change nameservers in Squarespace**
This routes DNS through Cloudflare immediately, even before the registrar transfer completes.

1. Go to `dash.cloudflare.com` → Add a Site → type `redteamkitchen.com`
2. Choose Free plan → Cloudflare scans your existing DNS records (it should find them automatically)
3. Cloudflare gives you two nameservers, e.g.:
   - `esperanza.ns.cloudflare.com`
   - `ken.ns.cloudflare.com`
   (Yours will be different — Cloudflare assigns a specific pair per account)
4. Go to `account.squarespace.com/domains` → `redteamkitchen.com` → DNS Settings → **Nameservers**
5. Change to "Custom nameservers" → paste Cloudflare's two NS records
6. Save. DNS propagates globally within 1–24 hours (usually ~30 min)

After nameserver change: your Cloudflare dashboard controls all DNS. TTL can be 1 second.
The CNAME records for `cortex.redteamkitchen.com` and `mercury.redteamkitchen.com` that you added in Squarespace are now in Cloudflare (it imported them during scan). They should already be there — verify in Cloudflare's DNS panel.

**STEP 2: Start registrar transfer (optional, can wait until after hackathon)**
1. In Squarespace: unlock the domain (Domain Lock → off)
2. Get transfer auth code (EPP code)
3. In Cloudflare: Registrar → Transfer → enter `redteamkitchen.com` + EPP code
4. Pay (~$9.15 for the year)
5. Approve transfer email (goes to your registrar contact address)
6. Transfer completes in 5–7 days; DNS is already working via Cloudflare the whole time

**The nameserver change in Step 1 is all you need before May 18.**
