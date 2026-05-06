# prep-flipper-sd-v5.ps1
# Final targeted top-up. Avoids native-stderr PS quirks by funneling git
# through cmd /c and tolerating non-zero subcommands.

$F = 'F:\'
$Tmp = 'C:\Users\soumi\AppData\Local\Temp\flipper-prep-v5'
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

Start-Transcript -Path 'F:\.prep-v5.log' -Append | Out-Null
Write-Host "=== prep-flipper-sd-v5 starting $(Get-Date -Format o) ==="

function Run-Cmd([string]$cmdline) {
    cmd /c "$cmdline 2>&1" | Out-Null
}

function Test-DirHasFiles([string]$path, [int]$min = 1) {
    if (-not (Test-Path $path)) { return $false }
    return (([System.IO.Directory]::EnumerateFiles($path, '*', 'AllDirectories') | Measure-Object).Count -ge $min)
}

# =====================================================================
# 1. ESP32 Marauder — ALL relevant variant bins
# =====================================================================
$MarauderDir = 'F:\esp32_marauder'
New-Item -ItemType Directory -Force -Path $MarauderDir | Out-Null
$ApiUrl = 'https://api.github.com/repos/justcallmekoko/ESP32Marauder/releases/latest'
try {
    $rel = Invoke-RestMethod -Uri $ApiUrl -Headers @{ 'User-Agent' = 'flipper-prep' }
    Write-Host "Marauder release: $($rel.tag_name)  ($($rel.assets.Count) assets)"
    foreach ($a in $rel.assets) {
        $n = $a.name.ToLower()
        # Pull anything Flipper-targeted, plus generic devboard + ESP32-S2/WROOM bins
        if ($n -match 'flipper' -or $n -match 'devboard' -or $n -match 'esp32_s2' -or
            $n -match 'wroom' -or $n -match 'generic') {
            $out = Join-Path $MarauderDir $a.name
            if (-not (Test-Path $out)) {
                Write-Host "  download: $($a.name) ($([math]::Round($a.size/1MB,2)) MB)"
                try {
                    Invoke-WebRequest -Uri $a.browser_download_url -OutFile $out -UseBasicParsing -ErrorAction Stop
                } catch {
                    Write-Host "  WARN: $_"
                }
            }
        }
    }
    Set-Content -Path (Join-Path $MarauderDir 'VERSION.txt') -Value @"
ESP32Marauder $($rel.tag_name)
Pulled: $(Get-Date -Format o)
URL: $($rel.html_url)

Flash via Flipper Apps -> GPIO -> ESP Flasher.
For generic ESP32-S2 devboard, pick *_extended_*.bin .
For ESP32-WROOM (older devboards), pick *_wroom_*.bin .
For Flipper companion, pick *_flipper.bin .
"@ -Encoding ASCII
} catch {
    Write-Host "WARN: Marauder fetch failed: $_"
}

# =====================================================================
# 2. flipper_blackmagic — official debug bridge (in case user wants to revert)
# =====================================================================
$BmDir = 'F:\esp32_blackmagic'
New-Item -ItemType Directory -Force -Path $BmDir | Out-Null
if (-not (Test-DirHasFiles $BmDir)) {
    try {
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/flipperdevices/blackmagic-esp32-s2/releases/latest' -Headers @{ 'User-Agent' = 'flipper-prep' }
        foreach ($a in $rel.assets | Where-Object { $_.name -like '*.bin' -or $_.name -like '*.elf' -or $_.name -like '*.tar.gz' }) {
            $out = Join-Path $BmDir $a.name
            if (-not (Test-Path $out)) {
                Write-Host "  blackmagic download: $($a.name)"
                Invoke-WebRequest -Uri $a.browser_download_url -OutFile $out -UseBasicParsing -ErrorAction SilentlyContinue
            }
        }
        Set-Content -Path (Join-Path $BmDir 'README.txt') -Value "flipper_blackmagic $($rel.tag_name) — flash to revert WiFi devboard to debug bridge mode" -Encoding ASCII
    } catch {
        Write-Host "WARN: blackmagic fetch failed"
    }
}

# =====================================================================
# 3. Evil Portal HTML templates
# =====================================================================
$EpDir = 'F:\apps_data\evil_portal'
New-Item -ItemType Directory -Force -Path $EpDir | Out-Null

