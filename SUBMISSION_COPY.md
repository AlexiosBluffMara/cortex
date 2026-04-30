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

Demo: [LIVE_URL]
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

**TL;DR**: Submit any video → TRIBE v2 predicts 20,484 cortical vertices firing at 2 Hz → Gemma 4 explains it at three audience levels simultaneously. Mercury orchestrates. Kimi K2.6 wrote the viewer.

---

### What it does

1. Upload any video, audio clip, or image (≤50 MB)
2. **TRIBE v2** (Meta's brain foundation model) predicts BOLD responses at **20,484 fsaverage5 vertices × 2 Hz** — the full cortical surface, not a regional average
3. **Gemma 4 E4B** narrates at three levels in parallel: General (high-school register) · College (named networks) · Clinical (Yeo-7, laterality, peak timing)
4. A Three.js viewer renders per-vertex activation as a live 3D animation with ISU cardinal colormap and adaptive scale bar

The pipeline runs entirely on a single RTX 5090 (32 GB). A GPU scheduler swaps TRIBE v2 and Gemma 4 with OOM recovery — they cannot coexist at 32 GB.

---

### The Kimi Track — exact receipts

Mercury dispatched specs (written by Claude Code) to **Kimi K2.6** via `tools/kimi_dispatch.py`. Kimi wrote the initial cortex viewer — 47 KB of Three.js, the 50-region atlas, the brain mesh pipeline — in a **75-minute, 14-commit sprint on Apr 28**.

| Metric | Value |
|---|---|
| Total Nous Portal spend | **$22.04** |
| Requests | **1,035** |
| Input tokens | **57M** |
| Output tokens | **564K** |
| Cache reads | **39.5M** |

The Apr 28–29 spike maps 1:1 to those 14 commits. Raw session dumps, config screenshots, and the full git log are in `kimi_proof/` in the repo.

---

### Mercury — the agent

- 6 client surfaces: terminal, Discord, web, iMessage, email, mobile
- 4 skill domains: Chicago education · Chicago tax/tenant law · 3D graphics dev · hackathon packaging
- GPU scheduler: IDLE → GEMMA_ACTIVE → TRIBE_ACTIVE (eviction-driven, swap time ~10 s)
- Snowy The Bot is **live right now** in `#bot-test-3` on this server

---

### Links

- **Cortex (TRIBE v2 pipeline)**: https://github.com/AlexiosBluffMara/cortex
- **Mercury (Hermes fork)**: https://github.com/AlexiosBluffMara/mercury
- **Live demo**: [LIVE_URL — replace with deployed URL]
- **Kimi proof**: https://github.com/AlexiosBluffMara/mercury/tree/main/kimi_proof

---

*Built by Red Team Kitchen / Alexios Bluff Mara LLC · Illinois State University 🔴*
```

---

## KAGGLE DISCUSSION POST — GEMMA 4 GOOD SUBMISSION

```
# Cortex: TRIBE v2 + Gemma 4 Brain-Response Analysis (Health & Sciences)

## What we built

**Cortex** predicts how a human brain responds to any video, audio clip, or image — using Meta's TRIBE v2 foundation model and Google's Gemma 4 E4B — and then explains what it found to three audiences simultaneously.

No fMRI machine. No hospital. ~6 minutes per scan on consumer hardware.

## The medical case

Clinical fMRI costs $3,000–$6,000 per session and requires specialized equipment. Population-level BOLD prediction won't replace it. But it opens three research doors that are currently locked:

1. **Educational neuroscience**: which brain networks does a given lecture, training video, or explainer actually engage? Educators can now iterate on content the way UX designers iterate on interfaces.
2. **Rehabilitation research**: does a therapy video reach the target motor or language regions? Quantify it without booking a scanner.
3. **Accessibility**: screen readers and educational tools can now know which cognitive systems a piece of content will engage, not just what it visually contains.

## Technical summary

- TRIBE v2 (Meta, CC-BY-NC 4.0): V-JEPA2 vision + wav2vec audio + Llama-3.2-3B text → predicts (T × 20,484) float32 BOLD at 2 Hz on fsaverage5
- Gemma 4 E4B: multimodal media description → 3-tier narration (General / College / Clinical) at 194 tok/s
- GPU scheduler: swaps TRIBE v2 and Gemma 4 on 32 GB VRAM with OOM recovery
- Three.js viewer: per-vertex real-time 3D animation with adaptive colormap

## Caveats (stated explicitly in the UI)

- TRIBE v2 is trained on 25 subjects — group-averaged prediction, not diagnostic
- Not a substitute for clinical imaging
- All narrations include explicit population-average caveats

## Links

- Code: https://github.com/AlexiosBluffMara/cortex
- Live demo: [LIVE_URL]
- License: MIT (code) · CC-BY-NC 4.0 (TRIBE v2 weights) · Gemma Terms of Use (Gemma 4)
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

Replace `[LIVE_URL]` in all copy above with this URL before posting.

Page title for Google crawl:
**"Cortex — Watch Your Brain Respond to Any Video | TRIBE v2 + Gemma 4 | Free"**

The word "free" in the title drives click-through rate significantly on Google.
