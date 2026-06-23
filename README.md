# Cortex — Watch Your Brain Respond to Any Video

**`ALEXIOS BLUFF MARA × ILLINOIS STATE UNIVERSITY`**
*Research conducted in association with [Illinois State University](https://illinoisstate.edu), Bloomington–Normal, IL.*

---

> Upload a short video. In about three minutes, 20,484 seats in your personal Brain Cinema light up in real-time 3D — and **four AI film critics** explain what just happened, in their own voices: a curious ISU freshman, a Northwestern neurologist, a WBEZ science reporter, and a Google ML scientist. Pick the voice that sounds like your brain.

## Current status — June 22, 2026

- **Local app:** `http://127.0.0.1:8765` on Seratonin, the RTX 5090 desktop.
- **Local gallery:** `http://127.0.0.1:8765/gallery.html` when the FastAPI app is running.
- **Public project page:** [redteamkitchen.com/cortex](https://redteamkitchen.com/cortex)
- **Optional live route:** [cortex.redteamkitchen.com](https://cortex.redteamkitchen.com) only works when Seratonin, the watchdog, and the Cloudflare Tunnel are online.
- **Live ops runbook:** [`docs/CORTEX_LIVE_OPS.md`](docs/CORTEX_LIVE_OPS.md)

The old Tailscale Funnel URLs (`*.scylla-betta.ts.net`) are retired and should not be used in public copy. Cortex can still be shown live from the local PC, but the resilient public version should publish selected gallery videos, thumbnails, scan metadata, and recordings from Cloudflare Pages/R2. Treat the upload app as lab mode, not as an always-on public promise.

---

Currently framed as the **Nous Research × Kimi Hackathon** Cortex artifact by **Alexios Bluff Mara LLC (dba Red Team Kitchen)**. The project also informed later MRI/fMRI research planning: the useful next step is not claiming a clinical brain scan, but using media-to-brain-response prototypes to ask better research questions before real MRI work.

Cortex shares its RTX 5090 with [Mercury](https://github.com/AlexiosBluffMara/mercury) (the agent gateway archive). The contract that lets them coexist is documented at [`mercury/docs/MERCURY_CORTEX_CONTRACT.md`](https://github.com/AlexiosBluffMara/mercury/blob/main/docs/MERCURY_CORTEX_CONTRACT.md) — Cortex owns the GPU swap state machine and exposes `GET /api/utilization` from the FastAPI app; Mercury can poll it before any Gemma load.

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

## The four personas

Every Cortex scan generates **four parallel narrations** from one TRIBE prediction. Same data, four different readers. The live persona page is available at `/personas.html` when the local app is running; public copy should prefer durable screenshots or recordings until the gallery is static-exported.

**Sam** — ISU freshman from Normal, IL. *"ok so basically your eyes are doing all the work here. it's like when you're scrolling through your fyp and everything just hits."*

**Chris** — Science reporter for WBEZ Chicago. *"The striking thing here is how the brain's visual processing highway takes center stage, reaching its peak activity at the 5.5-second mark. Think of it like a spotlight swinging across a dark stage."*

**Dr. Park** — Associate Professor of Neurology, Northwestern Feinberg. *"The present data demonstrate a BOLD response with rising phase 0.5s, peak amplitude z=0.10, in the right somatomotor network — consistent with M1/S1 recruitment."*

**Priya** — Senior ML Research Scientist, Google DeepMind, Chicago. *"Stimulus processed via TRIBE v2 (V-JEPA2/wav2vec-BERT 2.0/Llama-3.2-3B) on local RTX 5090, ~6 GB VRAM; ~$0.006/scan local vs ~$0.30/scan on a GCP L4."*

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

## Architecture (local, current)

```
   Browser / phone
     ↓ http://127.0.0.1:8765  (local)
     ↓ https://cortex.redteamkitchen.com  (optional Cloudflare Tunnel)
   FastAPI backend  (port 8765)
     ├─ TRIBE v2     PyTorch on RTX 5090, ~6 GB VRAM
     │                → 20,484-vertex BOLD prediction at 2 Hz
     │
     └─ 4× narrate   student · patient · clinician · ml_scientist
         ↓
   Ollama / local narration models
     └─→ localhost:11434
```

The current working path is local-first and PC-bound. Seratonin runs the FastAPI app, the scan registry, the generated ASCII brain videos, and the RTX 5090 TRIBE path. Cloudflare Tunnel can expose that app to the internet, but it is an availability convenience, not a guarantee.

GPU scheduler state machine: `IDLE → GEMMA_ACTIVE → TRIBE_ACTIVE` — eviction-driven swap with OOM recovery. TRIBE checkpoint is 676 MB on disk, ~5–6 GB VRAM during inference.

The website should not depend on this process being live. Durable Cortex publishing should export scan metadata plus selected videos/thumbnails to Cloudflare Pages/R2, then link to the live app only when the desktop is available.

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

Cortex is now archived publicly under the **Nous Research × Kimi Hackathon** banner:

- **Nous Research × Kimi Creative Hackathon** — Creative track. Demonstrates Mercury/Hermes dispatching Kimi K2.6 to build the early 3D viewer and supporting artifacts.
- **Follow-on research direction** — Cortex inspired more careful MRI/fMRI work planning, but it is not itself a clinical imaging tool.

---

## Links

- Project page: [https://redteamkitchen.com/cortex](https://redteamkitchen.com/cortex)
- Optional live route, PC required: [https://cortex.redteamkitchen.com](https://cortex.redteamkitchen.com)
- GitHub: [https://github.com/AlexiosBluffMara/cortex](https://github.com/AlexiosBluffMara/cortex)
- Mercury archive: [https://github.com/AlexiosBluffMara/mercury](https://github.com/AlexiosBluffMara/mercury)

---

## Run your own copy

Run `bash scripts/replicate.sh` to spin up your own GCP project with the full Cortex infrastructure stack — APIs, Workload Identity Federation for GitHub Actions, Artifact Registry, Secret Manager, GCS bucket, Firestore, Firebase Hosting, and the GitHub Secrets the deploy workflow reads. See [`docs/REPLICATE.md`](docs/REPLICATE.md) for prerequisites, the manual steps the script can't automate (Firebase ToS, Cloudflare Tunnel cert), and a teardown script.

Tested on macOS, Linux, and Windows (Git Bash). A PowerShell variant `scripts/replicate.ps1` is provided for Windows users without WSL. Free tier covers most workloads — Cloud Run scales to zero between requests.

---

## License

Code: MIT. TRIBE v2 model weights: CC-BY-NC 4.0 (Meta — non-commercial use only). Gemma 4: Gemma Terms of Use (Google). *Gemma is a trademark of Google LLC.*