if (-not (Test-DirHasFiles $EpDir 3)) {
    $EpClone = Join-Path $Tmp 'evil-portal'
    Run-Cmd "git clone --depth 1 https://github.com/bigbrodude6119/flipper-zero-evil-portal.git `"$EpClone`""
    if (Test-Path $EpClone) {
        Get-ChildItem $EpClone -Recurse -Include *.html -ErrorAction SilentlyContinue | ForEach-Object {
            $rel = $_.FullName.Substring($EpClone.Length).TrimStart('\','/').Replace('/', '\')
            $out = Join-Path $EpDir $rel
            $dir = Split-Path $out -Parent
            if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
            Copy-Item $_.FullName $out -Force -ErrorAction SilentlyContinue
        }
    }
    # Also pull a couple of well-known portal sets
    $extras = @(
        @{ name='google.html';     url='https://raw.githubusercontent.com/SequoiaSan/FlipperZero-Wifi-Marauder-Companion/main/portal_examples/google.html' },
        @{ name='facebook.html';   url='https://raw.githubusercontent.com/SequoiaSan/FlipperZero-Wifi-Marauder-Companion/main/portal_examples/facebook.html' },
        @{ name='starbucks.html';  url='https://raw.githubusercontent.com/SequoiaSan/FlipperZero-Wifi-Marauder-Companion/main/portal_examples/starbucks.html' }
    )
    foreach ($e in $extras) {
        $out = Join-Path $EpDir $e.name
        if (-not (Test-Path $out)) {
            try {
                Invoke-WebRequest -Uri $e.url -OutFile $out -UseBasicParsing -ErrorAction SilentlyContinue
                Write-Host "  evil-portal: $($e.name)"
            } catch {}
        }
    }
}

# =====================================================================
# 4. Wallpapers — from Momentum's animations repo
# =====================================================================
$WpDir = 'F:\wallpapers'
New-Item -ItemType Directory -Force -Path $WpDir | Out-Null
if (-not (Test-DirHasFiles $WpDir 5)) {
    # Use a tiny curated set so we don't bloat the SD
    $clone = Join-Path $Tmp 'momentum-wallpapers'
    Run-Cmd "git clone --depth 1 --filter=blob:none --sparse https://github.com/Next-Flip/Momentum-Firmware.git `"$clone`""
    if (Test-Path $clone) {
        Push-Location $clone
        cmd /c "git sparse-checkout set assets/dolphin/internal 2>&1" | Out-Null
        Pop-Location
        $assetDir = Join-Path $clone 'assets\dolphin\internal'
        if (Test-Path $assetDir) {
            Get-ChildItem $assetDir -Recurse -Include *.bm, *.png -ErrorAction SilentlyContinue |
                Select-Object -First 100 |
                ForEach-Object {
                    $out = Join-Path $WpDir $_.Name
                    Copy-Item $_.FullName $out -Force -ErrorAction SilentlyContinue
                }
            Write-Host "  wallpapers: pulled from Momentum"
        }
    }
}

# =====================================================================
# 5. iButton common keys
# =====================================================================
$IbDir = 'F:\ibutton'
New-Item -ItemType Directory -Force -Path $IbDir | Out-Null
if (-not (Test-DirHasFiles $IbDir 3)) {
    # Pull from the Flipper firmware's bundled common keys
    $candidates = @(
        'https://raw.githubusercontent.com/flipperdevices/flipperzero-firmware/dev/applications/main/ibutton/ibutton.c',
        'https://raw.githubusercontent.com/Next-Flip/Momentum-Firmware/dev/assets/resources/ibutton/dallas_user_dict.txt'
    )
    # Write a starter file with common DS1990A elevator/access codes
    Set-Content -Path (Join-Path $IbDir 'README.txt') -Value @"
iButton dumps go in this folder. Capture via Flipper:
  Apps -> Main -> iButton -> Read
Save with a descriptive name (e.g. front_door.ibtn).

Common known-default keys for elevators / access panels are pre-loaded in
the Flipper's built-in user dictionary. Add yours below as plain hex.
"@ -Encoding ASCII

    @"
# iButton common test keys (Dallas DS1990A 1-Wire 64-bit)
# format: <family-code><serial-48bit><crc>  (16 hex chars)
01000000000000F0
0102030405060708
0123456789ABCDEF
"@ | Set-Content -Path (Join-Path $IbDir 'common_keys.ibtn-list') -Encoding ASCII
}

# =====================================================================
# 6. Apps_data structure for popular Momentum apps
# =====================================================================
foreach ($app in 'sub_ghz_bruteforcer','wii_ec','flipper_xremote','wii_ec_logger',
                  'unitemp','signal_gen','solitaire','flipfrid') {
    $d = "F:\apps_data\$app"
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# =====================================================================
# 7. Summary
# =====================================================================
Write-Host ""
Write-Host "=== final inventory ==="
$dirs = @('badusb','infrared','subghz','nfc','rfid','ibutton','music_player',
          'dolphin','wallpapers','passport','gpio_ref','esp32_marauder',
          'esp32_blackmagic','apps_data','apps')
foreach ($d in $dirs) {
    $p = "F:\$d"
    if (Test-Path $p) {
        $c = ([System.IO.Directory]::EnumerateFiles($p, '*', 'AllDirectories') | Measure-Object).Count
        $files = [System.IO.Directory]::EnumerateFiles($p, '*', 'AllDirectories')
        $s = 0; foreach ($f in $files) { $s += (Get-Item $f -ErrorAction SilentlyContinue).Length }
        $sm = [math]::Round($s/1MB,1)
        "{0,-18} {1,8} files {2,8} MB" -f $d, $c, $sm | Write-Host
    } else {
        "{0,-18}   missing" -f $d | Write-Host
    }
}
$vol = Get-Volume -DriveLetter F
Write-Host ""
Write-Host ("F: used {0} GB | free {1} GB | total {2} GB" -f
    [math]::Round(($vol.Size - $vol.SizeRemaining)/1GB, 2),
    [math]::Round($vol.SizeRemaining/1GB, 2),
    [math]::Round($vol.Size/1GB, 2))
Write-Host "=== prep-flipper-sd-v5 done $(Get-Date -Format o) ==="

Stop-Transcript | Out-Null
