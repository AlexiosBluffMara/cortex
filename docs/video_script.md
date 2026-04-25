# Cortex — 3-Minute Demo Video Script

> **Status:** Draft 1, shot list. Re-record after the Three.js cortex mesh swap
> (`buildBrainMesh()` → real `brain.glb`) and after at least one demo clip
> from `scripts/demo_clips.yaml` has run end-to-end.
>
> Hard targets:
> - **Total runtime:** 2:55 ± 0:05.
> - **First payoff frame** (brain visibly responding to a stimulus): before 0:30.
> - **Trademark line on screen** at 0:05–0:08 *and* in the credits.

---

## Cold open · 0:00 – 0:20 · "Hook"

| Shot type | On screen | Voiceover |
| --- | --- | --- |
| Talking head, eye-contact framing | Soumit at desk; RTX 5090 box visible behind | "Imagine watching a video and seeing exactly which parts of your brain light up — in real time." |
| Cut to viewer screen recording | The Three.js cortex animating in response to a stimulus, time scrubber moving | "That's what Cortex does. Locally. On one GPU." |

**Lower-third overlay** (`0:05–0:08`):
> Cortex by Alexios Bluff Mara LLC. Gemma is a trademark of Google LLC.

**Edit note:** kill any music here. Talking-head opens dry; music kicks in at 0:20.

---

## The problem · 0:20 – 0:50 · "Why this is hard"

| Shot type | On screen | Voiceover |
| --- | --- | --- |
| Slow zoom on a TRIBE v2 BOLD heatmap (random gradient) | Title card: *"TRIBE v2 predicts your cortex's response to any video. Output: 20,484 numbers per second."* | "Meta's TRIBE v2 is a remarkable scientific tool. Feed it a clip; it predicts your cortical response. The catch: its output is twenty thousand floating-point numbers." |
| Cut to a clinician/teacher mock looking confused at a CSV | TRIBE v2 numerical CSV scrolling | "Useless. Unless you have a neuroscience PhD and three months." |
| Cut back to host | Camera pushes in slightly | "We thought Gemma 4 could close that gap." |

**Music:** under-bed, contemplative, low. Bring it up at the cut to Gemma.

---

## The solution · 0:50 – 1:40 · "Cortex demo"

The longest single block. **One clean end-to-end demo with no cuts inside.** Use the `deepmind-gemma-launch` clip from `scripts/demo_clips.yaml` (or whichever one renders best — pick the one with strongest visual + audio variety).

| t | Action on screen | Voiceover |
| --- | --- | --- |
| 0:50 | User drags the clip into the upload form, clicks **Analyze** | "We feed Cortex a 30-second clip from DeepMind's Gemma launch announcement." |
| 0:55 | Status bar transitions: *Connecting → Vision gate → Brain scan → Narrating* | "Gemma 4 E4B reads the keyframes, then we swap models on the 5090: TRIBE v2 in, Gemma out, run inference, swap back." |
| 1:10 | The cortex viewer animates: regions pulse, time scrubber moves | "The visual cortex lights up first — that's V1 and FFA, recognizing the speakers' faces. Then auditory regions, A1 and STG, picking up speech." |
| 1:25 | User clicks the FFA marker; sidebar slides in with name, network, narration | "Click any region. Gemma explains what it does, *and* why it activated for this specific clip." |
| 1:35 | User drags the tier slider from 1 to 5 (clinician); narration text replaces in place | "Drag the slider; the same brain response, three different audiences. Toddler. Clinician. Researcher." |

**Edit note:** the cortex animation is the load-bearing 10 seconds. If frame rate stutters, re-record at higher quality and slow it down 1.5×.

---

## The tech · 1:40 – 2:15 · "How it runs"

