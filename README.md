# Cortex — Watch Your Brain Respond to Any Video

**`ALEXIOS BLUFF MARA × ILLINOIS STATE UNIVERSITY`**
*Research conducted in association with [Illinois State University](https://illinoisstate.edu), Bloomington–Normal, IL.*

---

> Upload a short video. In about six minutes, 20,484 seats in your personal Brain Cinema light up in real-time 3D — and an AI film critic explains what just happened, to anyone from a curious 8-year-old to a working neurologist.

Built for the [Gemma 4 Good Hackathon](https://www.kaggle.com/competitions/gemma-4-good-hackathon) (Health & Sciences track) and the [Nous Research × Kimi Creative Hackathon](https://nousresearch.com) (Creative track) by **Alexios Bluff Mara LLC (dba Red Team Kitchen)**.

*Gemma is a trademark of Google LLC.*

---

## Quick install (release)

If you just want to run Cortex, grab the latest release from <https://github.com/AlexiosBluffMara/cortex/releases> — one bootstrap script, one image pull, ~6 min to first scan.

```bash
# Linux / macOS / Git Bash / WSL
curl -fsSL https://github.com/AlexiosBluffMara/cortex/releases/latest/download/bootstrap-cortex.sh -o bootstrap-cortex.sh
chmod +x bootstrap-cortex.sh
export HF_TOKEN=hf_xxx   # required to fetch TRIBE v2 (Meta, CC-BY-NC 4.0)
./bootstrap-cortex.sh
```

```powershell
# Windows PowerShell
Invoke-WebRequest -Uri https://github.com/AlexiosBluffMara/cortex/releases/latest/download/bootstrap-cortex.ps1 -OutFile bootstrap-cortex.ps1
$env:HF_TOKEN = 'hf_xxx'
.\bootstrap-cortex.ps1
```

The script auto-detects your GPU and picks the right execution mode (swap on a 32 GB RTX 5090, both-resident on ≥48 GB cards). Air-gapped operators: see `CORTEX_OFFLINE=1` in the release notes.

If you want to **develop on Cortex** instead, jump to the [Quickstart](#quickstart) section below.

---

## The Brain Cinema — how Cortex works

Picture a movie theater. Your brain is the audience. Every seat in that theater corresponds to one tiny patch of your brain's outer surface — the cortex. Cortex (the tool) is the camera system and the film critic rolled into one.

**The movie being screened** is whatever you upload: a short video clip, an audio recording, an image, or a block of text.

**The audience** is your brain — 20,484 people sitting in 20,484 assigned seats, each responsible for a specific job: seeing faces, recognizing voices, feeling suspense, processing language. When something interesting happens on screen, certain sections of the audience lean forward. Blood rushes to those seats. That rush of blood is called the BOLD signal — the audience's live reaction meter.

**TRIBE v2** is the high-speed sensor system built into every seat. Trained by Meta on 25 human subjects, it watches the movie playing at the front of the theater and predicts, twice per second, how excited each of those 20,484 audience members is going to get — 3.5 to 5 seconds before their reaction visibly peaks. (The delay exists because biology is slow: when neurons fire, the blood that feeds them takes a few seconds to arrive. This delay is called the hemodynamic response, and Cortex has already corrected for it.)

**Gemma 4** is the film critic sitting in the back booth with a notebook. After the screening, Gemma reads the audience-reaction printout and explains what happened — at three levels of detail:

- **General**: written for a curious high-schooler. Plain language, real-world analogies, no jargon.
- **College**: written for someone who has taken a neuroscience or biology course. Named brain networks, functional anatomy.
- **Clinical**: written for a clinician or researcher. Yeo-7 network labels, laterality, peak activation timing, BOLD z-scores.

**The 3D brain viewer** is a live seating chart of that theater — colored from cold blue to hot red — showing which sections of the audience lit up, when. You can rotate it, zoom in, scrub through time, and click any section to read exactly what that part of the brain does and why it responded the way it did.

**Running locally on an RTX 5090** means you own the IMAX theater outright. No data leaves your machine, no cloud subscription required, and the processing costs you roughly nothing per scan after the hardware purchase. **Running on the cloud** means the same theater, accessible from any browser, anywhere — at roughly $0.70 per hour for a comparable GPU on Google Cloud Run, or effectively $0 when the server scales to zero between uses.

---

## Why it matters medically

A clinical fMRI session — a real brain scan in a real hospital — costs between $3,000 and $6,000, takes 90 minutes, and requires a radiologist to interpret. That price tag puts brain science out of reach for most of the world.

Cortex does not replace a clinical scan. It is not a diagnostic tool. Its predictions are averaged across 25 subjects, not tuned to your individual brain. But it opens research doors that are currently locked:

- **Educational neuroscience**: which brain networks does a given lecture, training video, or online course actually engage? Educators can now iterate on content the way product designers iterate on interfaces — with signal, not guesswork.
- **Rehabilitation research**: does a therapy video reach the target motor or language regions? Quantify it without booking a scanner.
- **Media and accessibility**: which cognitive systems does a piece of content activate? Designers, filmmakers, and accessibility engineers can answer this question for the first time without a neuroscience lab.

**Hardware: RTX 5090 (32 GB) is the baseline, not the ceiling.** The 5090 is what you need at minimum to run the full TRIBE-and-Gemma swap pipeline locally. From there, every modern NVIDIA accelerator — RTX 6000 Ada, L40S, A100 (40 GB or 80 GB), H100 (80 GB) — runs Cortex faster, with both models resident simultaneously, no swap. We're targeting deployment on national HPC resources via [ACCESS-CI](https://access-ci.org) at UIUC and partner institutions, where a single A100 makes the per-scan budget effectively zero.

The direction is unmistakable: brain-response analysis at the cost of electricity, on hardware academic researchers already have.

---

## What Cortex is NOT

- **Not a diagnostic tool.** Nothing Cortex produces should be used to diagnose, treat, or screen for any medical condition.
- **Not a personal brain scan.** TRIBE v2 is trained on 25 subjects and outputs a population-averaged prediction. It predicts how a statistically average human brain responds to your video — not how *your* specific brain responds.
- **Not a replacement for fMRI.** Clinical fMRI measures actual blood flow in your brain. Cortex *predicts* what blood flow would look like, based on a model trained on a small group of people watching TV shows and movies.
- **Not subcortical.** The predictions cover the cortical surface only — the outer rind of the brain. Deep structures like the amygdala, hippocampus, and thalamus are not modeled.
- **Not trained on medical data.** TRIBE v2 was trained on subjects watching the TV series *Friends* and feature films, not on patients or clinical stimuli.

---

## Model limits

| Parameter | Value |
|---|---|
| TRIBE v2 sampling rate | 2 Hz (one prediction every 0.5 seconds) |
| Hemodynamic lag | 5 seconds — pre-applied |
| What "t=7" means | 7 × 0.5 s = 3.5 s into the movie. Like applause arriving slightly after the plot twist. |
| Cortical vertices predicted | 20,484 (fsaverage5 surface, both hemispheres) |
| Subjects in training data | 25 (Courtois NeuroMod dataset) |
| Maximum input duration | 50 seconds (100 time-points at 2 Hz — hard limit) |
| Input types | Video, audio, text, or any combination |
| Subcortical coverage | None |
| Diagnostic use | Not permitted |

---

## The three narration tiers

Think of them as three different film critics writing about the same screening:

**General** — The film critic who writes for a general newspaper. "The audience in the back-left section lit up — those are the people responsible for recognizing faces. A close-up of a person just appeared on screen, and about 3.5 seconds later, that section of the audience leaned forward in their seats."

**College** — The critic writing for a film studies journal. "Primary visual and fusiform face regions showed strong bilateral activation within the visual network (peak z = 4.2 at t = 7), consistent with face-selective response in the ventral visual stream."

**Clinical** — The critic writing the formal screening report. "Yeo-7 Visual network activation (bilateral occipital, fusiform): peak BOLD at t = 7 (3.5 s post-stimulus onset, 5 s HRF lag pre-applied). Left-lateralized superior temporal gyrus response suggests auditory-linguistic processing. Default mode network suppression consistent with externally-directed attention."

---

## Tech stack

| Component | What it is | Key numbers |
|---|---|---|
| TRIBE v2 | Meta's brain foundation model | 25 subjects, 20,484 vertices, 2 Hz, 5 s HRF lag |
| Gemma 4 E4B | Google's local language model (fast tier) | 194 tok/s on RTX 5090, multimodal |
| Gemma 4 26B | Google's MoE model (standard tier) | 132 tok/s, mixture-of-experts |
| Gemma 4 31B | Google's dense model (expert tier) | 51 tok/s, highest quality |
| Mercury (Hermes fork) | AI agent orchestrating everything | Snowy The Bot, live on Discord |
| Three.js viewer | Interactive 3D brain in the browser | Per-vertex BOLD animation, time scrubber, click-to-inspect |
| Hardware (local) | RTX 5090, 64 GB RAM, Windows 11 | MSRP ~$1,999, street ~$2,500–3,000 |
| Cloud backup | Google Cloud Run, L4 GPU | ~$0.70/hr; ~$0 at scale-to-zero |

**VRAM note**: TRIBE v2 uses ~22.4 GB and Gemma 4 E4B uses ~10 GB. They cannot coexist on 32 GB. A GPU scheduler swaps them sequentially — one loads while the other unloads. This takes about 10 seconds.

**The Kimi K2.6 contribution**: the initial Three.js brain viewer was written by Kimi K2.6 (via the Nous Portal), dispatched by Mercury. 14 commits, 75 minutes, $22.04 — covering 47 KB of Three.js, the 50-region atlas overlay, and the brain mesh pipeline.

---

## Architecture

```
Upload
  └─ media_gate          Gemma 4 multimodal — describes what it sees
       └─ TRIBE v2        Predicts (T × 20,484) BOLD z-scores at 2 Hz
            └─ BrainAnalysis   Schaefer-400 parcellation, Yeo-7 networks, top ROIs
                 └─ Gemma narrate × 3 tiers   General · College · Clinical
                      └─ WebSocket → Three.js viewer   Per-vertex 3D animation
```

GPU scheduler state machine: `IDLE → GEMMA_ACTIVE → TRIBE_ACTIVE` — eviction-driven swap with OOM recovery and GCP A100 fallback.

Mercury (the Hermes fork) orchestrates the pipeline end-to-end across six client surfaces: terminal, Discord, web, WhatsApp, email, and mobile.

---

## Quickstart

```bash
git clone https://github.com/AlexiosBluffMara/cortex.git
cd cortex

# Create venv on C: for speed (keep project files on D:)
uv venv C:/Users/soumi/cortex/.venv
source C:/Users/soumi/cortex/.venv/Scripts/activate

uv pip install -e ".[dev]"

# Pull the fast narration model (others are optional — pulled on demand)
ollama pull gemma4:e4b

# Start the server
uvicorn webapp.server:app --host 0.0.0.0 --port 8765 --reload
# Open http://localhost:8765
```

TRIBE v2 weights must be installed separately (CC-BY-NC 4.0 — see NOTICE). They are not included in this repository.

---

## Hackathon context

Cortex is a dual submission:

- **Gemma 4 Good (Kaggle)** — Health & Sciences track. Deadline May 18, 2026. Demonstrates Gemma 4 running locally via Ollama + Unsloth fine-tuning for neuroscience interpretation.
- **Nous Research × Kimi Creative Hackathon** — Creative track. Demonstrates Mercury (Hermes fork) dispatching Kimi K2.6 to build the 3D viewer, with Snowy The Bot live on Discord.

---

## Links

- Live demo: [https://cortex.redteamkitchen.com](https://cortex.redteamkitchen.com)
- GitHub: [https://github.com/AlexiosBluffMara/cortex](https://github.com/AlexiosBluffMara/cortex)
- Mercury (Hermes fork): [https://github.com/AlexiosBluffMara/mercury](https://github.com/AlexiosBluffMara/mercury)

---

## Run your own copy

Run `bash scripts/replicate.sh` to spin up your own GCP project with the full Cortex infrastructure stack — APIs, Workload Identity Federation for GitHub Actions, Artifact Registry, Secret Manager, GCS bucket, Firestore, Firebase Hosting, and the GitHub Secrets the deploy workflow reads. See [`docs/REPLICATE.md`](docs/REPLICATE.md) for prerequisites, the manual steps the script can't automate (Firebase ToS, Cloudflare Tunnel cert), and a teardown script.

Tested on macOS, Linux, and Windows (Git Bash). A PowerShell variant `scripts/replicate.ps1` is provided for Windows users without WSL. Free tier covers most workloads — Cloud Run scales to zero between requests.

---

## License

Code: MIT. TRIBE v2 model weights: CC-BY-NC 4.0 (Meta — non-commercial use only). Gemma 4: Gemma Terms of Use (Google). *Gemma is a trademark of Google LLC.*
