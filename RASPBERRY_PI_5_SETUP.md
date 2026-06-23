# Raspberry Pi 5 — third-tier ternary-LLM node

**Goal:** add a $80, 12 W always-on node running BitNet b1.58 (1.58-bit ternary, ~700 MB) to the Cortex stack. Lives in the living room. Displays a live demo dashboard on the 4K monitor. Becomes part of the inference router as a Tier 4 fallback for short-form requests.

This is one of the most pitchable parts of the system: **"a $80 single-board computer running a real LLM at 11 tokens/sec, no GPU, no cloud"** is hackathon-judge catnip.

---

## Why we want this in the stack

| Role | What it does | Why the RPi 5 specifically |
| --- | --- | --- |
| **Inference Tier 4** | Short-prompt fallback when the 5090 is busy or offline | BitNet b1.58 2B-4T runs at 8-11 tok/s on CPU |
| **Always-on coordinator** | Lightweight FastAPI proxy + cron jobs | 12 W draw, 24/7 OK; no fan on the 8 GB version |
| **Live dashboard host** | 4K monitor in living room shows real-time demo metrics | RPi 5 has dual 4K HDMI out, plays nicely with Plymouth |
| **Edge classifier** | "Is this video safe?" gate runs locally on every demo submission | Free, fast, runs even when network's down |
| **Restic backup target** | NVMe-backed Restic repo | Cheap, off-machine from the desktop, encrypted |

For competitions: single-line pitch is **"local-first inference: RTX 5090 for the real work, optional cloud TRIBE when funded, and Raspberry Pi 5 for tiny edge tasks — same router, different price points, demo still degrades gracefully."**

For production: it's the cheapest insurance against your two main machines being down. As long as the RPi is up, *something* responds.

---

## Bill of materials (~$130 if buying everything)

| Item | Why | Cost |
| --- | --- | --- |
| **Raspberry Pi 5 (8 GB)** | 8 GB is enough; 16 GB only matters for >3B models | $80 |
| Official 27 W USB-C PD power supply | RPi 5 needs ≥5V/5A; off-spec adapters throttle | $13 |
| Active cooler (official) | BitNet pegs all 4 cores; passive throttles in 30 s | $5 |
| microSD ≥ 32 GB **or** NVMe SSD + Pimoroni NVMe Base | NVMe is 5× faster + the model is on disk | $25 SD / $35 NVMe + $15 base |
| micro-HDMI → HDMI cable (2 m) | RPi 5 uses micro-HDMI on the board side | $8 |
| Cat6 ethernet cable (long enough to reach the bedroom router) | Optional, see networking below | $0–15 |

You probably already have a USB keyboard and HDMI cable lying around. The only mandatory new buy is the RPi + cooler + PSU + storage.

---

## Network topology — what to actually do

Your bedroom has the WiFi router and the desktop. RPi is in the living room. You asked: **ethernet vs WiFi vs Tailscale**.

### Recommendation: **WiFi + Tailscale, ethernet only if you decide to**

**Why WiFi is enough:**
- BitNet inference traffic is **< 5 KB/s** per request (text in, text out)
- Cortex demo videos that flow to the RPi for the gate-classifier: ~1-5 MB per call, every few minutes during a demo session — easily handled by 802.11ac
- Backups via Restic are already deduplicated; nightly delta is < 50 MB
- The RPi 5 has WiFi 5 (802.11ac); real-world ~150-250 Mbit/s in a typical home

**When to add ethernet later:** if you start moving the full TRIBE input video corpus (~50 GB) between machines, then yes ethernet. But for the core inference + dashboard role, WiFi is fine.

**Private overlay optional:** the Pi should not be part of the public Cortex route.
Use local LAN or a private overlay only for maintenance, and keep public traffic on
Cloudflare-backed routes.

### If you want ethernet anyway

