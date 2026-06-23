# Mac → Pixel — operate the Pixel from Seratonin

Goal: when you're at Seratonin, you can mirror, control, and install apps on the Pixel without ever touching the phone. Two tools, both Googley.

## The two tools

| Tool | What it's for | Latency | Audio? |
| --- | --- | --- | --- |
| **scrcpy** | "I just want to see and click the phone" | ~30 ms LAN | yes (v2.0+) |
| **Android Studio Device Mirroring** | "I'm developing an Android app" | ~50 ms | no |

Both share the same plumbing: `adb` (Android Debug Bridge). Set up adb once, both work.

---

## One-time setup — Mac side

```bash
# Install adb + scrcpy (Homebrew)
brew install android-platform-tools scrcpy

# Confirm
adb --version    # shows "Android Debug Bridge version 1.0.41" or higher
scrcpy --version # 3.0+ as of 2026
```

For the Studio path:
```bash
brew install --cask android-studio
# Open it once; it runs through "Android Studio Setup Wizard"
# Pick: Standard install, accept all SDK licenses
```

---

## One-time setup — Pixel side

1. **Settings → About phone → Build number** → tap 7 times → "You are now a developer!"
2. **Settings → System → Developer options**:
   - **USB debugging** = ON
   - **Wireless debugging** = ON (for cable-free pairing)
3. (Optional but Googley) **Settings → System → Developer options → Default USB configuration** = "File Transfer / Android Auto" (so adb works without unplug-replug)

---

## First connection (USB cable, 30 seconds)

1. Plug Pixel → Mac via USB-C cable
2. Pixel pops "Allow USB debugging from this computer?" → check **Always allow** → Allow
3. On Mac:
```bash
adb devices
# List of devices attached
# 38XXXXXXXXXXXX  device
scrcpy
```
A window opens with your Pixel's screen, full keyboard + mouse + clipboard pass-through. Drag a file from Finder onto the window → it installs (`.apk`) or copies (`.png`/etc).

---

## Wireless debug over Tailscale (the daily flow)

Once you've done the USB step once, switch to wireless:

```bash
# Get the Pixel's Tailscale IP
PIXEL_IP=100.102.198.9   # from `tailscale status | grep pixel`

# On the Pixel: Settings → Developer options → Wireless debugging → tap on
# "Pair device with pairing code" → note the IP:PORT and 6-digit code

# On Mac:
adb pair $PIXEL_IP:<pairing-port>    # paste 6-digit code when asked
adb connect $PIXEL_IP:5555            # 5555 is the standard adb-over-TCP port

# From now on, anywhere in the world:
scrcpy
```

The Pixel-Mac pairing survives reboots. Just `adb connect` again after Pixel reboots.

---

## scrcpy daily commands (cheat sheet)

```bash
# Mirror everything, full quality
scrcpy

# Mirror only the unfolded inner display (display id 1 on the Pixel Fold)
scrcpy --display-id=1 --max-size=2152

# Mirror the cover (folded) display
scrcpy --display-id=0 --max-size=1080

# Record a clean demo video (no audio, 1080p, MP4)
scrcpy --record=demo.mp4 --max-size=1920 --no-audio

# Audio + video, for streaming the Pixel's screen into OBS
scrcpy --audio-codec=aac --video-codec=h265 --max-fps=60

# Rotate to landscape (useful for the inner display)
scrcpy --display-orientation=90

# Pixel screen off but you keep control (security demo trick)
scrcpy --turn-screen-off --stay-awake

# OTG: forward Mac peripherals (keyboard) to the Pixel as if plugged in
scrcpy --otg
```

Keyboard shortcuts inside scrcpy: `Cmd+H` home, `Cmd+B` back, `Cmd+S` recents, `Cmd+R` rotate, `Cmd+0` reset zoom.

---

## Installing apps from the Mac (no Play Store roundtrip)

Three paths:

### A. Drag-and-drop into scrcpy
Drag any `.apk` from Finder onto the scrcpy window → installs immediately.

### B. adb install
```bash
adb install ~/Downloads/Termux-app_v0.118.3+github-debug_arm64-v8a.apk
adb install -g app.apk        # auto-grant all permissions
adb install -r app.apk        # replace existing
```

### C. Aurora Store (alternative Play Store, anonymous)
For Play Store apps without the Play Store account dance:
```bash
adb install ~/Downloads/AuroraStore.apk
# Then on the Pixel: open Aurora Store → Anonymous login → install anything
```

---

## Android Studio mirroring (when you're actually building Android)

1. Open Android Studio
2. **Tools → Device Manager** (or the Device dropdown in the toolbar)
3. **Pair Devices Using Wi-Fi** → scan the QR code on the Pixel (Settings → Developer options → Wireless debugging → Pair device with QR code)
4. **View → Tool Windows → Running Devices** → click the Pixel → mirror window opens

Studio mirroring is integrated with:
- **Logcat**: live logs from the Pixel
- **Layout Inspector**: pick any UI element on the Pixel, see its layout tree
- **Profiler**: CPU/memory/network of any running app
- **Database Inspector**: inspect SQLite DBs in app sandboxes

If you're not building an Android app, **scrcpy is faster and lighter**. Studio is overkill for "I just want to use the Pixel from my Mac."

---

## Useful: emulator for testing without burning the Pixel battery

If you want to test app installs / configurations without using the real Pixel:

```bash
# Inside Android Studio: Device Manager → "Create Device"
# Pick "Pixel 9 Pro Fold" (Studio has the exact profile)
# Pick API 35 (Android 15) or 36 (Android 16)
# Click Finish → wait for download → click ▶ to launch

# Or from CLI:
~/Library/Android/sdk/cmdline-tools/latest/bin/avdmanager list device | grep -i fold
~/Library/Android/sdk/cmdline-tools/latest/bin/avdmanager create avd \
  -n PixelFoldTest -k "system-images;android-35;google_apis_playstore;arm64-v8a" \
  -d pixel_fold
~/Library/Android/sdk/emulator/emulator @PixelFoldTest
```

The emulator gives you the **exact same APIs** as the real Pixel Fold — same Material 3 widgets, same Connected Display (you can simulate the dock), same Pixel Buds simulator. Useful when prototyping.

---

## Privacy / safety notes

- adb-over-TCP exposes your phone to anyone who can reach 5555 on the LAN. Tailscale-only ACL takes care of this (Tailscale ACLs default to "only my devices"). Don't `adb connect` to your Pixel from a public Wi-Fi without Tailscale on.
- scrcpy traffic is *unencrypted* on LAN (raw H.264 over TCP). Over Tailscale = encrypted via WireGuard.
- USB debugging stays on after you turn it on. Turn off when handing the phone to someone who isn't you.

---

## What this replaces

| Old workflow | New workflow |
| --- | --- |
| Switch focus to phone, type with thumbs | scrcpy keyboard from Mac |
| AirDrop equivalent (file transfer) | Drag onto scrcpy OR `adb push file.zip /sdcard/Download/` |
| Open Play Store on phone, search, install | `adb install app.apk` from Mac |
| Screen recording from the Pixel | `scrcpy --record=demo.mp4` |
| "How does this look on the Fold?" | Studio emulator with `pixel_fold` device profile |
