# Cortex — Sprint Plan v3

**Project:** Cortex by Alexios Bluff Mara LLC  
**Owner:** Soumit Lahiri / Red Team Kitchen / ISU  
**Sprint:** Apr 24 – May 18, 2026  
**Hackathon 1:** Hermes Creative (due May 3)  
**Hackathon 2:** Gemma 4 Good (due May 18)  
**Domain:** redteamkitchen.com  
**Updated:** Apr 24, 2026 v3  

---

## Project Name: Cortex

Jemma is too close to Gemma. The project is now **Cortex** — direct, memorable, neuroscience-native, and distinct from the base model. The fine-tuned model on HuggingFace follows Google's naming guidelines: `RedTeamKitchen/cortex-gemma-4-e4b` (Gemma referenced in the path, not the model name).

Alternative names considered: Neuraxon, Stellara, Cognitiva. Cortex wins because it's one word, immediately understood, and works for both the platform ("Cortex by ABM") and the model name (`cortex-gemma-4-e4b`).

**Gemma naming guidelines (from Google's PDF):**
- Do NOT use "Gemma" as part of your model name
- Reference Gemma in the repo path or model card description
- Format: `company/modelname-gemma-version-size`
- Always include: "Gemma is a trademark of Google LLC."
- No suggestion of Google endorsement

---

## Architecture: Local-First, GCP Backup

### Core Principle
**Everything runs on the 5090 by default.** GCP is the backup, not the primary.

### The VRAM Problem
TRIBE v2 needs ~31GB. Gemma E4B needs ~10GB. Both can't coexist on 32GB. Solution: sequential mode with a GPU priority scheduler.

### GPU Priority Scheduler (extends existing ModelManager)

Your `model_manager.py` already handles Gemma model swapping with VRAM tracking and asyncio locks. We extend this to also manage TRIBE v2 as a "super-tier":

```
Priority (highest to lowest):
  P0: TRIBE v2 inference (when a brain scan is actively running)
  P1: Gemma E4B FAST (always warm when TRIBE isn't running)
  P2: Gemma 26B DEEP (on-demand for analysis tiers 2-4)
  P3: Gemma 31B EXPERT (on-demand for tiers 5-6)
```

**Swap sequence for a brain scan:**
1. Unload all Gemma models from Ollama (`keep_alive: 0s`)
2. Load TRIBE v2 into VRAM (~31GB, fills the card)
3. Run inference (4-7 minutes)
4. Unload TRIBE v2 (`torch.cuda.empty_cache()`)
5. Reload Gemma E4B FAST (3s load time)
6. Resume normal Gemma operations

**During TRIBE inference, Gemma is unavailable.** Any queued Gemma requests wait in an async queue and resume after TRIBE finishes. WhatsApp/Discord users get: "Analyzing your video — brain scan in progress (~5 min)..."

### Fallback Chain (when 5090 GPU is occupied)

```
Gemma inference:
  1. Local Ollama (Gemma E4B on 5090)           ← primary
  2. GitHub Copilot via 0x models               ← fallback when GPU busy with TRIBE
  3. GCP Gemma 31B (Vertex AI or Ollama on A100) ← cloud backup

TRIBE inference:
  1. Local 5090 (sequential swap mode)           ← primary
  2. GCP spot A100 40GB (~$0.36-1.47/hr)        ← backup if local fails or concurrent needed
```

### GCP Spot Instance Strategy

**Instance:** `a2-highgpu-1g` (1x A100 40GB)  
**Pricing:** ~$0.36-1.47/hr spot vs $3.67/hr on-demand (60-91% savings)  
**Region:** `us-central1` (best spot availability)

**Preemption handling:**
- GCP gives 30-second warning before killing spot instance
- Shutdown script saves inference state to Cloud Storage
- Managed Instance Group auto-recreates when resources available
- Pending requests queue in Pub/Sub, resume on new instance

**Cost control:**
- Auto-shutdown after 15 min idle
- Budget alert at $30
- Spot instance only — never on-demand unless manually overridden
- Estimated cost: ~$5-10 total for hackathon (few hours of inference)

---

## Three Integration Points

### 1. WebUI (PRIMARY — hosted on GCP, live demo for judges)
- Full-featured web application at `cortex.redteamkitchen.com`
- Upload video/audio → see brain analysis → interactive 3D viewer
- Collection gallery of past analyses
- Gemma-generated text descriptions of inputs (when originals can't be shown)
- Gemma-generated explanations of TRIBE v2 results
- Three narration tiers displayed alongside 3D brain
- Hosted on GCP Cloud Run (no GPU needed for frontend)
- Backend API talks to 5090 via Cloudflare Tunnel or GCP spot A100

### 2. WhatsApp (via Pixel Fold 9 / Google Fi)
- Send video → get brain analysis + narration back as text + heatmap image
- Send text → get Cortex assistant response
- Primary mobile interface
- Rate limited: 10 msgs/min

### 3. Discord (already built, evidence of prior work)
- Existing Discord bot from mindscope-local
- Already has RBAC, priority queue, rate limiting
- Show in demo as evidence of multi-platform capability
- Don't rebuild — reference existing code

**Documentation note:** Writeup mentions that Telegram integration is also possible via Hermes Agent's native support, but we focused on WhatsApp and WebUI for the demo.

---

## Website Architecture: redteamkitchen.com

### Structure
```
redteamkitchen.com/
  /                        → ABM LLC landing page
  /cortex/                 → Cortex project overview
  /cortex/gemma4good/      → Gemma 4 Good hackathon submission page
  /cortex/hermes/          → Hermes Creative hackathon submission page
  /cortex/viewer/          → Interactive 3D brain viewer (live demo)
  /cortex/gallery/         → Collection of analyzed content
  /cortex/api/             → API docs
```

### Gallery: Collection of Analyses
Each entry in the gallery contains:
1. **Input description** — Gemma-generated text describing the original video/audio (not the copyrighted source itself). E.g., "A 30-second animated sequence depicting two characters in a high-contrast urban environment with rapid scene transitions, intense dialogue, and orchestral music building to a crescendo."
2. **TRIBE v2 results** — Raw brain activation data, top ROIs, peak activation frame
3. **Gemma explanation** — Multi-tier narrations explaining what the brain does in response
4. **3D brain visualization** — Interactive Three.js cortex heatmap for this specific analysis
5. **Metadata** — Processing time, model used, VRAM usage, confidence scores

### Demo Content: Google Official Videos
Use videos from official Google YouTube channels (YouTube license, public):
- Google I/O keynotes
- Google DeepMind research videos
- Gemma announcement videos
- "Year in Search" videos (highly emotional, great for brain response demos)

This is thematically perfect: showing how Google's own content activates the brain, analyzed by Google's own Gemma model.

---

## Interactive 3D Brain Viewer — Design

### Innovation: Layered Cortex with Region Highlighting

Standard brain viewers show a blob with colors. Ours does more:

**Layer 1 — Glass Brain Shell**
Semi-transparent outer cortex mesh. Always visible. Provides anatomical context.

**Layer 2 — Network Activation Overlay**
7 canonical brain networks (default mode, frontoparietal, visual, auditory, somatomotor, dorsal attention, ventral attention) rendered as colored sub-meshes inside the glass brain. Each network's opacity maps to its activation level — brighter = more blood flow.

**Layer 3 — Hotspot Particles**
The top 12 active ROIs emit particle effects (subtle glowing dots that pulse). This draws the eye to the most active regions without cluttering the view.

**Interaction:**
- **Click any region** → sidebar panel slides in showing:
  - Region name (e.g., "Fusiform Face Area")
  - What it does ("Processes face recognition and identity")
  - What activated it ("The video contained close-up character faces")
  - Activation strength (percentile vs. baseline)
  - Which brain network it belongs to
  - The Gemma narration for this region at the user's selected tier
- **Rotate/zoom** with mouse/touch
- **Time scrubber** — drag through the video timeline, brain activations update in real-time
- **Network toggle** — buttons to show/hide each of the 7 networks
- **Tier selector** — switch between layperson/clinician/researcher narrations

**Technical implementation:**
- Three.js with `brain.glb` mesh (already in webapp/public/)
- Schaefer-400 atlas parcellation for ROI mapping (already in analysis.py)
- WebSocket stream for real-time BOLD updates (already in server.py)
- Region metadata from Schaefer-400 atlas lookup tables
- Click detection via Three.js raycasting on mesh vertices

---

## Hermes Agent Fork: Specialized for 3D + Narration

### What we customize
Fork `nousresearch/hermes-agent` → `RedTeamKitchen/cortex-agent`

**Specialization 1: 3D Visual Creator**
- Custom skill: given TRIBE v2 BOLD data, generate Three.js code that renders the cortex heatmap
- Custom skill: update the interactive viewer with new data via WebSocket push
- The agent can modify the visualization in real-time based on user requests ("highlight just the visual cortex", "show me the default mode network")

**Specialization 2: TRIBE v2 Translator**
- Custom skill: take raw BOLD predictions (20,484 vertex array) and translate to human-readable text
- 7-tier narration system (toddler → researcher) — already built in tiers.py
- The agent chains: vision_gate → brain_scan → analyze → narrate → visualize

**Specialization 3: Content Describer**
- Custom skill: given a video the user can't share publicly, generate a rich text description using Gemma's multimodal vision that captures the essential content without reproducing copyrighted material
- This description becomes the "input" shown in the gallery

### Tools registered in Hermes:
```
brain_scan       — orchestrates full TRIBE pipeline (swap models, run inference, swap back)
narrate          — generates tier-specific narration from BOLD data
visualize        — pushes BOLD data to Three.js viewer via WebSocket
describe_input   — Gemma multimodal description of video/audio input
analyze_rois     — extracts top ROIs, network activations, peak frame
gallery_add      — saves analysis result to gallery collection
```

---

## Unsloth Fine-Tuning: Cortex Companion Model

### Goal
Create `cortex-gemma-4-e4b` — a Gemma E4B fine-tuned to be a proper companion to TRIBE v2. Optimized for:
1. Interpreting brain activation data (BOLD, ROIs, networks)
2. Generating Three.js visualization code
3. Multi-tier narration (translating neuroscience to plain english)
4. Agentic tool use (function calling for the pipeline)

### Dataset Mix (~40-50K examples)

| Dataset | Domain | Examples | License | Source |
|---------|--------|----------|---------|--------|
| Synthetic Neuro QA | Brain region interpretation | 5,000 | Self-owned | Generated (see below) |
| NVIDIA Nemotron Tool-Use | Agentic/function calling | 15,000 | CC-BY 4.0 | HuggingFace |
| TokenBender Code Instructions | Python/JS/Three.js coding | 15,000 | Apache 2.0 | HuggingFace |
| OpenHermes 2.5 (sampled) | General instruction following | 10,000 | MIT | HuggingFace |
| Synthetic Three.js | 3D visualization code | 2,000 | Self-owned | Generated |

### Synthetic Neuroscience Dataset Pipeline (5,000 examples)

**Step 1: Define the 50 target brain regions**
Use Schaefer-400 atlas (already in analysis.py). Group into the 7 canonical networks, pick the top 50 regions by functional importance:

```
Visual Network (8 regions):
  V1 (primary visual), V2 (secondary visual), V4 (color processing),
  MT/V5 (motion), FFA (fusiform face area), PPA (parahippocampal place area),
  LOC (lateral occipital complex), EBA (extrastriate body area)

Auditory Network (5 regions):
  A1 (primary auditory), STG (superior temporal gyrus), STS (superior temporal sulcus),
  Planum temporale, Heschl's gyrus

Default Mode Network (8 regions):
  mPFC (medial prefrontal), PCC (posterior cingulate), Angular gyrus,
  Hippocampus, Temporal pole, Precuneus, vmPFC, Retrosplenial cortex

Frontoparietal Control (7 regions):
  dlPFC (dorsolateral prefrontal), IPS (intraparietal sulcus), aIPL (anterior
  inferior parietal), preSMA, FEF (frontal eye fields), AI (anterior insula),
  MFG (middle frontal gyrus)

Somatomotor (6 regions):
  M1 (primary motor), S1 (primary somatosensory), SMA (supplementary motor),
  Premotor cortex, Paracentral lobule, Postcentral gyrus

Dorsal Attention (5 regions):
  FEF, SPL (superior parietal lobule), IPS, MT+, V3A

Ventral Attention (5 regions):
  TPJ (temporoparietal junction), vFC (ventral frontal cortex), MFG,
  AI (anterior insula), IFG (inferior frontal gyrus)

Limbic/Subcortical (6 regions):
  Amygdala, OFC (orbitofrontal), Temporal pole, Insula,
  ACC (anterior cingulate), Hippocampus
```

**Step 2: Generate QA pairs per region (100 per region = 5,000 total)**

For each of the 50 regions, generate 100 QA pairs across these templates:

```
Template A — "What does activation here mean?" (20 per region)
  Q: "The BOLD analysis shows high activation in [region]. What does this indicate?"
  A: [Detailed explanation of what the region does, what stimuli trigger it, what
      cognitive processes it supports, with confidence level]

Template B — "What stimulus caused this?" (20 per region)
  Q: "A subject watching [stimulus description] shows peak activation in [region]
      at time T. Why?"
  A: [Explanation linking stimulus features to region's known function]

Template C — "Compare two regions" (20 per region)
  Q: "How does activation in [region A] relate to simultaneous activation in [region B]?"
  A: [Network-level explanation, functional connectivity, co-activation patterns]

Template D — "Clinical interpretation" (20 per region)
  Q: "A patient shows unusual [hyper/hypo]-activation in [region] during [task].
      What might this suggest?"
  A: [Clinical perspective, potential implications, caveats about fMRI limitations]

Template E — "Plain english" (20 per region)
  Q: "Explain what [region] activation means in simple terms for someone with no
      science background."
  A: [Tier 0-1 narration, analogies, everyday language]
```

**Step 3: Generation method**

Use Claude Code to generate the dataset. Process:
1. For each region, provide Claude with: region name, Brodmann area, network membership, known functions (from neuroscience textbooks/papers)
2. Generate 100 QA pairs following the templates above
3. Format as ShareGPT-style JSON:
```json
{"conversations": [
  {"from": "system", "value": "You are Cortex, a neuroscience AI that explains brain activation data from TRIBE v2 fMRI analysis..."},
  {"from": "user", "value": "The BOLD analysis shows..."},
  {"from": "assistant", "value": "The fusiform face area activation indicates..."}
]}
```
4. Validate: check for factual accuracy, proper region naming, consistent tone per tier
5. Deduplicate: hash-based dedup on (user, assistant) pairs

**Budget:** Free if using Claude Code. If using GCP Gemma 31B for generation, ~$2-5 in compute.

**Step 4: Generate Three.js synthetic dataset (2,000 examples)**

QA pairs for generating Three.js visualization code:
- "Given BOLD data for [N] vertices, write Three.js code to render a cortex heatmap"
- "Update the brain visualization to highlight [network name]"
- "Create a particle effect for the top [N] active regions"
- "Add a click handler that shows region details on raycaster intersection"

Source: scrape Three.js documentation + examples, reformat as instruction pairs.

---

## Video Handling Pipeline — Immaculate & Impeccable

### Gemma's Native Multimodality

Gemma 4 handles video, image, and audio natively. We use this for everything:

**Video processing:**
1. Extract keyframes at 1 FPS using FFmpeg
2. Send each keyframe to Gemma E4B multimodal for vision analysis
3. Gemma classifies content: scene type, objects, emotions, colors, motion
4. Audio track extracted separately → Gemma processes audio embeddings
5. Combined multimodal analysis sent to TRIBE v2

**Audio processing:**
- Extract audio from video with FFmpeg
- Gemma's multimodal capabilities handle audio natively
- Also feed to TRIBE v2's wav2vec-BERT encoder for brain prediction

**Input validation (at system boundary):**
```python
ALLOWED_VIDEO  = {'.mp4', '.mkv', '.webm', '.avi', '.mov'}
ALLOWED_AUDIO  = {'.mp3', '.wav', '.flac', '.ogg', '.m4a'}
ALLOWED_IMAGE  = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
MAX_FILE_MB    = 50
MAX_DURATION_S = 50  # TRIBE v2 hard limit
MIN_DURATION_S = 2
MAX_RESOLUTION = (1920, 1080)  # downscale if larger
```

**Pipeline spec:**
```
1. validate_input(file)
   ├── check file type, size, duration, resolution
   ├── if video > MAX_DURATION_S → trim to last 50s
   ├── if resolution > MAX_RESOLUTION → downscale with FFmpeg
   └── return validated file path or structured error

2. extract_features(file)
   ├── FFmpeg → keyframes at 1 FPS (JPEG, 720p)
   ├── FFmpeg → audio track (WAV, 16kHz mono)
   └── return {keyframes: [...], audio: path, metadata: {...}}

3. vision_gate(keyframes)  [Gemma E4B multimodal]
   ├── classify each frame: scene, objects, emotions, content_type
   ├── generate overall content description (for gallery)
   ├── flag any content concerns
   └── return VisionGateResult with classifications + description

4. brain_scan(features)  [TRIBE v2 on 5090 or GCP]
   ├── swap GPU: unload Gemma, load TRIBE
   ├── run inference → (T, 20484) BOLD predictions
   ├── swap GPU: unload TRIBE, reload Gemma
   └── return InferenceResult

5. analyze(inference_result)  [CPU-only, no GPU needed]
   ├── Schaefer-400 parcellation
   ├── extract top 12 ROIs by mean |z|
   ├── identify peak activation frame
   ├── network-level activation summary
   └── return BrainAnalysis

6. narrate(analysis, tier)  [Gemma E4B/26B/31B via Ollama]
   ├── select model based on tier (0-1: E4B, 2-4: 26B, 5-6: 31B)
   ├── generate narration at requested expertise level
   └── return TieredNarration

7. visualize(analysis)  [Three.js WebSocket push]
   ├── encode BOLD data as vertex buffer
   ├── push to Three.js viewer via WebSocket
   ├── highlight top ROIs with particle effects
   └── return viewer URL

8. gallery_save(all_results)  [Cloud Storage]
   ├── save input description (Gemma-generated, not original file)
   ├── save BOLD predictions, ROIs, narrations
   ├── save Three.js scene state
   └── return gallery entry URL
```

---

## Error Handling Architecture

### Structured Error Response
Every component returns:
```json
{
  "ok": false,
  "error_code": "cuda_oom",
  "error_class": "resource",
  "message": "GPU memory exhausted during TRIBE inference",
  "recovery_action": "Retrying on GCP spot instance",
  "retry": true,
  "fallback_used": "gcp_spot_a100",
  "timestamp": "2026-04-24T15:30:00Z",
  "component": "pipeline.brain_scan",
  "vram_state": {"total_gb": 32.0, "used_gb": 31.8, "free_gb": 0.2}
}
```

### Error Classes and Handlers

**Class: INPUT**
| Error | Trigger | Handler |
|-------|---------|---------|
| `invalid_file_type` | Unsupported format | Return supported formats list |
| `file_too_large` | > 50MB | Suggest compression, offer to trim |
| `duration_too_long` | > 50s | Auto-trim to last 50s, warn user |
| `corrupt_media` | FFmpeg can't decode | Return "file appears corrupted" |
| `no_audio_track` | Video has no audio | Continue with video-only (TRIBE handles missing modalities) |
| `resolution_too_high` | > 1920x1080 | Auto-downscale, log original resolution |

**Class: RESOURCE**
| Error | Trigger | Handler |
|-------|---------|---------|
| `cuda_oom` | torch.cuda.OutOfMemoryError | Free cache → retry → if fail, fall back to GCP |
| `cuda_unavailable` | No GPU detected | Fall back to GCP immediately |
| `disk_full` | Output dir full | Alert user, cleanup old outputs, retry |
| `ollama_down` | Ollama not responding | Restart Ollama service, retry 3x at 5s intervals |
| `model_not_found` | Gemma model not pulled | Auto-pull model, queue request |
| `gcp_preempted` | Spot instance killed | Queue request, wait for new instance |
| `gcp_quota_exceeded` | No GPU quota available | Fall back to local sequential mode |

**Class: MODEL**
| Error | Trigger | Handler |
|-------|---------|---------|
| `hallucinated_tool_call` | Gemma calls nonexistent tool | Validate against schema, reject, re-prompt |
| `bad_narration` | Narration is gibberish/truncated | Retry with lower temperature, simpler prompt |
| `vision_gate_failed` | Gemma can't classify frame | Use text-only TRIBE path (skip vision) |
| `tribe_nan_output` | TRIBE returns NaN BOLD values | Retry with different preprocessing, warn user |
| `tribe_timeout` | Inference > 10 minutes | Kill, return partial results if available |

**Class: NETWORK**
| Error | Trigger | Handler |
|-------|---------|---------|
| `whatsapp_disconnect` | WhatsApp connection lost | Reconnect loop with exponential backoff (1s→30s) |
| `websocket_dropped` | Three.js viewer disconnects | Auto-reconnect, replay last BOLD frame |
| `gcp_network_timeout` | Can't reach GCP instance | Retry 3x, then fall back to local mode |
| `cloudflare_tunnel_down` | Tunnel to 5090 lost | Restart tunnel, serve cached last-known state |

### GPU Priority Scheduler — Detailed Design

Extends existing `ModelManager` with TRIBE awareness:

```python
class GPUScheduler:
    """
    Manages GPU allocation between Gemma (Ollama) and TRIBE v2 (PyTorch).
    
    State machine:
      GEMMA_ACTIVE   — Gemma models loaded, TRIBE unloaded (default state)
      TRIBE_LOADING  — Swapping: Gemma unloading, TRIBE loading
      TRIBE_ACTIVE   — TRIBE running inference, Gemma unavailable
      TRIBE_UNLOADING — Swapping back: TRIBE unloading, Gemma reloading
    
    During TRIBE_ACTIVE:
      - All Gemma requests queue in async queue
      - WhatsApp/Discord get "brain scan in progress" message
      - Fallback to GitHub Copilot / 0x models for urgent Gemma requests
    """
    
    STATES = ['GEMMA_ACTIVE', 'TRIBE_LOADING', 'TRIBE_ACTIVE', 'TRIBE_UNLOADING']
    
    # Fallback for Gemma when GPU is busy
    GEMMA_FALLBACK = "github_copilot_0x"  # or GCP Gemma 31B via Vertex
```

**Queue management:**
- Gemma requests during TRIBE inference → queued with priority (WhatsApp user messages > background tasks)
- Max queue depth: 50 requests
- Queue timeout: 10 minutes (if TRIBE takes too long, start rejecting)
- Emergency interrupt: if a P0 request comes in during TRIBE, TRIBE can be killed mid-inference (data loss acceptable, retry later)

---

## HuggingFace ml-intern Integration

**What it is:** HuggingFace's new open-source AI agent that automates LLM post-training workflows. Browses arXiv, finds datasets, executes training scripts, runs evaluations.

**How we use it:**
1. Let ml-intern browse arXiv for latest TRIBE/brain-encoding papers
2. Let ml-intern find and format relevant datasets on HuggingFace Hub
3. Use ml-intern to automate the Unsloth fine-tuning loop (train → eval → adjust → retrain)
4. HF offers $1,000 GPU credits + Anthropic credits for early users — apply for this

**Setup:** `pip install ml-intern` → configure with HF token + GCP credentials → point at training script.

---

## Claude ↔ Discord Connection

To give me (Claude) read access to your Discord:

**Option 1: Discord MCP Server (recommended)**
Install `discordmcp` (MIT licensed):
```bash
npm install -g discordmcp
```
Configure with a Discord bot token in your server. Then connect it as an MCP server in Claude Code or Cowork. I'll be able to read messages, channels, threads, and server history.

**Option 2: Composio Discord Toolkit**
Pre-built integration that wraps Discord API for Claude agents. Less setup, more managed.

You'll need to create a Discord bot in your Nous Research server (or your own), grab the token, and configure the MCP.

---

## Gemma 4 Only — No Gemma 3

All references to Gemma 3 are eliminated. Model tiers:
- **FAST:** `gemma4:e4b` (~10GB, 197 tok/s)
- **DEEP:** `gemma4:26b` (~19GB, 132 tok/s, MoE)
- **EXPERT:** `gemma4:31b` (~21GB, 51 tok/s, dense)
- **FINE-TUNED:** `cortex-gemma-4-e4b` (our Unsloth model, same VRAM as base E4B)

If Gemma 4 E4B isn't available on Ollama, we use Gemma 4 E2B. There is no Gemma 3 fallback.

---

## Website: redteamkitchen.com

### Hosting
- **Domain:** redteamkitchen.com (already owned, Cloudflare DNS)
- **Frontend:** Static site on Cloudflare Pages (free, fast CDN)
- **Brain viewer API:** Cloudflare Tunnel → 5090 local server
- **Gallery storage:** GCP Cloud Storage (for BOLD data, thumbnails)
- **Backup API:** GCP Cloud Run (frontend only, no GPU)

### Hackathon Sections
```
/cortex/gemma4good/
  - Kaggle writeup (embedded or linked)
  - YouTube video embed
  - Live demo link (→ /cortex/viewer/)
  - HuggingFace model link
  - GitHub repo link
  - Architecture diagram
  - Track: Unsloth (or Ollama)

/cortex/hermes/
  - Demo video embed (from tweet)
  - Hermes Agent capabilities
  - 3D brain viz showcase
  - Link to fork repo
  - Same content, repackaged for creative angle
```

Essentially the same project, two lenses: Gemma 4 Good emphasizes social impact + technical depth, Hermes Creative emphasizes the 3D visualization + agent creativity.

---

## Target Tracks (Updated Priority)

1. **Ollama Track ($10K)** — PRIMARY unless Unsloth model is great
2. **Unsloth Track ($10K)** — PRIMARY if fine-tuned model outperforms base
3. **Health & Sciences Impact ($10K)** — auto-eligible (neuroscience)
4. **Main Track (up to $50K)** — eligible if standout
5. **Hermes Creative Main ($10K)** — 3D brain viz = creative domain

**Track selection strategy:** We build for both Ollama and Unsloth. When we evaluate the fine-tuned model around May 5-6, we compare it to base. If fine-tuned is clearly better → select Unsloth as primary track. If not → select Ollama. One submission, evaluated across all tracks regardless.

---

## Day-by-Day Calendar v3

| Day | Date | Focus |
|-----|------|-------|
| 1 | Apr 24 (Thu) | Fork Hermes Agent, pull Gemma 4 on Ollama, verify agent boots |
| 2 | Apr 25 (Fri) | Build GPU scheduler (extend ModelManager for TRIBE swapping) |
| 3 | Apr 26 (Sat) | Register Hermes tools (brain_scan, narrate, visualize, describe_input) |
| 4 | Apr 27 (Sun) | End-to-end pipeline: video → TRIBE → analysis → narration → viz |
| 5 | Apr 28 (Mon) | WhatsApp integration + Three.js viewer improvements (click-to-inspect) |
| 6 | Apr 29 (Tue) | WebUI: gallery page, analysis collection, Cloudflare Tunnel |
| 7 | Apr 30 (Wed) | Test with Google official YouTube videos, fix edge cases |
| 8 | May 1 (Thu) | Generate synthetic neuro dataset (5K QA pairs via Claude Code) |
| 9 | May 2 (Fri) | Record Hermes demo video + start Unsloth fine-tuning |
| 10 | May 3 (Sat) | **HERMES DEADLINE** — tweet + Discord. Training continues. |
| 11 | May 4 (Sun) | Evaluate fine-tuned model, export GGUF, load in Ollama |
| 12 | May 5 (Mon) | Benchmark cortex-gemma-4-e4b vs base, decide track |
| 13 | May 6 (Tue) | Upload model to HuggingFace, swap Hermes to fine-tuned model |
| 14 | May 7 (Wed) | Build out redteamkitchen.com/cortex/ sections |
| 15 | May 8 (Thu) | Set up GCP spot instance as backup, test failover |
| 16 | May 9 (Fri) | Write Kaggle writeup (draft 1) |
| 17 | May 10 (Sat) | Record YouTube video (draft 1) |
| 18 | May 11 (Sun) | Edit video, add captions |
| 19 | May 12 (Mon) | Polish WebUI, test interactive brain viewer as live demo |
| 20 | May 13 (Tue) | Final writeup edit |
| 21 | May 14 (Wed) | Re-record video if needed |
| 22 | May 15 (Thu) | Verify all links: YouTube, GitHub, HuggingFace, live demo, website |
| 23 | May 16 (Fri) | Buffer day |
| 24 | May 17 (Sat) | Submit on Kaggle |
| 25 | May 18 (Sun) | **GEMMA DEADLINE** (6:59 PM CDT) — final verification |

---

## Fallback Chain (Complete)

```
GEMMA INFERENCE:
  1. Local Ollama on 5090 (gemma4:e4b or cortex-gemma-4-e4b)
  2. GitHub Copilot / 0x models (when GPU busy with TRIBE)
  3. GCP Gemma 31B via Vertex AI or Ollama on spot A100
  4. GCP Gemma 26B MoE (cheaper, still good)

TRIBE V2 INFERENCE:
  1. Local 5090 sequential swap mode (unload Gemma → load TRIBE → swap back)
  2. GCP spot A100 40GB (~$0.36-1.47/hr)
  3. GCP on-demand A100 40GB ($3.67/hr) — emergency only

MESSAGING:
  1. WhatsApp (primary)
  2. Discord (already built, show as evidence)
  3. Telegram (document as possible via Hermes, don't build)

LIVE DEMO:
  1. Cloudflare Tunnel → 5090 local Three.js viewer
  2. Static deploy on Cloudflare Pages with pre-baked BOLD data
  3. GCP Cloud Run serving viewer with cached data

FINE-TUNING:
  1. Unsloth on local 5090 (Gemma 4 E4B)
  2. Unsloth on local 5090 (Gemma 4 E2B, if E4B unavailable)
  3. Document training attempt, submit with base model
```

---

## YouTube Video Structure (3 minutes)

```
0:00-0:20  HOOK
  "What if you could watch a video and see exactly which parts
   of your brain light up in real-time?"
  [Show: 3D brain with regions pulsing]

0:20-0:50  THE PROBLEM
  "Neuroscience is locked behind expensive lab equipment and
   impenetrable jargon. TRIBE v2 can predict brain responses,
   but its output is 20,000 numbers. Useless without translation."

0:50-1:40  THE SOLUTION: CORTEX
  "Cortex combines Google's Gemma 4 with Meta's TRIBE v2
   brain foundation model. Send any video — Gemma's vision
   understands what you're watching. TRIBE predicts your
   cortical response. Gemma translates it to plain english."
  [Show: full pipeline demo — upload Google I/O clip, watch brain
   light up, read narration at three levels]

1:40-2:15  THE TECH
  "Running locally on an RTX 5090 via Ollama. Fine-tuned with
   Unsloth for neuroscience interpretation. Hermes Agent
   orchestrates everything. WhatsApp for mobile. Interactive
   3D brain viewer for deep exploration."
  [Show: clicking brain regions, reading explanations]

2:15-2:45  THE IMPACT
  "Cortex makes brain science accessible to everyone. A patient
   understands their fMRI. A teacher explains how students learn.
   A filmmaker crafts more emotionally resonant scenes. All from
   an edge-deployed, privacy-first AI."

2:45-3:00  CALL TO ACTION
  "Cortex by Alexios Bluff Mara. Built with Gemma 4, TRIBE v2,
   Hermes Agent, and Unsloth. Open source on GitHub."
  [Show: logo, links]
```

---

*v3 — Apr 24, 2026. Renamed to Cortex. Local-first with GCP backup. GPU scheduler. No Gemma 3. No Raspberry Pi. WebUI + WhatsApp + Discord. redteamkitchen.com. Synthetic neuro dataset plan. Full error handling architecture.*