Cleanest setup:
1. Buy a small unmanaged Gigabit switch ($15) or use a port on the back of your router/modem
2. Run Cat6 from bedroom switch → living-room RPi
3. RPi auto-gets DHCP from your home router
4. Tailscale still runs; it just uses the wired link instead of WiFi
5. Optional: **keep WiFi OFF on the RPi** (`sudo rfkill block wifi`) to force traffic over the wire — slightly lower latency, lower contention with your other devices

For a hackathon demo, ethernet is reassuring (no WiFi flake on stage) but in your home setup, it's a nice-to-have, not a need-to-have.

---

## Setup — step by step

### Step 0 — Flash the SD card (or NVMe SSD) on your desktop

1. Download **Raspberry Pi Imager** for Windows: https://www.raspberrypi.com/software/
2. Choose Device: **Raspberry Pi 5**
3. Choose OS: **Raspberry Pi OS (64-bit)** — pick Lite if you don't need a desktop, Full if you want the 4K monitor to show GUI. **Pick Lite for now**, we'll add a custom dashboard later.
4. Choose Storage: your microSD or NVMe via USB
5. Click the gear icon (⚙) BEFORE writing — these pre-configure the OS so first boot just works:
   - **Hostname**: `baby-pi`
   - **Username**: `soumit` / **password**: a strong one (or paste your existing SSH public key under Services tab)
   - **Wireless LAN**: tick + enter your WiFi SSID + password + country code US
   - **Locale**: America/Chicago, en_US.UTF-8
   - **Services tab**: Enable SSH → use **public-key authentication only**, paste your public key from `~/.ssh/id_ed25519.pub` on this Windows desktop
6. Write the image. Takes 5-15 min depending on storage.

### Step 1 — First boot

1. Insert the storage into the RPi
2. Connect: HDMI to the 4K monitor, USB keyboard, USB-C power LAST
3. Power on. First boot takes 1-2 min while it expands the filesystem.
4. You should see a login prompt on the 4K monitor.
5. From your Windows desktop, find the RPi on Tailscale or local network:
   ```powershell
   # If on the same WiFi network:
   ping baby-pi.local       # mDNS, usually works on home networks
   # OR just scan ARP:
   arp -a | findstr "192.168" | sort
   ```
6. SSH in from Windows (using the key you put in via Imager):
   ```powershell
   ssh soumit@baby-pi.local
   # or by IP:
   ssh soumit@<rpi-local-ip>
   ```

### Step 2 — Update OS + install build tools

Run on the RPi:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
  build-essential cmake git python3-pip python3-venv \
  ninja-build curl htop tmux jq

# Reboot to pick up any kernel updates
sudo reboot
```

### Step 3 — Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname=baby-pi
# It prints a login URL — open it on your desktop, log in to your Tailscale account, approve.
```

After approval, on your Windows desktop:
```powershell
& "C:\Program Files\Tailscale\tailscale.exe" status | findstr baby-pi
# should show:  100.x.x.x   baby-pi   soumitlahiri@   linux   active
```

From now on, you can SSH from anywhere via:
```powershell
& "C:\Program Files\Tailscale\tailscale.exe" ssh soumit@baby-pi
```

### Step 4 — Install bitnet.cpp + the ternary model

```bash
cd ~
git clone --recursive https://github.com/microsoft/BitNet bitnet
cd bitnet

# Python venv for the conversion tooling
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Pull the pre-quantized 2B-4T model (~700 MB)
huggingface-cli download microsoft/bitnet-b1.58-2B-4T-gguf \
  --local-dir models/bitnet-b1.58-2B-4T \
  --include "ggml-model-i2_s.gguf"

# Build the C++ runtime (uses the OS-installed cmake/ninja)
python setup_env.py --hf-repo microsoft/bitnet-b1.58-2B-4T -q i2_s
# (this builds ./build/bin/llama-cli with the BitNet kernels enabled)

# Smoke test
./build/bin/llama-cli \
  -m models/bitnet-b1.58-2B-4T/ggml-model-i2_s.gguf \
  -p "What does Cortex do? Answer in one sentence." \
  -n 100 -t 4 --temp 0.7
```

