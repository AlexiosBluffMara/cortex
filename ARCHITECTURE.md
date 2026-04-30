# Cortex — System Architecture

## The Full Picture

```
═══════════════════════════════════════════════════════════════════════════════
                         INTERNET (ANYONE, ANYWHERE)
═══════════════════════════════════════════════════════════════════════════════
         │                                    │
         │  BROWSE / VIEW (no login needed)   │  SUBMIT SCAN (Google auth required)
         │                                    │  Must be @philanthropytraders.com
         ▼                                    │  or @redteamkitchen.com
 cortex.redteamkitchen.com ◄─────────────────┘
         │
         │  Cloudflare CDN + DDoS protection (free tier)
         │  CNAME → 10c2805b-...cfargotunnel.com
         │
         ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │               CLOUDFLARE TUNNEL  rtk-5090                       │
 │          4 redundant connections → Chicago PoPs                 │
 │          ord02 / ord06 / ord12 / ord15                          │
 └───────────────────────┬─────────────────────────────────────────┘
                         │  (when 5090 is ONLINE)
                         ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │              RTX 5090 — WINDOWS 11 WORKSTATION                  │
 │                     192.168.0.34                                │
 │                                                                 │
 │   Cortex FastAPI server (:8765)                                 │
 │   ├── POST /api/scan     → GPU scheduler → TRIBE v2 queue       │
 │   ├── GET  /api/scans    → ScanRegistry (in-memory)             │
 │   ├── WS   /api/ws/{id}  → real-time scan progress              │
 │   └── GET  /             → local Three.js viewer                │
 │                                                                 │
 │   ┌─────────────────┐    ┌─────────────────────────────────┐   │
 │   │   TRIBE v2      │    │         Gemma 4 E4B             │   │
 │   │  22.4 GB VRAM   │    │        ~10 GB VRAM              │   │
 │   │  Meta CC-BY-NC  │    │    194 tok/s via Ollama         │   │
 │   │  20,484 verts   │◄──►│   3 narration tiers parallel   │   │
 │   │  2 Hz, 0.5s TR  │    │  General / College / Clinical  │   │
 │   └─────────────────┘    └─────────────────────────────────┘   │
 │          GPU SCHEDULER: IDLE → TRIBE_ACTIVE → GEMMA_ACTIVE      │
 │          Swap time ~10s. OOM recovery. Cannot coexist on 32 GB. │
 └─────────────────────────────────────────────────────────────────┘
                         │
                         │  scan results + BOLD .npy
                         ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                  GOOGLE CLOUD (abm-isu project)                 │
 │                                                                 │
 │   Cloud Run: cortex-relay (us-central1)                        │
 │   ├── Serves gallery.html + scan.html (always online)           │
 │   ├── Proxies /api/scan → 5090 tunnel (primary path)            │
 │   ├── Falls back to queue if 5090 offline                       │
 │   ├── Auth: Google OAuth domain check on POST /api/scan         │
 │   └── File proxy: /api/files/* (avoids public IAM complexity)   │
 │   Scale: 0 → 5 instances, CPU throttled, min-instances=0       │
 │   Cost: $0/hr idle, ~$0.002/request at load                     │
 │                                                                 │
 │   Firestore (native mode, us-central1, free tier)              │
 │   └── scans/{scan_id}: status, narrations, peak_t, GCS paths   │
 │                                                                 │
 │   GCS: cortex-public-scans (us-central1)                       │
 │   ├── uploads/   → auto-deleted after 7 days                   │
 │   ├── bolddata/  → auto-deleted after 30 days                  │
 │   └── thumbnails/ → moved to NEARLINE after 3 days             │
 │                                                                 │
 │   Artifact Registry: cortex/relay:latest                       │
 │   Cloud Build: builds relay image on each push                 │
 │   Billing budgets: $10 early warning, $50 hard cap             │
 └─────────────────────────────────────────────────────────────────┘
                         │
                         │  (FALLBACK — when 5090 AND tunnel offline)
                         ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │         CLOUD GPU FALLBACK (configured, not running 24/7)       │
 │                                                                 │
 │   Cloud Run + L4 GPU: tribe-inference (PREVIEW — allowlist req) │
 │   ├── 24 GB GDDR6 VRAM (vs 32 GB GDDR7 on 5090)               │
 │   ├── Scale to zero — $0/hr idle, ~$0.59/hr active             │
 │   ├── ~90s cold start (GPU warmup + TRIBE model load)           │
 │   └── ~$0.30/scan cloud cost (vs $0.006 local electricity)      │
 │                                                                 │
 │   Gemini 1.5 Flash API (narration fallback)                    │
 │   └── $0.0375/M input tokens, $0.15/M output ≈ $0.001/scan    │
 └─────────────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────────────────────────────────┐
 │                    MERCURY AGENT (Hermes fork)                  │
 │                  Snowy The Bot / ABM Hermes                     │
 │                                                                 │
 │   mercury gateway run -v → Discord (abmsnowy#1566)             │
 │   Surfaces: Discord · Terminal · Web · iMessage · Email         │
 │   Skills: brain-viz · cortex-bridge · discord-bot · education   │
 │   Persona: SOUL.md → direct, autonomous, ISU-aware             │
 │   Repos: github.com/AlexiosBluffMara/mercury                   │
 └─────────────────────────────────────────────────────────────────┘
```

