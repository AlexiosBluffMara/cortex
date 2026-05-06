# Flipper Zero — full loadout (firmware + SD + WiFi DevBoard)

Soumit's Flipper, fully tricked out. End state:

- **Momentum** custom firmware on the Flipper itself
- **ESP32 Marauder** on the WiFi DevBoard
- **23,800+** assets on a 64 GB exFAT SD: Sub-GHz / IR / BadUSB / NFC + dictionaries / iButton / Music / Wallpapers / Animations / Passport / Evil Portal templates / Marauder bins
- **All Flipper apps** the modern firmwares ship: ESP Flasher, Marauder, Evil Portal, Bad KB, NFC Magic, Sub-GHz Bruteforcer, IR Universal Remote, GPS NMEA, Wii EC, Picopass, Apple BLE Spam, etc.
- **Pixel Fold ↔ Flipper** companion paired over BLE for log streaming + remote control

---

## 1. Custom firmware on the Flipper

### Pick: **Momentum** (recommended)

Why Momentum over the alternatives:
| Firmware | Status | Pros | Cons |
|---|---|---|---|
| **Momentum** | Active, 2025-2026 | Modern UI, biggest app catalog, sub-GHz region unlocked, NFC magic, BLE spam, Picopass, fast updates | None for our use |
| Unleashed | Active | Stable, conservative | Smaller app catalog |
| RogueMaster | Active | Aggressive feature set | Sometimes unstable |
| Xtreme | Stale | — | Abandoned |
| Stock | Active | Official | Locked sub-GHz region, no Marauder integration |

### Flash steps

1. Plug Flipper into the desktop with USB-C.
2. Open Chrome → **https://momentum-fw.dev** → click **Web Updater**.
3. Approve the WebUSB prompt → choose Release channel → Install.
4. Wait ~2 minutes. Flipper reboots into Momentum.
5. Eject Flipper.

### First-boot Momentum config

Settings → System → Region: **WORLD**
Settings → System → Sub-GHz Frequency Analyzer: **on**
Settings → System → Sleep Method: **Display Off** (saves battery, instant resume)
Settings → Protocols → NFC → MIFARE Classic → enable user dictionary

---

## 2. SD card

The 64 GB exFAT card is already populated by `prep-flipper-sd-v4.ps1`:

- `badusb/` — 605 DuckyScripts (hak5 + ascended-base + UberGuidoZ + falsephilosopher)
- `infrared/` — 907 device IR codes (Lucaslhm/Flipper-IRDB full)
- `subghz/` — 11,585 captures (UberGuidoZ comprehensive set)
- `music_player/` — 10,688 RTTTL ringtones
- `nfc/assets/` — **MIFARE Classic + Plus + Ultralight default + extended key dictionaries**
- `nfc/` — Amiibo + HID iClass + Documentation + custom ascended-base
- `ibutton/` — common iButton key dumps
- `dolphin/` — custom dolphin animations
- `wallpapers/` — Momentum + Unleashed wallpaper sets
- `passport/` — custom passport pictures
- `gpio_ref/` — pinout cheatsheets
- `esp32_marauder/` — **latest Marauder release .bin files for the WiFi DevBoard**
- `apps_data/evil_portal/` — captive portal HTML templates
- `apps_data/esp_flasher/` — staging dir for ESP Flasher app

After Momentum is flashed, eject the SD reader (F:), pop the card into the Flipper, power on. Apps tab now shows everything Momentum bundles plus app-data we pre-staged.

---

## 3. WiFi DevBoard — flash ESP32 Marauder

The Flipper Zero WiFi DevBoard is an ESP32-S2 (or ESP32-WROOM, depending on revision) breakout that plugs into the Flipper's GPIO header. Stock firmware is the official `flipper-blackmagic` debug bridge. Replace it with **ESP32 Marauder** for offensive WiFi/BLE.

### Capabilities Marauder unlocks

- **WiFi attacks**: deauth (single + flood), beacon spam, beacon scan, evil portal
- **WiFi recon**: AP scan, station scan, packet sniffer, probe sniffer, channel-hop sniff, EAPOL capture (PMKID + handshake)
- **Bluetooth**: BT scan + airtag spoof + skimmer detect
- **Wardriving**: GPS-tagged AP captures (via the GPS NMEA app on Flipper)
- **Evil Portal**: serve captive-portal HTML from `apps_data/evil_portal/` + capture credentials
- **Random**: signal jammer (where legal), wireless display brick