You should see something like:
```
... 8.4 tokens / sec on baby-pi
... Cortex predicts a person's brain response to a video using a foundation model called TRIBE v2.
```

If you get < 5 tok/sec, check `vcgencmd measure_temp` — if it's ≥ 80°C the cooler isn't on or seated correctly. RPi 5 throttles hard at 85°C.

### Step 5 — Wrap it as an OpenAI-compatible HTTP server

The inference router speaks an OpenAI-ish "/v1/generate" interface. We need a little shim. There's `llama-server` in the BitNet build:

```bash
cd ~/bitnet
./build/bin/llama-server \
  -m models/bitnet-b1.58-2B-4T/ggml-model-i2_s.gguf \
  --host 0.0.0.0 --port 8000 \
  -c 2048 -t 4 \
  --log-disable >/tmp/bitnet-server.log 2>&1 &
disown
```

Then from anywhere on Tailscale:
```bash
curl http://baby-pi:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"bitnet","messages":[{"role":"user","content":"hi"}],"max_tokens":50}'
```

### Step 6 — Auto-start on boot via systemd

```bash
sudo tee /etc/systemd/system/bitnet-server.service > /dev/null <<'EOF'
[Unit]
Description=BitNet b1.58 inference server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=soumit
WorkingDirectory=/home/soumit/bitnet
ExecStart=/home/soumit/bitnet/build/bin/llama-server \
  -m /home/soumit/bitnet/models/bitnet-b1.58-2B-4T/ggml-model-i2_s.gguf \
  --host 0.0.0.0 --port 8000 \
  -c 2048 -t 4 --log-disable
Restart=always
RestartSec=5
Nice=10
CPUSchedulingPolicy=idle

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now bitnet-server.service
sudo systemctl status bitnet-server.service
```

`Nice=10` + `CPUSchedulingPolicy=idle` keeps the bitnet process from starving the dashboard renderer. With 4 cores it's still fast.

### Step 7 — Live demo dashboard on the 4K monitor

This is what makes it pitchable. We run a fullscreen Chromium kiosk pointed at a Cortex status page. Total cost: zero, looks like a $5K AI control center.

```bash
# Install the desktop bits (skip if you picked Full OS earlier)
sudo apt install -y --no-install-recommends \
  xserver-xorg x11-xserver-utils xinit \
  chromium-browser unclutter matchbox-window-manager

# Tiny startup script
mkdir -p ~/.config/autostart-cortex
cat > ~/.config/autostart-cortex/dashboard.sh <<'EOF'
#!/usr/bin/env bash
xset -dpms; xset s off; xset s noblank
unclutter -idle 0 &
matchbox-window-manager -use_titlebar no &
chromium-browser \
  --kiosk \
  --noerrdialogs --disable-infobars --disable-translate \
  --check-for-update-interval=315360000 \
  --no-first-run --start-maximized \
  --app=https://cortex.redteamkitchen.com/dashboard
EOF
chmod +x ~/.config/autostart-cortex/dashboard.sh

# Auto-login + auto-X
sudo raspi-config nonint do_boot_behaviour B4   # console autologin then startx
echo "[ -z \"\$SSH_TTY\" ] && [ \"\$XDG_VTNR\" = \"1\" ] && exec startx ~/.config/autostart-cortex/dashboard.sh" \
  >> ~/.bash_profile
```

Reboot. Within 30 s the 4K monitor shows the Cortex dashboard fullscreen, no chrome, no cursor, no "Press F1 to continue." In a hackathon room, this looks deliberate.

(I'll build the actual `/dashboard` page next — it queries the inference router's `/healthz` every 2 s and renders a real-time view of which node is doing what.)

### Step 8 — Optional: Restic backup target

