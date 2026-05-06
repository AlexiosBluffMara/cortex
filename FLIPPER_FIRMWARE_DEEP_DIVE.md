# Flipper Zero firmware — deep dive (2026-05)

Three CFW (custom firmware) options stand out + the official stock. Soumit's Flipper is unflashed; we're picking one before first power-on.

## TL;DR

**Use Momentum.** It's the most actively developed CFW with the deepest app catalog and best UX. Stock is too restricted; Unleashed is solid but slower-moving; RogueMaster is more raw and not for first-timers.

## Detailed comparison

| Capability | Stock (official) | Unleashed | RogueMaster | **Momentum** |
| --- | --- | --- | --- | --- |
| Sub-GHz region unlock | ❌ region-locked | ✅ | ✅ | ✅ |
| Built-in app catalog | small | medium | big (raw) | **biggest, curated** |
| Animations / wallpapers | bare | community | LOTS | LOTS, polished |
| Update cadence | quarterly | monthly | sporadic | **weekly+** |
| WebUSB updater | https://lab.flipper.net | n/a | n/a | https://momentum-fw.dev |
| OTA updates | yes | yes | yes | yes |
| Mass Storage mode (SD over USB) | no | no | no | **yes** |
| BadUSB editor on-device | basic | basic | yes | **yes, syntax-highlighted** |
| Sub-GHz protocol coverage | ~30 | ~80 | ~120 | **~140 + custom decoders** |
| NFC: Mifare DESFire support | partial | partial | yes | **yes + dev-mode AID emulation** |
| Bluetooth: BadBT | no | yes | yes | **yes + scripts** |
| Pixel/Android Flipper Mobile app compatibility | full | full | full | **full** |
| Risk of bricking | tiny | tiny | small | tiny |
| Recovery from brick | DFU mode (button combo) | same | same | same |

## Why Momentum specifically

1. **Mass Storage mode** lets you mount the Flipper's SD card from the Mac/PC over USB-C *without taking the SD card out*. Edit BadUSB scripts in VS Code, save, run on the Flipper instantly. Stock doesn't have this. Game-changer for iteration.
2. **Deep app catalog** — the JS Runner app, Geiger counter, Wifi Marauder integration, NRF24 sniffer, Magspoof emulation, all curated and signed.
3. **Settings UX** — searchable, navigable. Stock buries half the settings.
4. **Active dev** — the Momentum Discord ships fixes within days. Unleashed slowed down in mid-2025.
5. **Pixel Fold companion app works identically across all CFWs** — so picking Momentum doesn't lose any Pixel integration.

## Install path (Mac OR Windows)

### Web installer (easiest, Chrome required)

1. Power the Flipper on, plug into the computer via USB-C
2. Visit https://momentum-fw.dev
3. Click **Install Momentum** → grant WebUSB permission
4. Pick channel: **Release** (stable) — DON'T pick Dev unless you want surprises
5. Wait ~3-5 minutes; the Flipper reboots into Momentum

### qFlipper (also works)

```
# Windows (already installed):
"C:\Program Files\qFlipper\qFlipper.exe"

# Mac:
brew install --cask qflipper
```

In qFlipper UI: top-right firmware channel dropdown → "Custom" → paste:
`https://up.momentum-fw.dev/firmware/release/update.tgz`

Click Install.

### Recovery from brick

DFU mode: hold **LEFT + BACK** while plugging in USB-C. Web installer will detect DFU and reflash from scratch.

## What our SD card layout assumes

The SD card we're prepping (label `FLIPPER`, 60 GB exFAT) follows Momentum's expected directory tree. If you ever switch to Unleashed/RogueMaster:
- `apps/` directory may need re-naming (each CFW has slight differences)
- `badusb/`, `infrared/`, `subghz/`, `nfc/` are universal — no rename needed
- `apps_data/` is auto-managed; can safely delete on CFW swap

## Custom firmware features that matter to Soumit

**For the Cortex demo:**
- BadUSB scripts on Mass Storage mode = edit `start-cortex-demo.txt` in VS Code on Mac, change behavior, replug Flipper, demo updates instantly
- IR universal remote = control any projector/AC at any hackathon venue
- Flipper Mobile (BLE pair to Pixel Fold) = remote-trigger BadUSB from the phone — useful when the Flipper is plugged into Seratonin and you're at the projector

**For the Ascended Base apartment:**
- Sub-GHz capture of the cheap doorbell button → ESP32 listener on Baby Pi → Hue + Echo orchestration
- NFC tag writes (NTAG215) = donor-tracking stickers, NFC handoff to Pixel Desktop Mode
- iButton emulation = old-style apartment fobs (only if you actually have an iButton lock)

**For pen-testing the Cortex demo itself:**
- BadBT mode lets you replicate the Flipper's BLE scan UI as if you're a hacker — useful for talking points at hackathons
- Marauder via WiFi devboard ($30 add-on) = WiFi penetration testing demo on stage. Don't do this without a clear rules-of-engagement statement to the venue.

## What to install ON TOP of Momentum (after first boot)

Momentum's app catalog is browsable from the Flipper itself once you connect it to WiFi via the WiFi devboard, but you can also install via qFlipper or sideload `.fap` files into `apps/`.

| App | Why | Source |
| --- | --- | --- |
| **WiFi Marauder** | WiFi attack toolkit (needs WiFi devboard hardware) | github.com/0xchocolate/flipperzero-wifi-marauder |
| **NRF24 Mousejack** | Wireless keyboard sniffing (with NRF24 module) | catalog |
| **Magspoof** | Magnetic stripe emulation (with hardware coil) | catalog |
| **JS Runner** | JavaScript scripts on the Flipper | built-in |
| **U2F** | YubiKey-style 2nd factor (Flipper as security key) | catalog |
| **TOTP** | Authenticator app on Flipper | catalog |
| **Geiger counter** | Radiation sensing (with hardware addon) | catalog |
| **Picopass** | HID iCLASS reader/emulator | catalog |

Each is a `.fap` file that drops into `/apps/<category>/<app>.fap`.

## When to switch CFW

Don't, unless:
1. Momentum stops updating for >2 months (then switch to Unleashed)
2. You discover a feature you specifically need that's only in RogueMaster (rare)
3. You're doing security research and need to compare behaviors

CFW switching takes 5 minutes (web installer reflashes), no SD card changes needed, but your saved app data is wiped per-CFW.

## Sources
- https://momentum-fw.dev — Momentum (recommended)
- https://github.com/Next-Flip/Momentum-Firmware — source
- https://github.com/DarkFlippers/unleashed-firmware — Unleashed
- https://github.com/RogueMaster/flipperzero-firmware-wPlugins — RogueMaster
- https://docs.flipper.net/ — official docs
- https://lab.flipper.net — official web updater (stock only)
