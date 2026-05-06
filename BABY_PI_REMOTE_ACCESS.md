# Baby Pi — every way to get into it remotely

Goal: cover every legitimate path from Seratonin (Windows desktop), Big Apple (Mac), Pixel Fold, or any other client. Pick the right tool for the job, don't try to make one tool do all of them.

## TL;DR — pick one per use case

| Use case | Tool | Why |
| --- | --- | --- |
| Run a shell command, run a script, edit a config | **Tailscale SSH** | Identity-keyed (Google account), zero-config, works from any node |
| Visual desktop session occasionally | **xrdp** (Microsoft RDP) | Native Windows Remote Desktop client, works from Pixel Fold's RD app, free |
| Low-latency 60fps game-streaming-style remote | **Sunshine + Moonlight** | H.264/HEVC hardware encode where supported, ~20ms LAN latency |
| "I just need to see it from a browser anywhere" | **Raspberry Pi Connect** | Free, official, Google-account-grade simple; Pi Foundation hosts the rendezvous |
| Re-image / recover a brick | **micro-HDMI + USB keyboard** | The escape hatch you keep next to the Pi |

**Parsec on Linux RPi: NO.** Parsec doesn't ship a Linux ARM64 host. It needs an x86 GPU encoder (NVENC, AMF, QuickSync). RPi 5 has VideoCore VII which Parsec doesn't support. Use Sunshine instead — same UX, free, ARM64 supported.

---

## 1. Tailscale SSH — primary path for ops

Already on your stack. Free tier covers everything you need.

### From Seratonin (Windows)
```powershell
& "C:\Program Files\Tailscale\tailscale.exe" ssh soumit@baby-pi
```

### From Big Apple (Mac)
```bash
tailscale ssh soumit@baby-pi
```

### From Pixel Fold (Termux **or** native SSH client)
```bash
ssh soumit@baby-pi      # works once Tailscale is running on the phone
```
Tailscale routes via `100.x.x.x` automatically; no port forwarding, no public exposure.

### Why not raw SSH on port 22?
You can do that too — works the same. Tailscale-SSH-on-top adds two things:
1. **Identity-keyed**: auth becomes "is this your Tailscale account?", not "is this key in authorized_keys?". Onboard a new device → SSH already works.
2. **Audit log**: Tailscale admin panel shows every SSH session with from/to/duration, free tier.

If Tailscale ever goes down, raw `ssh -p 22 soumit@<lan-ip>` is the fallback.

### One-time setup on Baby Pi (already in `firstrun.sh`)
```bash
sudo tailscale up --ssh --hostname=baby-pi --auth-key=<one-shot-key>
```

---

## 2. xrdp — visual desktop, RDP-compatible

Works with the **native Windows Remote Desktop client** (Soumit already has it), Microsoft RD on iOS/iPad/Pixel, and `xfreerdp` on Mac. No third-party Windows client needed.

### Install on Baby Pi
```bash
sudo apt install -y xrdp xfce4 xfce4-goodies
sudo systemctl enable --now xrdp
sudo ufw allow from 100.64.0.0/10 to any port 3389  # Tailscale CGNAT range only
```

### Connect from Seratonin
```powershell
mstsc.exe /v:baby-pi:3389
# Login: soumit / <your password>
```

### Connect from Big Apple
- Install **Microsoft Remote Desktop** from the Mac App Store (free)
- Add PC: `baby-pi`, save credentials

### Connect from Pixel Fold
- Install **Microsoft Remote Desktop** from Play Store
- On the unfolded 8" screen, RDP renders as a usable Linux desktop. Add `baby-pi` as a connection.

### When to use xrdp vs Sunshine
- **xrdp**: occasional troubleshooting, file manager, web browser quick-test. Latency ~80-150 ms, but bandwidth ~ 2-5 Mbps so it works on cellular.
- **Sunshine**: when you want to actually *use* the Pi (4K dashboard editing, watching it think). 20-40 ms latency, 10-50 Mbps, LAN ideal.

---

## 3. Sunshine + Moonlight — game-streaming-grade remote

Sunshine is open-source server (replaces Nvidia GameStream). Moonlight is the client — open-source, on every platform.

### Install Sunshine on Baby Pi (ARM64)
RPi 5 has hardware H.264 encode in VideoCore VII; Sunshine uses it via VAAPI. Build is from source (no apt package for ARM64 yet, ~10 min build).

```bash
sudo apt install -y \
    build-essential cmake git ninja-build \
    libssl-dev libavdevice-dev libboost-locale-dev libboost-log-dev \
    libboost-program-options-dev libpulse-dev libopus-dev libxtst-dev \
    libx11-dev libxrandr-dev libxfixes-dev libxcb-shm0-dev libxcb-xfixes0-dev \
    libxcb-shape0-dev libxcb-randr0-dev libxcb-image0-dev libwayland-dev \
    libdrm-dev libcap-dev libcurl4-openssl-dev libpipewire-0.3-dev \
    nodejs npm

git clone https://github.com/LizardByte/Sunshine.git --recursive ~/sunshine
cd ~/sunshine
mkdir build && cd build
cmake -DSUNSHINE_ENABLE_X11=ON -DSUNSHINE_ENABLE_WAYLAND=ON \
      -DSUNSHINE_ENABLE_DRM=ON -DSUNSHINE_ENABLE_VAAPI=ON \
      -G Ninja ..
ninja
sudo ninja install

sudo systemctl --user enable --now sunshine
```