If you want the RPi to ALSO be the backup target for desktop+Mac:

```bash
# Mount an external USB SSD, format ext4, mount at /mnt/backup
# Then init a Restic repo:
restic -r /mnt/backup/restic init
# (note the password it generates — store it in 1Password)
```

On seratonin (Windows) and seratonin (Mac), point Restic at `sftp:soumit@baby-pi:/mnt/backup/restic` for nightly backups. The RPi just sits there receiving deduplicated encrypted blobs. Doesn't need to do anything else.

---

## Add the RPi to the inference router

After Step 6, on this Windows desktop, edit the env file the router uses:

```bash
cat >> ~/.cortex/inference.env <<EOF
# Inference backends, tried in order, with model affinity
INFERENCE_BACKENDS="ollama:seratonin:11434,ollama:seratonin:11434,bitnet:baby-pi:8000"
INFERENCE_BITNET_TIMEOUT_S=60
EOF

# Restart the router
powershell -Command "Get-Process python | Where-Object { \$_.CommandLine -match 'inference_router.server' } | Stop-Process -Force"
bash ~/.cortex/start-router.sh
```

Routing rules (encoded next time I patch the router):

| Request shape | Goes to |
| --- | --- |
| TRIBE inference | seratonin only |
| Long narration (> 500 tokens) | seratonin → seratonin |
| Short narration (< 200 tokens) | seratonin → seratonin → baby-pi |
| Vision-gate "is this safe?" | seratonin → baby-pi (BitNet handles intent classification well) |
| Embeddings | seratonin OR seratonin round-robin |

---

## Verification — what success looks like

After Step 6:
```bash
ssh soumit@baby-pi systemctl is-active bitnet-server
# active

curl -s http://baby-pi:8000/v1/models | jq
# {"data":[{"id":"bitnet"...}]}

# From any node, time a 50-token generation
time curl -s http://baby-pi:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Hello","max_tokens":50}' | jq -r .choices[0].text
# real ~6s (at 8 tok/s for 50 tokens) — FINE
```

If the dashboard step (7) is done:
- 4K monitor shows the Cortex status page in fullscreen
- Cursor doesn't appear
- Survives reboot

---

## Power consumption

| State | Watts |
| --- | --- |
| Idle | ~3 W |
| Dashboard (just Chromium) | ~5 W |
| BitNet inference (active) | ~10 W |
| Peak (boot, all 4 cores) | ~12 W |

24/7 at idle: ~26 kWh / year ≈ **$3 / year of electricity**. Nothing.

---

## Failure modes + recovery

1. **SD card corruption after months** — most common RPi failure. Mitigation: use NVMe, enable read-only root (or move to overlay-fs). Restore: re-flash, restore from Restic.
2. **Thermal throttle in summer** — happens if cooler isn't seated. Mitigation: Active Cooler is mandatory; check `vcgencmd measure_temp` weekly.
3. **WiFi drops during long-running inference** — shouldn't matter, llama-server holds the connection. If using ethernet, even more reliable.
4. **Power outage** — RPi reboots, all systemd services come back up, dashboard resumes. No babysitting.

---

## Sources

- [microsoft/BitNet — official inference framework](https://github.com/microsoft/BitNet)
- [BitNet b1.58 2B-4T model on HuggingFace](https://huggingface.co/microsoft/bitnet-b1.58-2B-4T)
- [How well do LLMs perform on a Raspberry Pi 5? — Stratosphere Lab](https://www.stratosphereips.org/blog/2025/6/5/how-well-do-llms-perform-on-a-raspberry-pi-5)
- [Local LLMs on Raspberry Pi — Adafruit BitNet tutorial](https://learn.adafruit.com/local-llms-on-raspberry-pi/bitnet)
- [1-bit AI Infra — Fast and Lossless BitNet b1.58 Inference on CPUs (arXiv 2410.16144)](https://arxiv.org/abs/2410.16144)
