# Pixel 9 Pro Fold as Ascended Base mobile node

`pixel-9-pro-fold` is **not just a phone** — it's a fourth desktop-class node that fits in your pocket. This playbook prefers Google-native primitives (Desktop Mode, Quick Share, Fast Pair, Cross-Device Services) and falls back to Termux/scrcpy only where the native path doesn't reach.

## Architecture role

```
Pixel 9 Pro Fold = "mobile control surface" for Ascended Base
├─ Glance / 1-tap actions     → cover display
├─ Multi-window dev work      → unfolded inner display (8" / 2152×2076)
├─ Desktop-class workstation  → USB-C dock + monitor (Native Desktop Mode)
├─ Demo presenter             → handed to judges to scroll cortex.redteamkitchen.com
└─ Mac-integrated peripheral  → scrcpy / Android Studio mirroring on Big Apple
```

Currently OFFLINE on Tailscale (last seen 11 days). Step 0 wakes it.

## Step 0 — Wake the Pixel + verify Tailscale (5 min)

1. Power on, connect to home Wi-Fi
2. Open the **Tailscale** app → log in with `soumitlahiri@philanthropytraders.com` → confirm node name `pixel-9-pro-fold`
3. Tailscale settings: **Always on** = ON; **Allow Tailscale on metered connections** = ON
4. Verify on Seratonin:
   ```powershell
   & "C:\Program Files\Tailscale\tailscale.exe" status | findstr pixel
   ```
   Should now say `active`, not `offline`.

## Step 1 — Native Android Desktop Mode (the Googley path)

This is the headline feature. Plug the Pixel into a USB-C dock attached to a monitor and you get a real desktop OS — apps in resizable windows, taskbar, multi-window snapping, Material 3 throughout. **First-party. Stable.** Shipped in the March Pixel Drop.

### Hardware needed

- Any USB-C dock with HDMI 2.0 + USB-A passthrough + USB-C PD-in. Examples: Anker 555 ($60), UGREEN Revodok Pro 209 ($45), CalDigit Tuxedo. Don't bother with Thunderbolt — Tensor G4 doesn't speak it.
- USB keyboard + USB mouse (or Bluetooth — see Pixel Buds + multipoint section)
- HDMI cable to your monitor (the 4K next to Baby Pi works great)
- USB-C → USB-C cable from dock → Pixel

### Activate

