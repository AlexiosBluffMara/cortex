# Cortex — Hermes update post

> Paste this verbatim into `#nous-research-hermes` as an update to the initial submission.

---

🧠 **Cortex update — multimodal brain-response prediction, fully local on a 5090**

It's live, it's faster, and the stack is fully on-fleet now.

**What it does**
Drop any video / audio / image / PDF / text file into the demo. TRIBE v2 (V-JEPA2 + wav2vec-BERT 2.0 + Llama-3.2-3B → 20,484-vertex fsaverage5 cortical surface @ 2 Hz) predicts the BOLD response. Then four Gemma 4 personas read the same map back to you in four voices — student / patient / clinician / ML scientist — each anchored to a real institution in the Bloomington-Normal / Chicago corridor.

**Try it**
• Demo: https://cortex.redteamkitchen.com
• Gallery: https://cortex.redteamkitchen.com/gallery.html
• Personas: https://cortex.redteamkitchen.com/personas.html
• Specs: https://cortex.redteamkitchen.com/specs.html
• Live fleet status: https://cortex.redteamkitchen.com/status
• Source: https://github.com/AlexiosBluffMara/cortex
• Discord: drop a file here with the word "scan" or `/scan <file>` — Snowy will reply with the full 4-embed result + brain screenshot.

**What's new since the last post**
• Cloud-first inference via OpenRouter (Gemma 4 31B free tier, ~840 ms TTFB)
• 26B / e4b warm locally on the M4 Max + 5090 respectively, with full failover
• Persona narrations now run in parallel (4 in-flight via `asyncio.gather` + Ollama `NUM_PARALLEL=4`) — went from sequential 4×N seconds to max(N) seconds
• 5090 sustains ~325 tok/s aggregate, M4 Max ~130 tok/s
• Fleet watchdog auto-restarts dead services on the 5090
• ISU-themed Apple-style frontend with drag-resize panels (gridstack) — every section is collapsible, layout persists per-user
• Distinct, stimulus-specific narrations — each persona now leads with a concrete observation about THE FILE, not a generic "the brain shows…"

**Numbers**
• Local cost: ~$0.011 / scan (5090 amortised) vs ~$0.32 on a GCP L4
• Two-node fleet: Seratonin (RTX 5090, Chicago) + Seratonin (M4 Max, Chicago)
• Cloudflare Tunnel + Tailscale Funnel for public ingress
• 11+ scans in the gallery, 4 narrations each, all reachable from one URL

**Stack**
TRIBE v2 (peer-reviewed, [arxiv.org/abs/2407.14076](https://arxiv.org/abs/2407.14076)) → Gemma 4 (e4b/26b/31b) → Three.js 3D brain → Discord bot via REST → fully open source.

**Sample files included**: `assets/demo_clip_20s.mp4` and `assets/nasa_artemis_15s_silent.mp4` — clone the repo and run `scripts/auto_demo_video.py` to reproduce the entire end-to-end demo (recording + Discord posts + edited video).

This is a research-grade prediction of how an AVERAGE brain (n=25, NSD training pool) would respond to your stimulus. Not a diagnostic, not patient-specific imaging — a public-engagement and translational-neuroscience tool, on purpose.
