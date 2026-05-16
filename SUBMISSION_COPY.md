# Cortex — Hackathon Submission Copy
# Optimized for Twitter algorithm, Google SEO, and Nous Discord
# Last updated: 2026-04-30

---

## TWITTER THREAD — EXACT WORDING (4 tweets, post as a reply-chain thread)

Post tweet 1 first with the demo video attached. Then reply to tweet 1 with tweets 2, 3, 4 in sequence.
Tags are placed at END of tweets (algorithm does not penalize end-tags, only inline spam).

---

### TWEET 1 (hero — attach demo video here)

```
We built a tool that predicts how any human brain responds to any video.

Upload a clip. In ~6 min: 20,484 cortical neurons light up in real-time 3D.
A neuroscience AI explains it — to your 8-year-old, your college student, and your neurosurgeon — simultaneously.

Free. Open source. Runs on a single consumer GPU.

🧵

@NousResearch @KaggleCompete
```

**Character count: ~270. Attach the demo video directly.**

---

### TWEET 2 (technical credibility — reply to tweet 1)

```
The stack:

• TRIBE v2 (Meta) predicts 20,484 fsaverage5 cortical vertices at 2 Hz
• Gemma 4 E4B generates three narrations in parallel at 194 tok/s
• GPU scheduler swaps both models on 32 GB VRAM — no cloud, no API cost
• Three.js renders per-vertex BOLD activation in real-time

t = 7 means 3.5 seconds into your brain's response.
t = 11 means 5.5 seconds.

The hemodynamic lag is already corrected.

Code: github.com/AlexiosBluffMara/cortex

@AIatMeta @GoogleDeepMind
```

---

### TWEET 3 (medical implications — reply to tweet 2)

```
The real implication:

A clinical fMRI session costs $3,000–$6,000. It requires a hospital, a radiologist, and 90 minutes of your time.

This runs in 6 minutes on hardware you can buy for $2,000.

It is NOT a diagnostic tool. It is population-averaged across 25 subjects.
But the direction is unmistakable.

Immediate applications we see:
→ Which brain networks does this lecture actually engage?
→ What does this ad do to the salience network vs. the DMN?
→ Does this rehabilitation video reach the right motor regions?

That's worth building.

@OpenNeuro_org @brainhack_org
```

---

### TWEET 4 (submission proof + tags — reply to tweet 3)

```
Built for two hackathons simultaneously.

↗ @NousResearch × @moonshot_ai Creative Hackathon
   Mercury (Hermes fork) orchestrates the pipeline
   Kimi K2.6 wrote the initial Three.js viewer: 14 commits, 75 min, $22.04

↗ @KaggleCompete Gemma 4 Good — Health & Sciences
   Cortex: TRIBE v2 + Gemma 4 from media → cortex → plain English

Demo: https://cortex.redteamkitchen.com
Cortex: github.com/AlexiosBluffMara/cortex
Mercury: github.com/AlexiosBluffMara/mercury

@IllinoisStateU 🔴 #Neuroscience #AI
```

---

## SEO PAGE TITLES + META TAGS (for gallery.html)

```html
<title>Cortex — Free Brain-Response Analysis | TRIBE v2 + Gemma 4</title>
<meta name="description" content="Submit any video, audio, or image. TRIBE v2 predicts cortical BOLD responses at 20,484 fsaverage5 vertices in real-time. Gemma 4 explains the activation to anyone — from curious readers to neurosurgeons. Free, open source, runs on a single GPU.">
<meta property="og:title" content="Cortex — Watch Your Brain Respond to Any Video">
<meta property="og:description" content="TRIBE v2 + Gemma 4: 20,484-vertex cortical activation in real-time 3D. Three narration levels generated in parallel. Free, open source.">
<meta name="keywords" content="TRIBE v2, Gemma 4, brain response, fMRI, cortical activation, BOLD, fsaverage5, neuroscience AI, brain visualization, open source fMRI, free brain scan">
```

---

## NOUS RESEARCH DISCORD — SUBMISSION POST

Post in the hackathon submission channel. Use Discord markdown.

