# Flipper Zero — Ascended Base integration playbook

How the Flipper plugs into Soumit's existing setup: Pixel 9 Pro Fold, Pixel Buds Pro 2, projector, monitors, Hue lights, Echo Dots, Google devices.

## Hardware integration map

```
                         [ FLIPPER ZERO ]
       ┌──────────┬──────────┬──────────┬──────────┐
       │  Sub-GHz │   NFC    │    IR    │  BadUSB  │  BLE
       ▼          ▼          ▼          ▼          ▼
  Doorbell    Stickers     Projector   Seratonin   Pixel Fold
  RF buttons  Hue keys     TV / AC     keyboard    (Flipper Mobile app)
  ESP32       Donor tags   Echo Dot    payloads    NFC handoff
  bridges     URLs         Sound bar
```

## Five novel uses (with the gear you already own)

### 1. NFC tap → Cortex demo deploy on the projector
Workflow:
- Stick an NTAG215 sticker on your laptop bag
- Pixel Fold in pocket reads it on contact (auto-trigger via Tagmo / NFC Tools)
- Pixel opens `https://cortex.redteamkitchen.com/dashboard`
- Pixel routes Quick Share to Big Apple → Big Apple's `start-demo.sh` runs locally
- Flipper IR fires `projector_power_on` + `projector_input_HDMI2`
- Hue scene "demo bright" triggers via the Pixel's Google Home app (REST call to local Hue Bridge)
- Result: by the time you're seated, projector is warming up, demo is loading, lights are right

Files:
- `/mnt/f/nfc/ascended-base-demo-tag.nfc` (NFC capture)
- `/mnt/f/infrared/<projector-brand>.ir` (from IRDB)
- Pixel: NFC Tools profile → URL + intent

### 2. Flipper as the universal Cortex remote
Pre-program three IR codes and three sub-GHz codes onto the Flipper:

| Button | Maps to | Why |
| --- | --- | --- |
| Up | Projector ON | Demo start |
| Down | Projector OFF | Emergency kill |
| Left | Echo Dot mute (433 MHz → ESP32 → MQTT) | Stop background music |
| Right | Hue scene "demo" | Color-correct stage lighting |
| OK | Trigger Mac volume mute (USB-C → BadUSB cmd+F1) | Audio panic |
| Back | Pixel notification "demo recovering" via ntfy.sh | Tell yourself to breathe |

Flipper d-pad becomes a deliberate, tactile control surface. Better than touching the phone mid-presentation.

### 3. BadUSB "deploy demo" macro (already on the SD)
File: `/mnt/f/badusb/ascended-base/start-cortex-demo.txt`

Plug the Flipper into Seratonin via USB-C. Open Flipper → BadUSB → start-cortex-demo. The Flipper acts as a keyboard, types out:
- Win+R → Run dialog
- Launch `D:\cortex\scripts\start-demo.ps1` → fires off Mercury dashboard, opens Cortex landing in Chrome kiosk, starts screen recording
- Total time: 5 seconds. **You walk in, plug in the Flipper, demo is live.**

### 4. Hue + Echo unified panic switch via cheap RF doorbell
Hardware: $8 doorbell button on Amazon (433 MHz), ESP32 ($5) plugged into Baby Pi via USB-A.

Workflow:
- Capture the doorbell button's 433 MHz signal with Flipper → save as `/mnt/f/subghz/panic.sub`
- Flash ESP32 with simple receiver code that listens on 433 MHz and POSTs to Baby Pi when triggered
- Baby Pi's panic handler:
  - HTTP POST to Hue Bridge → `all-lights-flash-red`
  - MQTT → Echo Dots: "Demo emergency, taking it from the top"
  - ntfy.sh notification → Pixel Fold buzzes
- Press the $8 button anywhere in the apartment → entire ecosystem responds

Use case: hackathon judging, you need 30 seconds. Press the button. Lights signal "wait." Echo politely asks the room. Pixel buzzes you to remember to switch to backup slide.

### 5. Donor NFC tags for the Cortex Stripe page
Make 10 NTAG215 stickers, each with a unique URL parameter:

```
https://redteamkitchen.com/donate?ref=tag-001
https://redteamkitchen.com/donate?ref=tag-002
...
```

At the hackathon, hand one to each judge / mentor / spectator. They tap their phone → Stripe Checkout opens with the right `ref` in the URL → you can attribute donations back to who got which sticker.

Cost per sticker: $0.50.
Brand recall: extreme (physical artifact > QR code).
Stripe metadata: tracks which sticker converted, useful for next time.

## Pixel Fold + Flipper combo workflows

### Tag-driven actions (NFC Tools app on Pixel)
The Pixel reads NFC tags (writable by the Flipper) and runs Android Intents:

```
Tag                      Pixel action
-------------------------------------
"On the desk"            open https://mercury.redteamkitchen.com/
"Demo mode"              launch Pixel Native Desktop Mode + open cortex
"Sleep mode"             toggle Do Not Disturb + Hue scene "night"
"Audio brain"            BT pair Pixel Buds + open Spotify "focus"
"Travel"                 enable Tailscale on metered + Hue scene "exit"
```

### Flipper Mobile app (BLE-paired)
- Browse SD card content from the Pixel
- Push captured `.ir`/`.sub`/`.nfc` files to the Pixel
- Run BadUSB payloads remotely from the phone
- Update SD content over BLE (slow but cable-free)

### Pixel Buds Pro 2 + Flipper combo
The Buds and Flipper are independent BT devices; they don't talk directly. But:
- Flipper can fire IR "audio receiver mute" while you're talking on Buds
- NFC tag triggers Pixel to play "Cortex demo intro music" through Buds while Flipper triggers the visual demo
- Useful at the hackathon: hands-free orchestration of multimedia

## Cross-system integration files

| File | Type | Purpose |
| --- | --- | --- |
| `/mnt/f/badusb/ascended-base/start-cortex-demo.txt` | DuckyScript | Soumit's "deploy demo" macro |
| `/mnt/f/badusb/ascended-base/health-check.txt` | DuckyScript | Public health probe via Windows Terminal |
| `/mnt/f/badusb/ascended-base/lock-pc.txt` | DuckyScript | Win+L emergency lock |
| `/mnt/f/infrared/Projectors/*.ir` | IR signature | Universal projector codes |
| `/mnt/f/subghz/panic.sub` (after capture) | Sub-GHz | Doorbell-button panic switch |
| `/mnt/f/nfc/donor-tags/*.nfc` (after writing) | NFC | Donor tracking stickers |

## What NOT to do

Be Googley about ethics. The Flipper makes some attacks trivial that aren't your devices:
- Don't capture neighbors' garage doors / car key fobs (federal felony)
- Don't clone someone else's apartment / office badge without written permission
- Don't run BadUSB on machines you don't own
- Don't sub-GHz brute force public infrastructure (utility meters, weather stations have FCC protection)

Stick to: your own devices, your own apartment, your own demo gear. The Flipper has a built-in disclaimer screen on first boot for a reason.

## Sources
- [Flipper Zero Docs](https://docs.flipper.net/)
- [Momentum Firmware](https://momentum-fw.dev)
- [NFC Tools (Android app — free version)](https://play.google.com/store/apps/details?id=com.wakdev.wdnfc)
- [Tagmo (Amiibo write tool, also great for NTAG management)](https://github.com/HiddenRamblings/TagMo)
