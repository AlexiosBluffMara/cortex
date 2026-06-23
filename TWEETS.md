# Cortex — tweet bank (2026-05-04)

Pick the angle that fits the day. All copy is under 280 chars and includes `https://cortex.redteamkitchen.com` (clean alias) — substitute for the raw Tailscale URL where you need ironclad uptime: `https://redteamkitchen.com`.

---

## Launch / "what is this"

1. **i taught a 5090 to predict your brain on a video.**
   drop any clip in → fMRI-style cortical maps + 4 explainers (student / patient / clinician / ML scientist).
   live demo: https://cortex.redteamkitchen.com
   gemma 4 + tribe v2 + 3d brain, 100% open source.

2. **what does YOUR brain look like watching a NASA launch?**
   cortex predicts cortical activation from raw video, audio, or text — no scanner needed. four readings, one brain.
   try it: https://cortex.redteamkitchen.com

3. **multimodal fmri prediction, fully local on a 5090.**
   v-jepa2 → tribe v2 → gemma 4 (e4b/26b/31b). 4 personas explaining the same result in 4 voices.
   no GPU rental, ~$0.011/scan. https://cortex.redteamkitchen.com

## Tech / build-in-public

4. **stack:**
   • TRIBE v2 (V-JEPA2 + wav2vec-BERT 2.0 + Llama-3.2-3B encoders → 20,484 vertices @ 2 Hz)
   • Gemma 4 narrations (cloud-first 31B via OpenRouter, e4b warm on Sera with TRIBE, 26b warm on M4 Max)
   • 3D brain in Three.js, drag-resize panels via gridstack
   live: https://cortex.redteamkitchen.com

5. **5090 + M4 Max as a 2-node fleet.**
   • Sera does TRIBE + Gemma e4b (multimodal native)
   • Seratonin holds Gemma 26b warm + serves the public
   • Cloudflare Tunnel + Tailscale Funnel for ingress
   • Watchdog auto-heals dead services
   status: https://cortex.redteamkitchen.com/status

6. **parallelised the persona narration loop with `asyncio.gather` + Ollama NUM_PARALLEL=4.**
   went from sequential 4×N seconds → max(N) seconds per scan.
   325 tok/s aggregate on the 5090, 130 tok/s on the M4 Max. cloud-first to OpenRouter for the snappy first byte.

## Demo prompt / community

7. **drop your weirdest 15-second clip and i'll tell you what 4 different people think your brain did watching it.**
   reply with the link and i'll run it.
   https://cortex.redteamkitchen.com

8. **submitted a NASA Artemis launch + a generic action clip to cortex.** got back 4 wildly different explainers from the same brain map. discord embeds + 3D brain + downloadable npy.
   gallery → https://cortex.redteamkitchen.com/gallery.html

## Numbers / proof

9. **20-second video → 4 persona narrations + 3D cortical surface map in ~2-7 minutes.**
   total local cost ≈ $0.011/scan vs ~$0.32 on a GCP L4 instance. fully reproducible, fully open source.
   https://github.com/AlexiosBluffMara/cortex

10. **today's gallery: 11 completed scans, 4 narrations each.**
    https://cortex.redteamkitchen.com/gallery.html

11. **fleet status, real and live:**
    🟢 Sera RTX 5090 — TRIBE warm, Gemma e4b, 11.2 GB VRAM
    🟢 Seratonin M4 Max — Gemma 26b warm, 22 GB
    🟢 OpenRouter — Gemma 4 31B cloud-first
    🟢 Cloudflare Tunnel — public ingress
    https://cortex.redteamkitchen.com/status

## Hot takes / philosophical

12. **"what does the inside of a brain look like" used to require a $3M scanner.**
    cortex predicts it from a 15-second clip on a desktop GPU. open source. the floor of neuroimaging research just dropped through the basement.
    https://cortex.redteamkitchen.com

13. **four people watch the same brain scan and all see different things.**
    the student notices the vibe. the patient wants reassurance. the clinician reads the dynamics. the ML researcher sees the inference graph.
    same data, four lenses. https://cortex.redteamkitchen.com

14. **no scanner. no IRB. no medical claim.**
    cortex is a research-grade prediction of how an AVERAGE brain (n=25, NSD) would respond to your stimulus. it's a public-engagement tool, not a diagnostic.
    that's the whole point. https://cortex.redteamkitchen.com

## Threads (open with the headline tweet, follow up with these)

T1.1 — _why this exists_:
clinical fMRI is locked behind hospital scanners. consumer brain-curiosity shouldn't be. tribe v2 (peer-reviewed: arxiv.org/abs/2407.14076) lets us predict brain responses from any modality — i wrapped it in a UI a high-schooler can use.

T1.2 — _how it works_:
1. you upload video / audio / text / image
2. v-jepa2 / wav2vec-BERT / llama-3.2 encode it into a shared latent
3. tribe v2 maps that latent → 20,484 cortical vertices @ 2 Hz
4. four gemma 4 prompts, run in parallel, narrate the same map for 4 audiences

T1.3 — _what's open_:
the entire stack: cortex + inference router + watchdog + frontend + skills.
github.com/AlexiosBluffMara/cortex
fork it, run your own fleet, submit a PR.

---

## Hashtags by topic

- launch:        `#opensource #neurotech #ai`
- gpu/hardware:  `#rtx5090 #nvidia #applesilicon #m4max`
- multimodal:    `#multimodal #vlm #vJEPA #gemma`
- neuroscience:  `#fmri #neuroscience #brainmapping #cogneuro`
- ISU / regional: `#illinoisstate #goredbirds #chicagotech`
