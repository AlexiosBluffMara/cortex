# Baby Pi reboot — Living-Room Gemma node + smart-home brain

## New role (replacing BitNet)

Baby Pi (Raspberry Pi 5, 8 GB) becomes:

1. **A 4th inference backend** — Ollama serving heavily quantized Gemma 4 e2b
   for low-latency LAN calls. Joins the 4-node orchestra alongside MLX
   on Seratonin, Ollama on Seratonin, and Ollama on Seratonin.
2. **A smart-home brain** — exposes a FastAPI service on `:8000` that wraps
   Hue + Google Cast + the local LLM. Inference router can route
   "smart home" intents here so commands stay strictly on-LAN.
3. **A 4K kiosk** — Chromium full-screen in dual-monitor mode, one display
   over the bed and one over the couch in the living room, both showing
   the live cluster dashboard (orchestra state, tok/s, training progress,
   active Hue colors).

## Hardware path

### Display option A — native dual micro-HDMI (recommended, no drivers)

Pi 5 has 2× micro-HDMI ports. Each can drive a 4K@60 display independently.
- Cable: 2× micro-HDMI → HDMI cables (Cable Matters, Amazon Basics)
- Run 1 cable to each TV — done

This is the **simplest** and most reliable path. No Targus drivers needed.
Use the Targus dock for keyboard/mouse/ethernet/extra-USB only.

### Display option B — Targus USB-C dock w/ DisplayLink (if you must)

The Pi 5 USB-C port is **power only** — it does NOT carry DP-alt-mode video.
The only way to drive monitors over a single USB-C from the Pi is
**DisplayLink** (Synaptics chipsets DL-3000 / DL-5500 / DL-6950).

Most Targus universal docks (DOCK130USZ, DOCK220USZ, DOCK310USZ, DOCK425USZ,
TSA1755T) use DisplayLink. Check the bottom label for "DisplayLink" or a
DL-#### chipset.

If yes:
1. Install `displaylink-debian` (or build `evdi` kernel module + the
   userspace `displaylink-driver` package for aarch64)
2. Pi boots, lsusb shows `Synaptics DisplayLink Manager`
3. xrandr lists DVI-I-1, DVI-I-2 etc. as additional outputs
4. xorg.conf maps them to your two TVs

The setup script handles both (auto-detects DL chipset and installs only
if found).

## Software stack

| Service | Port | Notes |
|---|---|---|
| Ollama (gemma4:e2b q4_k_m) | 11434 | Tier-4 backend in orchestra |
| smart-home-relay (FastAPI) | 8000 | Wraps Hue + Cast + LLM intent routing |
| kiosk (Chromium fullscreen) | n/a | Auto-launched at boot via systemd user unit |
| Tailscale | 41641 | Mesh VPN (already on Pi) |
| sshd | 22 | Tailscale SSH (already on Pi) |

## Inference budget

Pi 5 (Cortex-A76 quad @ 2.4 GHz, 8 GB LPDDR4X), llama.cpp arm64 NEON:

| Model | Quant | Size | Tokens/s |
|---|---|---|---|
| `gemma4:e2b` | q4_k_m | ~1.5 GB | **~6-8 t/s** |
| `gemma4:e2b` | q2_k | ~700 MB | ~10-12 t/s |
| `gemma4:e4b` | q2_k | ~2.0 GB | ~3-4 t/s |

Default: `gemma4:e2b q4_k_m` for the orchestra probe. Plenty fast for
"is the front door light off?" style queries.

## Deployment order

1. Boot Pi from existing SD (the headless setup that's already done — see
   `BABY_PI_RECOVERY_PROCEDURE.md`)
2. SSH in via Tailscale (`ssh soumitlahiri@baby-pi`)
3. Run `bash setup-baby-pi-llm.sh` — installs Ollama + gemma model + relay
4. Plug in physical hardware:
   - Two micro-HDMI cables to the two TVs
   - Targus dock USB-C for keyboard/mouse/ethernet/peripherals
5. Run `bash setup-baby-pi-kiosk.sh` — installs X11 + dual-monitor xorg.conf + Chromium kiosk systemd unit
6. Reboot — both screens show the dashboard URL automatically
7. On Seratonin/Seratonin: update orchestra `BACKENDS` list to add
   `ollama-baby-pi` (port 11434, model `gemma4:e2b`) and remove the BitNet entry

After step 7, the orchestra goes **GREEN** with all 4 nodes online and
the Living Room Mini announces "All 4 Ascended Base nodes online,
fastest is X at Y tokens per second."
