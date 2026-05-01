# Cortex demo recording guide

**`ALEXIOS BLUFF MARA × ILLINOIS STATE UNIVERSITY`**
*Research conducted in association with Illinois State University, Bloomington–Normal IL.*

This walks you through recording a clean ~3-minute submission video for the
**Gemma 4 Good Hackathon** (Health & Sciences track) and the **Nous Research
× Kimi Creative Hackathon** (Creative track). Everything in this script runs
on the local RTX 5090 — no cloud dependency in the hot path.

---

## 0 · Pre-flight checklist (before you hit Record)

These three services must be alive. If you ran the auto-installer
(`scripts/windows-services/install-services.ps1` in the **mercury** repo)
they're all auto-restarting Windows services and you can skip this section.

If you didn't, open **two PowerShell windows** and start each one:

```powershell
# Window 1 — Cloudflared tunnel (already running per your earlier session)
cloudflared.exe tunnel --config "C:/Users/soumi/.cloudflared/config.yml" run rtk-5090
```

```powershell
# Window 2 — Cortex webapp on port 8765
cd D:\cortex
& "C:/Users/soumi/cortex/.venv/Scripts/python.exe" -m uvicorn webapp.server:app --host 0.0.0.0 --port 8765
```

Verify both are up before recording:

```bash
# Local webapp
curl http://localhost:8765/api/health
# Should return JSON with "ok": true and a live VRAM report

# Public path (relay → tunnel → 5090)
curl https://cortex.redteamkitchen.com/api/info | grep '"5090_online"'
# Should return "5090_online":true
```

If `5090_online` is **false** — restart cloudflared. If it's still false —
check the cloudflared service log at `~/.cloudflared/tunnel.log`.

---

## 1 · OBS scene layout

**Recommended canvas: 1920×1080**.

| Layer | Source | Position |
|---|---|---|
| 1 (top) | **Browser source**: `https://cortex.redteamkitchen.com/specs.html?bg=solid` | full canvas |
| 2 | **Browser source**: `https://cortex.redteamkitchen.com` | full canvas |
| 3 | **Window capture**: your terminal showing the cloudflared + uvicorn logs | bottom-right thumbnail |
| 4 | **Webcam** (optional) | bottom-left thumbnail |

The hardware specs overlay (`/specs.html`) sits on a separate scene that you
cut to mid-demo to show the GPU lighting up. Use scene transitions for the
cuts — don't toggle visibility or it strobes the recording.

OBS browser-source URLs to bookmark:

- **Hardware overlay** (full): `https://cortex.redteamkitchen.com/specs.html?bg=solid`
- **Hardware overlay** (compact, for picture-in-picture): `https://cortex.redteamkitchen.com/specs.html?compact=1&frame=0`
- **Public gallery**: `https://cortex.redteamkitchen.com`
- **Direct uploader**: `https://cortex.redteamkitchen.com/scan` (skips the gallery list)

---

## 2 · The recording script (~3 minutes total)

### Cold open (0:00 – 0:15) — *Hardware*

Cut to the **`/specs.html`** overlay. Voiceover (or text card):

> Alexios Bluff Mara × Illinois State University. Research collaboration.
> The 5090 powerstation, running locally in Chicago.

Let viewers see the live spec table for ~5 seconds. The status pill should
read `5090 LIVE` in cardinal red. The VRAM free counter should be ~26 GB.

### The pitch (0:15 – 0:35) — *What this is*

Cut to **`https://cortex.redteamkitchen.com`** (the gallery). Voiceover:

> Cortex predicts how 20,484 patches of your brain's outer surface respond
> to any short video — in about six minutes — and an AI film critic explains
> the response at three reading levels. Trained by Meta, narrated by Gemma 4,
> running on a single RTX 5090.

While you talk, hover over the gallery items (each is a previously-submitted
scan) so the viewer sees this is a working public service.

### The upload (0:35 – 1:00) — *Show the live submission*

1. Click **Submit a scan** (or drag a file onto the drop zone).
2. Drop the NASA Artemis B-roll: `D:\mercury\demo\nasa_source\KSC-01172026-Artemis_II_Rollout_B_Roll_Package.mp4`
   — this is **public-domain** NASA footage; safe for a public submission.
   Trim to a 15-second clip first if it's longer (the hard cap is 50 s).
3. Click **Analyze**.

The live event log on the left should start filling: `accepted: <id>`,
`queued`, `running`, `tribe v2 inference`, ...

### The transformation (1:00 – 1:50) — *Hardware lights up*

Cut to **`/specs.html`** overlay so viewers see the 5090 work:

- Scheduler state: `idle` → `tribe_active` (~15 s in)
- VRAM used: ~6 GB → ~28 GB
- Temperature: ~50 °C → ~75 °C (live)
- Power: ~60 W → ~450 W (live)
- Queue: `0/8` → `1/8` → `0/8` as the job clears

Voiceover:

