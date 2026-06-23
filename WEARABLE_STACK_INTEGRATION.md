# Wearable / mobile stack — Pixel 9 Pro Fold + Pixel Buds Pro 2 + Omi + Flipper

The mobile half of Ascended Base. Each device has a clear role; together they form an always-on personal AI rig.

## Roles

| Device | Form factor | Primary role | Secondary roles |
| --- | --- | --- | --- |
| **Pixel 9 Pro Fold** | foldable phone | Mobile dev workstation (Native Desktop Mode) | NFC reader, Tailscale node, Flipper companion |
| **Pixel Buds Pro 2** | earbuds | Audio I/O for Pixel + Mac multipoint | Real-time translation, Conversation Detection |
| **Omi (Based Hardware)** | necklace/wearable | Always-on conversation logger + AI assistant | Memory writes, action triggering |
| **Flipper Zero** | handheld | Hardware multi-tool (RF, NFC, IR, BadUSB) | Phone-companion via BLE |

Each is a single-purpose device that gets out of the way. Together they cover **every input/output mode you'd want from a personal computing rig**: audio, voice, text, RF, IR, NFC, USB, screen, BLE.

## Pixel Fold — the hub

The Pixel is the orchestrator that all the others talk to. Two modes:

### Pocket mode (folded, cover display)
- Tailscale always-on (~1% battery/hr)
- Discord notifications via Mercury (Snowy bot pings for cluster alerts)
- Flipper Mobile app stays paired via BLE in the background
- Pixel Buds connected (multipoint with Mac)
- Omi paired via BLE (transcribing if you're talking)

### Unfolded / Desktop mode (dock plugged in)
- 8" inner screen → Chromium-style desktop on external monitor
- USB-C dock → HDMI to whatever monitor + USB keyboard + USB mouse
- VS Code Web (vscode.dev), Termux, Chrome, Discord, Linux apps
- Same Tailscale identity, same Flipper Mobile, same Buds — just bigger

### Developer mode setup (one-time)

```
Settings -> About phone -> tap "Build number" 7 times
Settings -> System -> Developer options:
  - USB debugging = ON
  - Wireless debugging = ON
  - Default USB configuration = "File Transfer / Android Auto"
  - Force allow apps on external = ON (lets Termux + others run while in Desktop Mode)
  - Don't keep activities = OFF (or you'll lose state on screen orientation)
```

ADB pairing for headless control from Seratonin / Seratonin:
```bash
# On Seratonin:
brew install android-platform-tools scrcpy
adb devices    # first run shows "unauthorized"
# Plug Pixel via USB-C → accept fingerprint prompt on phone
# OR pair wirelessly: Pixel → Developer Options → Wireless debugging → "Pair device with QR code" → scan from Studio
```

After ADB is paired, scrcpy gives you a window of the Pixel on the Mac with full keyboard + drag-and-drop file install.

## Pixel Buds Pro 2 — multipoint magic

The Buds bridge the Pixel and the Mac at the BT layer. They're not a Flipper accessory.

### One-time pair
1. Pixel Settings → Connected devices → Pixel Buds Pro 2 → toggle **Connect to multiple devices** = ON
2. Seratonin System Settings → Bluetooth → with case open, select Buds → Connect

### Daily flow
- Audio plays on whatever device you started — no manual switch
- Phone call comes in → Buds switch from Mac to Pixel automatically
- **Conversation Detection** (auto-pause + transparency when you start talking) only works when audio is currently routed to the Pixel — not on Mac calls
- **Real-time translation**: tap and hold either earbud → "Translate" → speaks the other language back through the Buds

For demo recording: Seratonin QuickTime → record from Buds mic = high-quality voice for the hackathon submission video.

## Omi — always-on memory + actions

Omi (Based Hardware) is a wearable AI device — necklace pendant with mic + BLE. Records ambient conversation, transcribes via the Omi app on the phone, lets you ask the assistant about anything you've said in the past day/week/month.

### Pair to Pixel
1. Install **Omi** from Play Store
2. Sign in with `soumitlahiri@philanthropytraders.com`
3. Press the button on the Omi pendant → Pixel pops Fast Pair → tap Connect
4. App walks you through wake-word + memory consent

### The integration trick: Omi memory API
Omi exposes a REST API. You can push memories from anywhere:

```bash
curl -X POST https://api.omi.me/v1/memories \
  -H "Authorization: Bearer $OMI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"Cortex hackathon demo started successfully", "tags":["demo","hackathon"]}'
```

This means:
- The Flipper can push memories via BadUSB-curl (we wrote a payload at `/badusb/omi/push-memory.txt`)
- Mercury (Discord bot on Seratonin) can push memories when notable events fire
- The Cortex demo can push a memory when a judge submits a video for analysis
- All memories show up in the Omi app, searchable, narrated back to you on demand

### Why this is googley
Omi makes Soumit's life into a queryable index. "What did the judge ask about TRIBE?" → Omi recalls. "What was that thing the professor said about brain encoding last Tuesday?" → Omi recalls. Combined with the Pixel's cross-device clipboard, you can copy from your past conversations directly into your code.

## Flipper Zero — the physical controller

Already covered in `D:\cortex\FLIPPER_INTEGRATION_PLAYBOOK.md`. Key role in the wearable stack:

- **NFC tags** the Flipper writes get read by the Pixel (Android NFC) AND the iPhone (iOS Core NFC) — write once, work on both ecosystems
- **Flipper Mobile app** on the Pixel = remote-trigger Flipper actions from the phone
- **BadUSB to Seratonin** = a physical "deploy demo" button you carry in your pocket
- **Sub-GHz capture/replay** = control the Hue Bridge indirectly via an ESP32 listener

## All four together — example: hackathon demo flow

You walk into the judging room. In your pocket: Pixel Fold + Flipper. On you: Omi + Pixel Buds.

1. **NFC tag on the projector** (you stuck it there earlier) → Pixel taps it → opens cortex.redteamkitchen.com/dashboard in Chrome (Pixel Native Desktop Mode if docked, otherwise mobile)
2. **Flipper IR fires "projector ON + HDMI 2"** to the projector
3. **Pixel Buds**: stay quiet, multipoint connected, ready for any call
4. **Omi**: starts recording the demo conversation; in 30 minutes you'll have a transcript searchable by topic
5. You start the demo. **Flipper plugged into Seratonin via USB-C** (BadUSB mode) → "deploy" button macro types the start-demo command
6. Cortex inference runs on Seratonin via Tailscale → response on the projector in 12 seconds
7. **Pixel Fold's outer cover display** shows live status while folded — checking inference router latency without unfolding
8. Judge asks a complex question → **Pixel Buds tap-and-hold for translate** if they're not native English
9. Judge says yes → **Flipper "lock-pc.txt" payload** locks Seratonin cleanly, you wrap up
10. Walk out → **Omi memory** has the entire conversation, searchable, narratable

That's the loop. Every device has one thing it does, and they hand off without you thinking.

## What you need to do (one-time, in order)

| Step | Where | Time |
| --- | --- | --- |
| 1. Wake Pixel + verify Tailscale active | Pixel | 2 min |
| 2. Enable Developer Options + USB debugging | Pixel | 1 min |
| 3. ADB pair Pixel ↔ Seratonin (USB then wireless) | Both | 2 min |
| 4. Pair Pixel Buds Pro 2 to Pixel + Mac (multipoint) | Pixel + Mac | 3 min |
| 5. Install Omi app + pair pendant | Pixel + Omi | 5 min |
| 6. Test Omi memory API (paste an `OMI_TOKEN` env var on Seratonin) | Seratonin | 2 min |
| 7. Install Flipper Mobile on Pixel + BLE pair | Pixel + Flipper | 3 min |
| 8. Write your first NFC tag (URL → Cortex dashboard) | Flipper + sticker | 2 min |
| Total | | ~20 min |

## Sources
- [Pixel Native Desktop Mode docs](https://support.google.com/pixelphone/answer/14749756)
- [Pixel Buds Pro 2 multipoint](https://support.google.com/googlepixelbuds/answer/14086019)
- [Based Hardware (Omi)](https://www.basedhardware.com)
- [Omi API docs](https://docs.omi.me)
- [Flipper Mobile (Android)](https://play.google.com/store/apps/details?id=com.flipperdevices.app)
