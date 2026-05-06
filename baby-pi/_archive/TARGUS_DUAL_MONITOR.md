# Targus dock + dual living-room TVs

The Pi 5 has **2× native micro-HDMI outputs**. The Targus dock provides USB,
ethernet, and (depending on model) extra HDMI via DisplayLink.

## Recommended wiring (no driver required)

```
┌──────────────────────────────┐
│ Pi 5                         │
│  ┌──────────┐  ┌──────────┐  │
│  │ HDMI-0   │  │ HDMI-1   │  │ ← Pi 5 micro-HDMI ports
│  └────┬─────┘  └────┬─────┘  │
│       │             │        │
│       │             │        │
│       │             │        │
│  ┌────┴─────┐  ┌────┴─────┐  │
│  │   USB-C  │  │  USB-C   │  │
│  │  (POWER) │  │  (DATA)  │  │
│  └──────────┘  └──────────┘  │
└──────┬─────────┬─────────────┘
       │         │
       │         └──→  Targus dock (USB-C data)
       │                  ├── Ethernet → router
       │                  ├── 2-3× USB-A → keyboard / mouse / future
       │                  └── (HDMI ports on dock = unused unless needed)
       │
       └──→  Pi 5 official 27 W PSU (USB-C power input)

Pi micro-HDMI 0 ──[micro-HDMI→HDMI cable]──→  TV (bed-facing)        — left  → /mercury
Pi micro-HDMI 1 ──[micro-HDMI→HDMI cable]──→  Hisense projector/TV   — right → /cortex
```

This avoids DisplayLink entirely. **Buy 2× quality micro-HDMI → HDMI cables**
(Cable Matters or Anker) and route them to your two displays. Either output
can drive 4K@60.

## If you must drive monitors through the Targus HDMI ports

If your Targus model uses **DisplayLink** (most universal docks do —
Synaptics chipsets DL-3000 / DL-5500 / DL-6950), the `kiosk-setup.sh` script
auto-detects it (`lsusb | grep -i DisplayLink`) and installs Synaptics' Linux
driver + the EVDI kernel module. Output names appear in xrandr as
`DVI-I-1-1`, `DVI-I-1-2` and the kiosk script lays them side-by-side
automatically.

DisplayLink quirks to know:
- 4K@60 is supported on DL-6950 only; older chips cap at 1080p@60 or 4K@30.
- USB-C on Pi 5 is **power-only** — the dock's data path goes through one of
  the two USB-A 3.0 ports, not the USB-C in. Use a USB-C-to-USB-A cable, or
  a different dock with USB-A out.
- After install, `sudo systemctl restart displaylink-driver`.

## Living-room layout (Soumit's room)

| Output | Cable | TV / projector | Dashboard URL |
|---|---|---|---|
| Pi micro-HDMI 0 | bed-facing 4K TV  | Mercury feed (`/mercury`) |
| Pi micro-HDMI 1 | sofa-facing Hisense projector (VIDAA) | Cortex feed (`/cortex`) |

Both TVs sit on the LAN and route DNS through the Pi (AdGuard); Hisense
domains (`hisense.com`, `vidaa.com`, `hismarttv.com`, `hicloud.com`,
`vidaa.network`) are pre-allowlisted in `AdGuardHome.yaml` so the projector
keeps streaming and firmware-updating normally.

## Power budget

- Pi 5 + active cooler: ~5 W idle, ~15 W under load
- 2× HDMI displays drawn over the Pi: another ~3 W (HDMI signaling itself)
- Targus dock USB-C draw: ~5 W when feeding USB devices
- **Total: ~25 W max**, well under the 27 W official PSU limit

If you see brown-out warnings (`Under-voltage detected`), you're either not
using the official Pi 5 PSU or the Targus is taking too much from one rail —
move power-hungry USB devices (keyboards/mice are fine; external SSDs
are not) off the Pi and onto the Targus's own power source.

## Testing

After kiosk-setup.sh runs and Pi reboots, you should see:
1. ASCENDED BASE splash on both monitors during boot
2. Two Chromium kiosks fullscreen — one per monitor
3. Dashboard live data from `seratonin:9090`

Force a reload remotely if needed:
```
ssh soumitlahiri@baby-pi 'pkill chromium; sleep 1; sudo systemctl restart getty@tty1'
```