Sunshine web UI: `https://baby-pi:47990` (self-signed cert, accept once). Pair every Moonlight client by typing the 4-digit PIN it displays.

### Install Moonlight clients
- **Big Apple (Mac)**: `brew install --cask moonlight` *or* `Moonlight Game Streaming` from Mac App Store
- **Seratonin (Windows)**: `winget install Moonlight.Moonlight`
- **Pixel Fold**: "Moonlight Game Streaming" from Play Store
- **iPhone172**: "Moonlight Game Streaming" from iOS App Store

### Connect
Each Moonlight client auto-discovers Sunshine on your LAN (mDNS). Click Baby Pi, enter the PIN once. Stream starts.

### Stream the 4K kiosk dashboard from anywhere
Once paired, Moonlight from your Pixel on cellular pulls the same 4K display Baby Pi is driving. Latency on a good 5G connection: ~70-120 ms. Usable for monitoring, not gaming.

---

## 4. Raspberry Pi Connect — easiest browser-based path

Free, official, runs `connect.raspberrypi.com` as the relay. Identity = your Raspberry Pi ID account (you already created one, per the earlier screenshot showing `soumitlahiri@philanthropytraders.com` logged in to Pi Connect).

### Install on Baby Pi
```bash
sudo apt install -y rpi-connect
rpi-connect on
rpi-connect signin     # opens a URL on the Pi's screen, you visit it on Seratonin
```

### Use
Visit https://connect.raspberrypi.com → pick `baby-pi` → "Screen sharing" or "Remote shell". Done.

Works on the Pixel Fold's Chrome browser too.

### Why use this when you have Tailscale + xrdp?
- **Public links**: you can share a single-use URL with someone (e.g. Soumit's professor) without putting them on Tailscale.
- **Audit trail**: every session shows up in the Pi Connect dashboard.
- **No client install** — just a browser.

But for *you*, Tailscale SSH is faster.

---

## 5. Chrome Remote Desktop — Google-native fallback

If Pi Connect doesn't work for some reason, Google's CRD does.

### Install on Baby Pi
```bash
curl -fsSL https://dl.google.com/linux/direct/chrome-remote-desktop_current_arm64.deb -o /tmp/crd.deb
sudo apt install -y /tmp/crd.deb
sudo systemctl restart chrome-remote-desktop@$USER
# Visit remotedesktop.google.com/headless on the Pi (or paste the curl-given setup string).
```

### Use
- Visit https://remotedesktop.google.com/access from any Chrome browser, signed in to your Google account.
- Click `baby-pi` → enter PIN.

This is the **Googley** answer — same auth as your Workspace account, no separate identity. Use as the public-link alternative to Pi Connect.

---

## 6. Local console (the recovery option)

Always keep this working as the escape hatch:
- micro-HDMI to HDMI cable into the 4K monitor in the living room
- USB keyboard plugged into the Pi
- Power cycle = boot up

If networking ever breaks (bad config, wrong WiFi password), this is how you fix it.

---

## Putting it all together — the firstrun.sh expansion

The existing `D:\cortex\scripts\baby-pi-boot\firstrun.sh` already installs Tailscale. Add (next session) the rest:

```bash
# (append to firstrun.sh after Tailscale block)

# --- xrdp + xfce4 ---
apt-get install -y xrdp xfce4 xfce4-goodies

# --- Pi Connect (already on RPi OS, just enable) ---
rpi-connect on || true
# (rpi-connect signin needs an interactive URL approval — defer to first SSH session)

# --- Chrome Remote Desktop ---
curl -fsSL https://dl.google.com/linux/direct/chrome-remote-desktop_current_arm64.deb \
  -o /tmp/crd.deb
apt-get install -y /tmp/crd.deb || true

# --- Sunshine: build deferred to first SSH session (long compile) ---
mkdir -p /home/soumit/.sunshine-todo
echo "Run install-sunshine.sh manually after first login" > /home/soumit/.sunshine-todo/README.txt
```

Sunshine build is ~10 min and shouldn't run in firstrun (would delay boot). Trigger it manually after first SSH.

---

## Network ports reference

| Port | Service | Exposed on |
| --- | --- | --- |
| 22 | SSH (and Tailscale SSH) | Tailscale interface only via UFW |
| 3389 | xrdp | Tailscale interface only |
| 47984/47989/47990/48010 | Sunshine | Tailscale + LAN (for Moonlight discovery) |
| 8000 | BitNet llama-server | Tailscale only (tunnel rule points here) |
| (none) | Pi Connect / Chrome RD | Outbound only — no inbound port |