---

## Hardware: RTX 5090 vs Cloud Equivalents

| Spec | Local RTX 5090 | GCP L4 (Cloud Run GPU) | GCP A100 40GB |
|---|---|---|---|
| VRAM | 32 GB GDDR7 | 24 GB GDDR6 | 40 GB HBM2e |
| Memory BW | 1,792 GB/s | 864 GB/s | 1,555 GB/s |
| Architecture | Blackwell sm_120 | Ada Lovelace | Ampere |
| FP16 TFLOPS | ~838 | ~242 | ~312 |
| TRIBE v2 inference | ✅ Primary | ⚠ Fits with quantization | ✅ |
| Gemma 4 E4B | ✅ 194 tok/s | ⚠ ~80 tok/s est. | ✅ ~150 tok/s est. |
| **Purchase cost** | **~$2,500 street** | N/A (rental) | N/A (rental) |
| **Hourly rental** | $0 (already owned) | ~$0.59/hr | ~$2.93/hr |
| **Monthly 24/7** | $0 (+ electricity) | ~$425/mo | ~$2,110/mo |
| **Per scan (6 min)** | ~$0.006 (electricity) | ~$0.06 | ~$0.29 |

**Key insight:** The RTX 5090 pays for itself after ~3,500 cloud-equivalent scans on L4 (~700 on A100).
At 100 scans/month locally: hardware ROI in ~3 years. At 500 scans/month: ROI in ~7 months.

---

## Free Tier Limits — What We Consume vs What's Allowed

| GCP Service | Free Tier Limit | Our Monthly Usage (est.) | Status |
|---|---|---|---|
| Cloud Run | 2M requests, 360K CPU-sec/mo | ~10K requests, ~1K CPU-sec | ✅ Well within free |
| Firestore | 50K reads/day, 20K writes/day, 1 GB | ~100 reads/day, ~20 writes/day | ✅ Free tier |
| GCS storage | 5 GB standard | ~1 GB (lifecycle auto-delete) | ✅ Free tier |
| GCS egress | 1 GB/day free to internet | ~500 MB/day | ✅ Free tier |
| Cloud Build | 120 build-min/day | ~2 min/deploy | ✅ Free tier |
| Artifact Registry | 0.5 GB free | ~0.4 GB (2 images) | ✅ Free tier |
| Cloudflare Tunnel | Unlimited (free product) | All traffic | ✅ Free |
| Cloudflare CDN | Unlimited requests (free) | All gallery traffic | ✅ Free |
| **Total GCP cost** | — | — | **~$0–3/month** |

Budget alerts: **$10** (email warning at $10 spent) + **$50** (email at $25, $40, $50).

---

## Security Model

| Action | Auth Required | Check |
|---|---|---|
| View gallery | None (public) | — |
| View scan profiles | None (public) | — |
| View narrations | None (public) | — |
| Download BOLD data | None (public) | — |
| **Submit a scan job** | **Google OAuth** | `hd` field must be `philanthropytraders.com` or `redteamkitchen.com` |
| Access Cloud Run directly | None (relay is public) | Same auth on POST /api/scan |
| Access 5090 directly | Cloudflare Tunnel | No direct port exposure; tunnel only |

Token verification: Google's public key JWK set, verified with `google-auth` Python library.
Token cache: 5-minute TTL (no Google round-trip on every request).

---

## Google Interview Relevance

This project directly demonstrates skills relevant to **Google Chicago** (Ads, Cloud, YouTube, DeepMind):

| Google Team | How This Is Relevant |
|---|---|
| **Google Cloud AI** | 100% GCP-native stack: Cloud Run, Firestore, GCS, Cloud Build, Artifact Registry, billing budgets, org policy overrides |
| **YouTube / Media AI** | Brain response to video content = novel video understanding signal |
| **Google Ads** | DMN vs salience network response to ad content — the exact thing ad researchers want to quantify |
| **DeepMind (neuroscience)** | TRIBE v2 is Meta's model, but the pattern of foundation-model + probe is identical to DeepMind's neuroscience work |
| **Site Reliability** | Scale-to-zero + cloud-fallback + health monitoring + budget controls = textbook SRE reliability pattern |
| **Developer Relations** | Open source, well-documented, layman-accessible = exactly what DevRel builds demos with |

**Strongest angle for a Google interview**: The architecture is a live demonstration of serverless-first design with graceful degradation — primary compute on local specialized hardware, Cloud Run relay always available, GPU inference on-demand, $0 idle cost. This is the pattern Google uses internally.