### Flash via the Flipper itself (zero extra tools)

1. SD has the Marauder bins at `esp32_marauder/marauder_v##_*.bin` (already staged by v4 prep).
2. Boot Flipper → Apps → GPIO → **ESP Flasher**. (If ESP Flasher isn't there, install it from the Apps catalog under "GPIO".)
3. Plug the WiFi DevBoard into the GPIO header.
4. ESP Flasher → **Flash ESP32-S2 (Marauder)** → pick:
   - `boot.bin`
   - `partitions.bin`
   - `marauder_v##_extended_<chipset>.bin`
5. Hold BOOT on the devboard, press FLASH on Flipper, release BOOT after the progress bar starts.
6. Reboot devboard. Apps → GPIO → **WiFi Marauder** to drive it from the Flipper screen.

### Or flash via desktop esptool (faster, more verbose)

```bash
# in WSL2
pip install esptool
# devboard COM port (Windows) → e.g. COM5 → /dev/ttyS5 in WSL2 OR plug into WSL via usbipd-win

# erase
esptool.py --chip esp32s2 --port /dev/ttyS5 erase_flash

# flash (paths point at the SD copies)
cd /mnt/f/esp32_marauder
esptool.py --chip esp32s2 --port /dev/ttyS5 --baud 921600 write_flash \
    0x1000 boot.bin \
    0x8000 partitions.bin \
    0x10000 marauder_v*_extended*.bin
```

### Marauder app on Flipper

After flashing, on the Flipper:
- Apps → GPIO → **WiFi Marauder** — terminal-style menu over UART
- Apps → GPIO → **Evil Portal** — load HTML from `apps_data/evil_portal/`, victims hit the captive page, creds stream back to Flipper
- Apps → GPIO → **Wardriving** — combined with the GPS NMEA app

---

## 4. Pixel Fold companion

Install **Flipper Mobile** from Play Store on the Pixel Fold. BLE-pair to the Flipper:

1. Flipper → Settings → Bluetooth → **Pair**
2. Pixel Fold Flipper Mobile → tap the device that appears
3. Approve PIN on both screens

What you can do from the Pixel:
- **Live screen mirror** — see the Flipper screen on the inner 8" display
- **Remote control** — drive the Flipper without taking it out of your bag
- **Log streaming** — Marauder packet logs / NFC dumps stream to the phone
- **File transfer** — push payloads from phone → Flipper SD over BLE
- **Cloud sync** — optional, can be disabled for paranoia

---

## 5. Tabletop setup at the desk

Daily-driver layout:
- Flipper Zero on a magnetic mount at desk-edge
- WiFi DevBoard semi-permanently attached (always on, drains ~30 mA from the Flipper)
- USB-C cable from Flipper → Seratonin for: power top-up, qFlipper for backups, ESP Flasher updates
- Pixel Fold sits on its dock with Flipper Mobile open as the "second monitor"
- SD card slot exposed on the side — swap if needed

---

## 6. Backup / restore

After everything is loaded:

```bash
# back up the entire SD to D:\cortex\backups\
robocopy F:\ D:\cortex\backups\flipper-sd-$(date +%Y%m%d) /E /COPY:DAT /R:1 /W:1
```

The Flipper's internal NVS (settings, BLE pairings, dolphin level, U2F seed) is
backed up via **qFlipper → Backup**. Store the resulting `.tgz` in the same
backups folder.

---

## Status checklist

- [ ] Momentum flashed via momentum-fw.dev
- [ ] Region set to WORLD
- [ ] SD ejected from F:, inserted into Flipper
- [ ] Apps catalog updated (Momentum auto-fetches)
- [ ] WiFi DevBoard plugged in
- [ ] ESP Flasher used to flash latest Marauder
- [ ] WiFi Marauder app launches, sees APs
- [ ] Evil Portal loads default template
- [ ] Pixel Fold paired via Flipper Mobile
- [ ] qFlipper backup taken to D:\cortex\backups\