```
## Cortex + Mercury — Nous Research × Kimi Creative Hackathon

**TL;DR**: Your brain is a movie theater with 20,484 seats. Upload any video — TRIBE v2 tracks how every seat reacts, twice per second. Gemma 4 explains it to anyone from a curious kid to a working neurologist. Mercury (our Hermes fork) ran the whole show. Kimi K2.6 built the 3D theater map.

---

### The Brain Cinema, in one paragraph

Imagine a cinema where the audience is your brain — 20,484 people sitting in 20,484 assigned seats, each responsible for a specific cognitive job: faces, voices, motion, language, emotion. When something interesting happens on screen, blood rushes to the excited sections. TRIBE v2 (Meta's brain foundation model) is the sensor system tracking every seat in real-time, at 2 Hz. Gemma 4 is the film critic who reads the reaction printout and explains it at whatever level you need. The Three.js viewer is the live seating chart, colored hot-to-cold.

---

### What it does

1. Upload any video, audio clip, image, or text (≤ 50 MB, ≤ 50 seconds)
2. **TRIBE v2** predicts BOLD activation at **20,484 fsaverage5 cortical vertices × 2 Hz** — the full cortical surface, not a regional average. Five-second hemodynamic lag pre-applied.
3. **Gemma 4 E4B** generates three narrations in parallel at **194 tok/s** on the RTX 5090:
   - **General** — plain language, high-school register
   - **College** — named networks, functional anatomy
   - **Clinical** — Yeo-7 labels, laterality, peak timing, z-scores
4. A **Three.js viewer** renders per-vertex activation as a real-time 3D animation with time scrubber and click-to-inspect on any brain region

Runs entirely on a single RTX 5090 (32 GB GDDR7). A GPU scheduler evicts Gemma to load TRIBE and swaps back in ~10 seconds.

---

### The Kimi receipts

Mercury dispatched specs to **Kimi K2.6** via the Nous Portal. Kimi wrote the initial cortex viewer — 47 KB of Three.js, the 50-region atlas overlay, and the brain mesh pipeline — in a **75-minute, 14-commit sprint on April 28**.

| Metric | Value |
|---|---|
| Total Nous Portal spend | **$22.04** |
| Requests | **1,035** |
| Input tokens | **57M** |
| Output tokens | **564K** |
| Cache reads | **39.5M** |

The April 28–29 spend spike maps 1:1 to those 14 commits. Raw session dumps, config screenshots, and the full git log are in `kimi_proof/` in the repo.

---

### Mercury — the agent behind the curtain

- 6 client surfaces: terminal, Discord, web, WhatsApp, email, mobile
- 4 skill domains: neuroscience narration · 3D graphics dev · Chicago education · hackathon packaging
- GPU scheduler: `IDLE → TRIBE_ACTIVE → GEMMA_ACTIVE` — eviction-driven, OOM recovery, GCP A100 fallback
- **Snowy The Bot is live right now in `#bot-test-3` on this server**

---

### Links

- **Cortex (TRIBE v2 pipeline)**: https://github.com/AlexiosBluffMara/cortex
- **Mercury (Hermes fork)**: https://github.com/AlexiosBluffMara/mercury
- **Live demo**: https://cortex.redteamkitchen.com
- **Kimi proof**: https://github.com/AlexiosBluffMara/mercury/tree/main/kimi_proof

---

*Built by Red Team Kitchen / Alexios Bluff Mara LLC · Illinois State University 🔴*
*Gemma is a trademark of Google LLC.*
```

---

## KAGGLE DISCUSSION POST — GEMMA 4 GOOD SUBMISSION

```
# Cortex: TRIBE v2 + Gemma 4 — Watch Any Brain Respond to Any Video (Health & Sciences)

## The one-sentence version

Cortex lets you upload a short video and see, in real-time 3D, which regions of a human brain respond — then explains what that means to anyone from a curious 8-year-old to a working neurologist.

## The Brain Cinema analogy (for non-technical judges)

Your brain is a movie theater with 20,484 seats. Each seat is staffed by a specialist: some handle faces, some handle voices, some handle motion, some handle fear. When something interesting happens on screen, blood rushes to the excited sections — that rush is the BOLD signal, neuroscience's proxy for "this part of the brain is paying attention."

**TRIBE v2** is the sensor system bolted to every seat, tracking reactions twice per second. **Gemma 4** is the film critic who reads the audience reaction printout and writes a plain-English report. **The 3D viewer** is the live seating chart, colored from cool blue (calm) to hot red (highly activated), updating as the movie plays.

## The medical case

A clinical fMRI session costs $3,000–$6,000, requires specialized hospital equipment, and takes 90 minutes to complete. Population-level BOLD prediction will not replace it. But it opens three research doors that are currently locked:

1. **Educational neuroscience**: which brain networks does a given lecture or training video actually engage? Educators can now iterate on content with real cognitive signal, not just A/B test engagement metrics.
2. **Rehabilitation research**: does a therapy video reach the target motor or language regions? Quantify this without booking a scanner.
3. **Accessibility design**: which cognitive systems does a piece of content activate? Content designers finally have a principled answer.