> TRIBE v2 — Meta's brain foundation model — is now predicting cortical
> response from the video frames. Twenty-two gigs of weights live on the
> GPU. Two hertz sample rate. Twenty thousand vertices.

Hold on the overlay until the scheduler returns to `idle` (TRIBE just
finished; Gemma is about to load).

### The reveal (1:50 – 2:30) — *3D brain + narration*

Cut back to the Cortex page — the scan should now have completed. Click
into the result.

**The 3D brain viewer** opens. Rotate it. Show:
- Cardinal-red activation in the V1/V4 visual regions (Artemis footage =
  visual-heavy stimulus)
- ISU-blue suppression in the default mode network
- The time scrubber sweeping through the response

Switch to the **narration tabs** (Sam · Priya · Dr. Park · Chris). Show
the **General** tier first (one paragraph), then click **Clinical** for
the same scan. Voiceover:

> Same prediction. Three audiences. Sam writes for an eight-year-old;
> Dr. Park writes the formal screening note. Gemma 4, running locally,
> 194 tokens per second.

### The hardware story (2:30 – 2:50) — *Why local*

Cut briefly to the spec overlay one more time. Voiceover:

> Twenty-six hundred dollar GPU. Sixty bucks of electricity per
> thousand scans. Versus a clinical fMRI: five thousand dollars per scan,
> ninety minutes, a radiologist required. We're not replacing the
> radiologist — but we are bringing the experiment to anyone with a
> consumer GPU.

### Close (2:50 – 3:00) — *Where to find it*

Cut to the GitHub Pages site (`https://alexiosbluffmara.github.io/cortex/`)
or the README on the cortex repo.

> Open source. Apache-2.0 for the code, CC-BY-NC for Meta's TRIBE weights.
> Mercury, the agent that orchestrates this, is in a sibling repo. Both
> on GitHub at AlexiosBluffMara.

---

## 3 · The fallback if anything goes wrong on camera

If the local 5090 chokes mid-demo, the cloud relay automatically falls back
to **Gemini Flash narration** for the text tier — the brain-prediction step
won't run (TRIBE v2 is local-only), but you'll get a graceful "queued —
5090 unreachable" status that you can talk to. To recover:

1. Open Window 2's PowerShell, Ctrl-C to kill uvicorn, re-run the start command
2. The relay re-checks `/api/utilization` every 3 s; the public site
   recovers automatically within ~10 s

If the **tunnel** drops, restart cloudflared:
```powershell
# kill the old window, run fresh
cloudflared.exe tunnel --config "C:/Users/soumi/.cloudflared/config.yml" run rtk-5090
```

If you've installed the auto-restart Windows services, neither of these
should be needed — services restart on crash automatically within 60 seconds.

---

## 4 · What to NOT show on camera

- **Any keys or tokens** — the GH secrets, the HF_TOKEN, the GEMINI_API_KEY,
  or anything in `~/.hermes/.env` / `~/.mercury/.env`
- **Any user-uploaded scan that wasn't your own** — the gallery shows
  publicly-submitted ones, but if you click into someone else's, don't
  use it as the demo subject; pick your own NASA upload
- **The Discord bot token reset flow** — show Snowy responding, but don't
  show the Discord developer portal where the token is visible
- **The cost panel** unless you're explicitly making the point about cost —
  it's there but cuts the science momentum

---

## 5 · Suggested narration outline (140 words, 60 seconds at relaxed pace)

> Welcome to Cortex. This is a research collaboration with Illinois State
> University, running on a single RTX 5090 in Chicago.
>
> Upload any short video. Cortex predicts how twenty thousand patches of
> your brain's outer surface respond to it — in about six minutes — and
> Gemma 4 explains the response at three reading levels: a curious teen,
> a college student, a working clinician.
>
> Watch the live overlay: the 5090's VRAM fills up as TRIBE v2 — Meta's
> brain foundation model — runs the prediction. Twenty-two gigs of
> weights, two hertz sample rate, twenty thousand vertices.
>
> When it's done, you get an interactive 3D brain viewer, narration in
> three tiers, and a permanent shareable link.
>
> Open source. Apache-2.0. Local-first. Built by Alexios Bluff Mara
> in association with Illinois State University. Find us on GitHub.

---

## 6 · After-the-record checklist

- [ ] Verify the recorded clip plays back at full quality (1080p, ≥30 fps)
- [ ] Trim to <3 minutes (Kaggle Gemma 4 Good has a hard cap; verify on
      the rules page before submitting)
- [ ] Add captions for accessibility (auto-caption then proof-read)
- [ ] Add the closing card: GitHub URLs + email `soumitlahiri@philanthropytraders.com`
- [ ] Upload to YouTube (unlisted) for the submission, plus a public version
      for X / LinkedIn
- [ ] Cross-link from the GitHub Pages docs site

---

*This guide is part of the cortex repo. For Mercury's submission video
(Nous + Kimi Creative track), see the parallel guide in
`D:/mercury/docs/DEMO_VIDEO_GUIDE.md` (focuses on the agent + Discord
flow rather than the brain viewer).*