| Shot type | On screen | Voiceover |
| --- | --- | --- |
| Architecture diagram (from `docs/gcp.md`) | Boxes labeled "5090", "Ollama", "FastAPI", "GCP A100 fallback" | "Cortex runs entirely on a single RTX 5090. Ollama hosts Gemma 4 in three sizes — E4B, 26B MoE, 31B Dense — picked per narration tier." |
| Cut to terminal showing `pytest tests/ -q` going green | 235 passed in 3.6s | "235 tests, all passing. CI on every push." |
| Cut to the GPU scheduler state-machine diagram | Animated transition GEMMA → SWAPPING → TRIBE → SWAPPING → GEMMA | "TRIBE and Gemma can't coexist in 32 GB, so we built a priority scheduler. When VRAM gets tight, we fall back to a GCP A100 — but the demo you just saw was 100% local." |
| Cut to the Unsloth notebook screenshot | Daniel Han-Chen's verified config in `docs/unsloth.md` | "And we're fine-tuning a Cortex-Gemma-4-E4B for neuroscience interpretation, on a five-thousand-example synthetic dataset we generated and validated against Neurosynth." |

---

## The impact · 2:15 – 2:45 · "Who this is for"

| Shot type | On screen | Voiceover |
| --- | --- | --- |
| Patient on phone screen, getting an explanation | Mobile UI of `cortex.redteamkitchen.com` | "A patient understands their fMRI." |
| Teacher in classroom | Teacher pointing at the Three.js cortex on a smartboard | "A teacher explains how students learn." |
| Filmmaker editing | Editor scrubbing through the BOLD timeline | "A filmmaker crafts more emotionally resonant scenes." |
| Talking head, push-in | Soumit, direct address | "All from a privacy-first, edge-deployed AI. Your video never leaves your machine." |

**Edit note:** the three vignettes are stock-footage stand-ins; mark each with a "demo footage" lower-third so judges aren't confused into thinking the patient/teacher/filmmaker is a real user.

---

## CTA · 2:45 – 3:00 · "Where to find it"

| Shot type | On screen | Voiceover |
| --- | --- | --- |
| Logo card with three URLs | `github.com/AlexiosBluffMara/cortex` · `huggingface.co/RedTeamKitchen/cortex-gemma-4-e4b` · `cortex.redteamkitchen.com` | "Cortex by Alexios Bluff Mara. Built with Gemma 4, TRIBE v2, and Hermes Agent. Apache 2.0. Open source." |
| Final card | "Gemma is a trademark of Google LLC." centered, brand mark below | (no VO; let the trademark line breathe for 2 s) |

---

## Music

- **Bed track:** instrumental, mid-tempo, science-y. No vocals.
  - Suggested cue: [Kevin MacLeod — "Lightless Dawn"](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100763) or any Creative-Commons track listed in the [YouTube Audio Library](https://studio.youtube.com/channel/UC/music) under *Cinematic / Calm*.
- **Mix levels:** −18 dB during VO, −12 dB during the demo block (1:10–1:35) where the brain animates without VO.

---

## Captions

Generate via YouTube auto-captions, then **review and correct** before publishing:

- "Gemma" must be capitalized everywhere (auto-captions tend to lowercase brand names).
- "TRIBE v2" must keep its capitalization.
- "Cortex" must be capitalized.
- BOLD (the fMRI signal) must be all-caps.

---

## Pre-flight checklist

- [ ] Recording machine is on the 5090 with Ollama, FastAPI, and a real demo clip already cached.
- [ ] `pytest tests/ -q` passes immediately before the take.
- [ ] Webapp is at the public Cloudflare-tunneled URL, not localhost (so the URL bar reads `cortex.redteamkitchen.com`).
- [ ] The trademark line is in the lower-third macro and the final card.
- [ ] Browser is in **incognito** with the cortex tab pinned (no extension chrome, no mail icon).
- [ ] Audio: shotgun mic on host, dialogue level −16 LUFS (broadcast standard).
- [ ] One full take, then a backup take, before any partial reshoots.

---

## Render & upload

- **Format:** 1920×1080, 30 fps, MP4 / H.264 / AAC.
- **Title:** `Cortex — Multimodal Brain-Response Analysis with Gemma 4 + TRIBE v2`
- **Description (first 150 chars matter):**
  > Cortex turns any short clip into a 3D cortical activation map plus a Gemma-narrated explanation, running locally on one RTX 5090. Built for the Gemma 4 Good Hackathon.
- **Tags:** `gemma 4`, `gemma 4 good hackathon`, `tribe v2`, `fmri`, `neuroscience`, `unsloth`, `ollama`, `local-first`, `rtx 5090`.

---

*Last updated: 2026-04-25. Plan to re-shoot ~May 11 after the cortex mesh swap and at least two real demo clips have run end-to-end.*