1. Plug Pixel → dock → monitor → keyboard + mouse
2. Notification on the Pixel: "External display connected" → tap **Switch to desktop**
3. Monitor lights up with the Android desktop UI. Pixel inner screen becomes a touchpad / second display
4. Open Chrome, GitHub mobile site (or chrome://flags → enable "Request Desktop Site"), VS Code Web (`vscode.dev`), Discord, Termux — all in resizable windows

### Daily flows
- **Cortex demo on the road**: dock + portable HDMI monitor (eg. ASUS ZenScreen MB16AC, ~$200) = a laptop without a laptop
- **Living-room workstation**: same 4K monitor Baby Pi drives — KVM-switch the HDMI input or just unplug-replug
- **Ad-hoc dev**: Termux app inside Desktop Mode gives you a real terminal window alongside Chrome. Best of both.

### What the Pixel inner display becomes
With external display connected, the inner 8" screen runs the **virtual touchpad** by default. You can change it to:
- **Mirror** (same content as monitor)
- **Touchpad** (default)
- **Independent app** (eg. Spotify on the Pixel while working on the monitor)

Settings → Display → External display → behavior selector.

## Step 2 — Pixel Buds Pro 2 multipoint (Pixel + Mac at the same time)

The Buds pair to Pixel and Big Apple simultaneously and auto-switch when audio plays on either side.

### One-time pair
1. Charge case → press button on case → **Fast Pair** pop-up on the Pixel → tap Connect
2. Pixel: Settings → Connected devices → Pixel Buds Pro 2 → toggle **Connect to multiple devices** = ON
3. On Big Apple: System Settings → Bluetooth → with case open, the buds appear → click Connect

### Auto-switching examples
- YouTube on Big Apple → buds switch from Pixel to Mac
- Phone call comes in → buds switch back to Pixel
- Spatial audio + head tracking works on both (Apple Music on Mac, YouTube on Pixel)
- **Conversation Detection** (auto-pause + transparency when you start talking) works only when audio is currently routed to the Pixel — not on Mac calls. Sensitivity: Medium.

### Real-time translation
Tap and hold either earbud → "Translate" → speak in English while the other person speaks in their language. The Buds + Pixel relay translations both ways. Useful at the hackathon.

## Step 3 — Use the Pixel inside the Mac (scrcpy + Android Studio)

When the Pixel is at your desk and you want to operate it from Big Apple's keyboard/screen instead of switching focus.

### One-time setup on Big Apple

```bash
brew install scrcpy android-platform-tools
adb devices    # first run will show "unauthorized"
```

On the Pixel, enable USB debugging:
1. Settings → About phone → tap "Build number" seven times
2. Settings → System → Developer options → **USB debugging** = ON
3. Plug USB-C cable Pixel → Mac → accept fingerprint prompt
4. `adb devices` on the Mac now says `device`

### Daily usage

```bash
# Plain mirror with audio + control
scrcpy

# Wireless over Tailscale (no cable)
adb tcpip 5555
adb connect 100.102.198.9:5555    # the Pixel's Tailscale IP
scrcpy

# Mirror only the unfolded inner display (display 1 on the Fold)
scrcpy --display-id=1 --max-size=2152

# Record a demo screen for the hackathon submission
scrcpy --record=demo.mp4 --display-id=1 --max-size=1920 --no-audio
```

### Android Studio mirroring (better for app dev)
```bash
brew install --cask android-studio
# View → Tool Windows → Running Devices → click the Pixel
```
Use this when you also need Logcat / Layout Inspector / Profiler. scrcpy is faster for plain mirroring.

## Step 4 — Quick Share (cross-platform, Googley)

Quick Share now works between Pixel and Mac via Google's official Mac client. Faster than AirDrop for big files, and bidirectional.

```bash
# On Big Apple
brew install --cask quick-share
# First launch: grant Bluetooth + Local Network in System Settings → Privacy & Security
```

After install, share from Pixel → choose Quick Share → Big Apple shows up. Or from Big Apple's Quick Share menu bar app → drag a file → Pixel pops up as a target.

## Step 5 — NFC + UWB underused powers

### NFC tags for one-tap automation
Buy 5× NTAG215 stickers ($5 on Amazon). Use the **NFC Tools** app (free, not the paid one) to write actions. Tap phone to tag → action runs.

Useful tags around the apartment:
- Sticker by the desk → opens `https://mercury.redteamkitchen.com/` in Chrome
- Sticker on the 4K monitor stand → triggers `am start -a android.intent.action.VIEW -d https://cortex.redteamkitchen.com/dashboard` (kiosk handoff to Pixel Desktop Mode)
- Sticker in the gym bag → BT on + connect to Buds + open Spotify

### UWB precision finding
Pixel + Buds Pro 2 case both have UWB. Find My Device locates the case to within ~10 cm (vs. ~3 m on BT alone). Useful when the case is under a couch cushion.

### Cross-Device Services (the sleeper)
Android 15 brought **Cross-Device Services**. Key flows:
- **Camera streaming**: use the Pixel's camera as a webcam on the Mac (Settings → Connected devices → Cross-device → Camera streaming)
- **Internet sharing**: route Mac through Pixel's 5G when home Wi-Fi dies (one tap on Pixel → "Share internet to nearby devices")
- **Clipboard**: copy on Pixel → paste on Mac, both ways

Settings → Connected devices → Cross-device. Toggle each as needed.

## Step 6 — Termux fallback (when Desktop Mode isn't enough)

Native Desktop Mode replaces ~95% of why you'd reach for Termux. Termux is still useful when:
- You need a Linux shell with `apt`, package management, persistent processes
- You want to ssh from the Pixel **without** docking it
- You want to run a long-lived script in the background (Termux:Boot starts it on phone reboot)

### Install (once)
1. Install **Termux** from F-Droid (NOT Play Store — that build is abandoned)
2. Install **Termux:Widget** + **Termux:Boot** from F-Droid

### Setup
```bash
pkg update && pkg upgrade -y
pkg install openssh curl jq termux-tools
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub   # copy this
```

Paste the Pixel's public key into `~/.ssh/authorized_keys` on Seratonin (via `tailscale ssh soumit@seratonin "cat >> ~/.ssh/authorized_keys" < pixel-key.pub`).

### One-tap shortcuts
Save these under `~/.shortcuts/` on the Pixel for Termux:Widget:

```bash
mkdir -p ~/.shortcuts && chmod 700 ~/.shortcuts

# Public health probe of all 5 hostnames
cat > ~/.shortcuts/health <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
for h in redteamkitchen.com cortex.redteamkitchen.com mercury.redteamkitchen.com ollama.redteamkitchen.com inference.redteamkitchen.com; do
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "https://$h/")
  echo "$c  $h"
done
EOF

# Tunnel state
cat > ~/.shortcuts/tunnel <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
TOK=$(grep CLOUDFLARE_API_TOKEN ~/.cloudflare/credentials | cut -d= -f2 | tr -d '"')
ACC=$(grep CLOUDFLARE_ACCOUNT_ID ~/.cloudflare/credentials | cut -d= -f2 | tr -d '"')
curl -s -H "Authorization: Bearer $TOK" \
  "https://api.cloudflare.com/client/v4/accounts/$ACC/cfd_tunnel/10c2805b-e6c2-4cf1-9af4-70f2c88d6f80" \
  | jq -r '.result | "tunnel: \(.status), conns: \(.connections|length)"'
EOF

# SSH shortcuts
cat > ~/.shortcuts/seratonin <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
ssh -o StrictHostKeyChecking=accept-new soumit@100.98.19.87
EOF
cat > ~/.shortcuts/big-apple <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
ssh -o StrictHostKeyChecking=accept-new soumit@100.93.240.52
EOF
cat > ~/.shortcuts/baby-pi <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
ssh -o StrictHostKeyChecking=accept-new soumit@baby-pi
EOF

chmod +x ~/.shortcuts/*
```

Add Termux:Widget to your home screen (long-press → Widgets) → pick each script.

## Step 7 — Mercury Discord push notifications

For incident alerts when the budget kill switch fires or the inference router goes down:

1. Install Discord on the Pixel (Play Store fine here)
2. Make sure you're in `#bot-test-3` where Snowy The Bot lives
3. Configure Snowy via the Mercury runbook to forward GCP budget alerts and `/healthz` failures to that channel

## Battery / power

| State | Battery / hr |
| --- | --- |
| Idle on Wi-Fi + Tailscale always-on | < 1 % |
| Foreground Chrome + Termux | ~ 5 % |
| Native Desktop Mode + monitor | ~ 8 % (charging via dock = net positive) |
| scrcpy mirror over USB-C | ~ 3 % (USB also charges if dock supports PD) |

Outlasts a full work day for any realistic mix.

## What's NOT on the Pixel

- **Inference**. Don't try to run Gemma on Tensor G4. Pin all LLM work to seratonin/big-apple/baby-pi.
- **Restic backups**. Not enough storage; not the right form factor.
- **Always-on dashboard**. That's Baby Pi's job on the 4K monitor.
- **Tailscale exit node** for the family. Battery dies.

## Sources

- [Plugable: How to use Pixel Desktop Mode with USB-C dock](https://kb.plugable.com/how-to-use-android-16-desktop-mode-with-a-pixel-phone-and-usb-c-display-or-hub)
- [Android Authority: Pixel Desktop Mode rolling out in March Drop](https://www.androidauthority.com/desktop-mode-march-pixel-drop-3646069/)
- [GrapheneOS: DisplayPort with Pixel 9 Pro Fold](https://discuss.grapheneos.org/d/16699-displayport-with-pixel-9-pro-fold)
- [Genymobile/scrcpy](https://github.com/genymobile/scrcpy)
- [Tom's Guide: Pixel Buds Pro 2 multipoint with Mac](https://www.tomsguide.com/reviews/google-pixel-buds-pro-2)
- [Google support: Pixel Buds multipoint + audio switch](https://support.google.com/googlepixelbuds/answer/12319417?hl=en)
