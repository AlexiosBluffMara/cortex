# Baby Pi — Home Assistant OS smart-home hub (headless)

Always-on, headless Raspberry Pi 5 (8 GB, active cooler) running **Home
Assistant OS** as the household's smart-home hub. No monitor needed. All
control via the HA web UI from any device on the LAN or via Tailscale.

## What it does

| Capability | How it's delivered |
|---|---|
| Central control of all Hue lights | HA's official Hue integration (auto-discovers bridge at 192.168.0.134) |
| Talk through any Google Home / Mini / Nest Hub | HA's Google Cast integration → `tts.google_translate_say` to any Cast target on the LAN |
| HA devices visible inside Google Home app | **Matter Server** add-on bridges HA → Google Home (+ Apple Home + SmartThings) — free, no Nabu Casa |
| Voice commands "Hey Google" → HA actions | Same Matter bridge: Google Home picks up HA-exposed entities natively |
| AdGuard Home with one-click pause | HA's official AdGuard add-on; exposes `switch.adguard_home_protection` — toggle from any HA dashboard or phone widget |
| Tailscale subnet router + exit node | Official HA Tailscale add-on |
| Automations | HA's automation engine (e.g. "lights amber when Mercury idle"; "pause AdGuard when Hisense projector is streaming") |
| Backup / snapshot | Native HA Backups (downloadable + scheduled) |

Idle: ~600 MB RAM, ~3 % CPU, ~6 W power.

## OS

**Home Assistant OS (HAOS) for Raspberry Pi 5**, official image. Replaces
Pi OS Lite entirely. Single web UI at `http://homeassistant.local:8123`.

Why HAOS:
- Single OS, single web UI, single update path
- AdGuard Home + Tailscale + Matter Server are all official add-ons (one-click)
- Native Hue + Google Cast integrations
- Backups / snapshots built-in
- Maintained by Home Assistant team

## Setup flow

```
1. Flash SD via D:\cortex\scripts\flash-baby-pi-haos.ps1 (Windows admin)
2. SD into Pi, plug ethernet + power, wait ~5-8 min for HAOS to provision
3. Browse to http://homeassistant.local:8123  (works via mDNS on LAN)
4. Create your admin account (username: soumit, password: yours)
5. From inside HA, do steps 6-10 below (all in the web UI)
```

### 6. Add the Hue integration

`Settings → Devices & Services → Add Integration → search "Philips Hue"`
HA discovers the bridge at 192.168.0.134 automatically. Click it → press
the button on the Hue bridge → all 12 lights appear as entities.

### 7. Add the Google Cast integration

`Settings → Devices & Services → Add Integration → search "Google Cast"`
Auto-discovers Bedroom Display (Nest Hub), Living Room speaker (Home Mini),
and Bob (SmartTV). All three become `media_player.*` entities and accept
`tts.*` calls.

### 8. Install the AdGuard Home add-on

`Settings → Add-ons → Add-on Store → search "AdGuard Home" → INSTALL`
Click **Show in sidebar**, then **Start**. Open the AdGuard UI from the
HA sidebar and configure filters + allowlist there. (Pre-configured YAML
example lives in `_archive/AdGuardHome.yaml` if you want to seed the same
allowlist; copy into the add-on's config dir on the Pi via SSH.)

The add-on automatically registers `switch.adguard_home_protection` and
sensors for query/block stats. Drop that switch on your default dashboard
for **one-click pause**.

### 9. Install the Tailscale add-on

`Settings → Add-ons → Add-on Store → search "Tailscale" → INSTALL → Start`
Web UI in sidebar → click "Authenticate" → sign in to Tailscale → Save.
Toggle ON: **Advertise routes** = `192.168.0.0/24`, **Advertise as exit
node** = yes. The Pi joins the mesh; subnet routing requires approval at
`https://login.tailscale.com/admin/machines`.

### 10. Install the Matter Server add-on

`Settings → Add-ons → Add-on Store → search "Matter Server" → INSTALL → Start`
Then `Settings → Devices & Services → Add Integration → "Matter (BETA)"`
to bind the server to HA. Now in the **Google Home app** on your phone,
add a Matter device → scan the QR code that HA generates → all your HA
entities (lights, switches, sensors) flow into Google Home and are
controllable by voice.

## One-click AdGuard pause

Three options, all wired through the AdGuard add-on:

1. **HA dashboard switch**: drop the `switch.adguard_home_protection`
   entity onto your Overview dashboard. Tap = pause/resume.
2. **HA phone app widget**: same switch as a home-screen widget.
3. **Voice via Google Home** (after Matter setup): "Hey Google, turn off
   AdGuard". Routes through Matter → HA → AdGuard add-on.

Plus an **automation idea**: "When `media_player.bob_smarttv` starts
playing, turn off AdGuard for 30 minutes" — covers projector breakage
proactively.

## Repo layout

```
D:\cortex\baby-pi\
├── BABY_PI.md             # this doc
└── _archive/              # Pi-OS-Lite-era stuff (firstrun.sh,
                            #   AdGuardHome.yaml, kiosk-setup.sh, etc.)
                            # Reference only; HAOS handles all of it now.
```

```
D:\cortex\scripts\
└── flash-baby-pi-haos.ps1 # the active flash script (HAOS image)
```

## Common operations

- **Reach HA UI**: `http://homeassistant.local:8123` (LAN) or `http://baby-pi:8123` (Tailscale)
- **SSH into HAOS host** (rare; HA does almost everything via UI):
  install the official **SSH & Web Terminal** add-on, then
  `ssh root@homeassistant -p 22222`
- **Check AdGuard query log**: HA sidebar → AdGuard Home → Query Log
- **Pause AdGuard for 30 minutes**: AdGuard UI top-right → "Disabled for"
  → 30 minutes. Or HA switch entity.
- **Add a Hue light**: HA auto-detects new lights from the bridge.
- **Add a Google Home Mini bought later**: HA's Cast integration
  re-scans nightly; or Settings → Devices & Services → Cast → reload.
- **Backup**: HA Settings → System → Backups → Create.
- **Update HAOS**: HA Settings → System → Updates (one-click).

## What this Pi is NOT

- Not an LLM / inference node (Big Apple's MLX + Seratonin's Ollama cover
  inference; orchestra `BACKENDS` does not include the Pi).
- Not a kiosk / dashboard display (we tried; pivoted away — headless is
  more useful).
- Not a metrics / Prometheus / Grafana box.

## Hardware reference

| | |
|---|---|
| SoC | Broadcom BCM2712 (4× Cortex-A76 @ 2.4 GHz) |
| RAM | 8 GB LPDDR4X |
| Network | Gigabit ethernet (preferred for hub reliability) |
| Cooling | active cooler (HDMI audio kept ON though no display attached) |
| Power | official Pi 5 27 W USB-C PSU |
