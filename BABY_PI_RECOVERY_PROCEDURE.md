# Baby Pi headless recovery — saved procedure (resume when ready)

You don't have a monitor or micro-HDMI cable, so the Pi must come up purely via Tailscale + xrdp + Chrome Remote Desktop. This procedure does that.

## Prerequisites
- A microSD card (256GB SanDisk recommended; 64GB works)
- USB-C card reader (already in your setup)
- A Tailscale **one-shot reusable auth key** (1h expiry is fine):
  https://login.tailscale.com/admin/settings/keys → toggle ON: Reusable, Pre-authorized

## Steps

### 1. Plug the SD card into Seratonin
Tell Claude when it's mounted. Claude inspects the card to know its current state.

### 2. Paste the Tailscale auth key
Claude bakes it into `D:\cortex\scripts\baby-pi-boot\firstrun.sh` (already 95% complete; just needs the auth key on line 20).

### 3. Run Raspberry Pi Imager — exact settings

| Imager screen | Action |
| --- | --- |
| Choose device | Raspberry Pi 5 |
| Choose OS | **Raspberry Pi OS (64-bit) with desktop** (NOT Lite — we want VNC/xrdp out of the box) |
| Choose storage | the SD card |
| Click ⚙ EDIT SETTINGS | (critical) |
| **General tab** | Hostname = `baby-pi`; Username = `soumit`; Password = (strong); Wireless LAN = ON, SSID + password, Country = US; Locale America/Chicago; Keyboard us |
| **Services tab** | Enable SSH = ON → "Allow public-key authentication only" → paste:<br>`ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBHNtZs+gAvJ/etrsIhuMLis4ahAYxj2gNuEiVPqWyBn soumitlahiri@seratonin` |
| Save → Yes (apply) → Yes (overwrite) | Wait 5-10 min for write + verify |

### 4. Drop firstrun.sh onto the bootfs partition
After Imager finishes, the SD card auto-mounts as **two volumes**: `bootfs` (FAT32, ~256 MB) and `rootfs` (ext4, Windows can't read it — that's fine).

Claude copies `D:\cortex\scripts\baby-pi-boot\firstrun.sh` to the root of the **bootfs** volume.

### 5. Eject + plug into Pi + power on
1. Right-click bootfs in Explorer → Eject
2. Insert the SD into the Pi
3. **Plug ethernet** (cable to the bedroom router via switch, OR your phone's hotspot via USB) — wired is the reliability win
4. Plug USB-C power LAST
5. Wait ~3-4 minutes — first boot expands FS, runs firstrun.sh, joins Tailscale, reboots once

### 6. Claude takes over via Tailscale
The instant `baby-pi` shows up in `tailscale status`, Claude SSHes in and:
- Runs `apt full-upgrade` for kernel/firmware
- Verifies the wireless KB+mouse work
- Builds BitNet b1.58 (~10 min; queued in firstrun.sh)
- Brings up xrdp + VNC + Chrome Remote Desktop so you can SEE the Pi at https://remotedesktop.google.com/access from any browser
- Wires `baby-pi:8000` into the inference router as Tier 4 ternary
- Confirms the kiosk dashboard URL is preset (renders when monitor is later attached)

## How you SEE the Pi without a monitor

Three Googley options, all work over Tailscale:

| Path | URL / command | When to use |
| --- | --- | --- |
| **Chrome Remote Desktop** | https://remotedesktop.google.com/access | Daily — Google account auth, browser-only |
| **xrdp from Windows** | `mstsc /v:baby-pi:3389` | Native Windows RDP client, fastest |
| **Raspberry Pi Connect** | https://connect.raspberrypi.com | Backup; useful for sharing with a non-Tailscale guest |

CRD is the answer for your situation.

## Resume trigger

When you're ready: tell Claude **"Resume Baby Pi setup"** and it'll start at Step 1.