## Technical summary

- **TRIBE v2** (Meta, CC-BY-NC 4.0): V-JEPA2 vision encoder + wav2vec-BERT 2.0 audio encoder + Llama-3.2-3B text encoder → predicts (T × 20,484) float32 BOLD z-scores at 2 Hz on fsaverage5. Trained on 25 subjects. Five-second HRF lag pre-applied.
- **Gemma 4 E4B**: multimodal media description + three-tier narration (General / College / Clinical) at 194 tok/s on RTX 5090 via Ollama
- **GPU scheduler**: swaps TRIBE v2 (~22.4 GB VRAM) and Gemma 4 E4B (~10 GB VRAM) on 32 GB GDDR7, OOM recovery, GCP L4 cloud fallback
- **Three.js viewer**: per-vertex real-time 3D brain animation with time scrubber, click-to-inspect, Yeo-7 network overlays
- **Hardware**: RTX 5090 (32 GB GDDR7). MSRP ~$1,999. Cloud equivalent: ~$0.70/hr on GCP L4, ~$0/hr scaled to zero.

## Stated caveats (shown explicitly in the UI)

- TRIBE v2 is trained on 25 subjects — group-averaged prediction, not a personal scan
- Not a substitute for clinical imaging under any circumstances
- Predictions cover cortical surface only — no subcortical structures
- All narrations include explicit population-average disclaimers

## Links

- Code: https://github.com/AlexiosBluffMara/cortex
- Live demo: https://cortex.redteamkitchen.com
- License: Apache 2.0 (Cortex code & Gemma 4 weights — https://ai.google.dev/gemma/apache_2) · CC-BY-NC 4.0 (TRIBE v2 weights — non-commercial only) · MIT (Mercury fork)

*Gemma is a trademark of Google LLC. Built by Alexios Bluff Mara LLC (dba Red Team Kitchen) / Illinois State University.*
```

---

## SMALLER ACCOUNTS TO TAG FOR LIVE FEEDBACK

These are the accounts most likely to actually read it, repost, or respond:

### Highest-signal tags (will see it, likely to engage)
| Account | Why |
|---|---|
| @AIatMeta | TRIBE v2 is Meta's model — they'll notice if it's doing something interesting |
| @NousResearch | Hackathon host, already invested |
| @_akhaliq | Posts cool AI demos daily, 100K+ followers, usually replies |
| @paperswithcode | Repost of code + demo = their core use case |
| @OpenNeuro_org | Open neuroimaging data org — aligns exactly with the mission |
| @brainhack_org | Brainhack community — active, will engage on the science |

### Medical/neuroscience-specific
| Account | Why |
|---|---|
| @neuro_data_sci | Neuroscience data science account, smaller, engaged |
| @neurohive | AI + neuroscience coverage |
| @NeuroscienceNew | Popular neuro news, ~800K followers, posts everything |
| @StanfordMed | Large, for broad reach — low response chance but high visibility |

### AI + open source
| Account | Why |
|---|---|
| @karpathy | Active, neuro-adjacent interests, 900K+ followers |
| @ylecun | Meta Chief AI Scientist — TRIBE is Meta's, relevant |
| @hardmaru | Creative AI, visual/neuro intersection |
| @GoogleDeepMind | Gemma 4 is their model |

---

## TIMING STRATEGY

**Best posting window**: Tuesday–Thursday 9–11 AM CDT (when US tech Twitter is most active).

1. Post tweet 1 with video attached
2. Immediately reply with tweet 2 (within 60 seconds — first reply matters for thread surfacing)
3. Reply with tweet 3
4. Reply with tweet 4
5. Quote RT tweet 1 from a second account if available, to seed engagement
6. Post the Discord submission within the same hour as the tweet thread

**Do NOT post all 4 tweets simultaneously** — reply-chain threads surface better when replies come a few minutes apart, as each reply triggers a re-serve of the parent.

---

## DOMAIN + META FOR SEO

Once the live site is up, the canonical URL should be:

`https://cortex.redteamkitchen.com` (Firebase Hosting + Cloudflare DNS)

Replace `https://cortex.redteamkitchen.com` in all copy above with this URL before posting.

Page title for Google crawl:
**"Cortex — Watch Your Brain Respond to Any Video | TRIBE v2 + Gemma 4 | Free"**

The word "free" in the title drives click-through rate significantly on Google.
