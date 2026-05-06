# prep-flipper-sd-v4.ps1
# Targeted top-up for the Flipper SD with everything that was missing in v2/v3:
#   - NFC dictionaries (MIFARE Classic / Plus / Ultralight default + extended keys)
#   - iButton common keys + dumps
#   - Wallpapers (Momentum / Unleashed sets)
#   - Passport pictures
#   - Custom dolphin animations
#   - GPIO pinout cheatsheets
#   - ESP32 Marauder firmware bins (latest release) for the WiFi DevBoard
#   - Evil Portal HTML captive-portal templates
#   - ESP Flasher payload structure (esp32_marauder/ on root)
#
# Avoids the massive Sub-GHz / Music_Player re-copies that stalled v3.
# Idempotent: skips anything already populated.

$ErrorActionPreference = 'Stop'
$F = 'F:\'
$Tmp = 'C:\Users\soumi\AppData\Local\Temp\flipper-prep-v4'

if (-not (Test-Path $F)) { throw "F:\ not present" }
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

Start-Transcript -Path 'F:\.prep-v4.log' -Append | Out-Null
Write-Host "=== prep-flipper-sd-v4 starting $(Get-Date -Format o) ==="

function Test-DirHasFiles([string]$path, [int]$min = 1) {
    if (-not (Test-Path $path)) { return $false }
    $c = ([System.IO.Directory]::EnumerateFiles($path, '*', 'AllDirectories') | Measure-Object).Count
    return ($c -ge $min)
}

function Copy-Tree([string]$src, [string]$dst) {
    if (-not (Test-Path $src)) { Write-Host "skip: $src missing"; return }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    & robocopy $src $dst /E /NFL /NDL /NJH /NJS /NC /NS /NP /R:1 /W:1 /MT:8 | Out-Null
    Write-Host "copied: $src -> $dst"
}

# =====================================================================
# 1. Use the existing UberGuidoZ clone if present, else shallow-clone fresh
# =====================================================================
$Uber = "C:\Users\soumi\AppData\Local\Temp\flipper-prep-v3\UberGuidoZ-Flipper"
if (-not (Test-Path $Uber)) {
    $Uber = Join-Path $Tmp 'UberGuidoZ-Flipper'
    if (-not (Test-Path $Uber)) {
        Write-Host "cloning UberGuidoZ/Flipper (shallow + sparse) ..."
        git clone --depth 1 --filter=blob:none --sparse https://github.com/UberGuidoZ/Flipper.git $Uber
        Push-Location $Uber
        git sparse-checkout set iButton NFC Wallpaper Passport Animations GPIO BadUSB Music_Player
        Pop-Location
    }
}

# =====================================================================
# 2. NFC MIFARE dictionaries — THE most important missing piece
# =====================================================================
$NfcSrc = "C:\Users\soumi\AppData\Local\Temp\flipper-prep-v3\flipperzero-firmware-assets"
if (-not (Test-Path $NfcSrc)) {
    $NfcSrc = Join-Path $Tmp 'flipperzero-firmware'
    if (-not (Test-Path $NfcSrc)) {
        Write-Host "cloning flipperdevices/flipperzero-firmware (sparse) ..."
        git clone --depth 1 --filter=blob:none --sparse https://github.com/flipperdevices/flipperzero-firmware.git $NfcSrc
        Push-Location $NfcSrc
        git sparse-checkout set applications/main/nfc/resources lib/nfc assets
        Pop-Location
    }
}
$DictRoot = Get-ChildItem $NfcSrc -Directory -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'assets' -and $_.FullName -match 'nfc' } | Select-Object -First 1
if ($DictRoot) {
    New-Item -ItemType Directory -Force -Path 'F:\nfc\assets' | Out-Null
    Copy-Item "$($DictRoot.FullName)\*" -Destination 'F:\nfc\assets' -Recurse -Force
    Write-Host "NFC dicts written to F:\nfc\assets"
} else {
    Write-Host "WARN: NFC dict source not found"
}

# Add a MIFARE Classic extended key dictionary scraped from the wild
$mfc = 'F:\nfc\assets\mf_classic_dict_user.nfc'
if (-not (Test-Path $mfc)) {
    $extra = @(
        '# user-supplemented MIFARE Classic keys',
        'A0A1A2A3A4A5','B0B1B2B3B4B5','D3F7D3F7D3F7','000000000000','FFFFFFFFFFFF',
        '4D3A99C351DD','1A982C7E459A','AABBCCDDEEFF','714C5C886E97','587EE5F9350F',
        'A0478CC39091','533CB6C723F6','8FD0A4F256E9'
    ) -join "`n"
    Set-Content -Path $mfc -Value $extra -Encoding ASCII
    Write-Host "wrote user-supplemented MIFARE keys"
}

# =====================================================================
# 3. iButton, Wallpapers, Passport, Animations, GPIO
# =====================================================================
foreach ($pair in @(
    @{src='iButton';     dst='F:\ibutton'},
    @{src='Wallpaper';   dst='F:\wallpapers'},
    @{src='Passport';    dst='F:\passport'},
    @{src='Animations';  dst='F:\dolphin'},
    @{src='GPIO';        dst='F:\gpio_ref'}
)) {
    $s = Join-Path $Uber $pair.src
    if ((Test-DirHasFiles $pair.dst 5)) {
        Write-Host "skip $($pair.dst): already populated"
    } else {
        Copy-Tree $s $pair.dst
    }
}

# =====================================================================
# 4. ESP32 Marauder for the WiFi DevBoard
# =====================================================================
$MarauderDir = 'F:\esp32_marauder'
New-Item -ItemType Directory -Force -Path $MarauderDir | Out-Null

if (-not (Test-DirHasFiles $MarauderDir 3)) {
    Write-Host "fetching latest ESP32Marauder release artifacts ..."
    $api = 'https://api.github.com/repos/justcallmekoko/ESP32Marauder/releases/latest'
    try {
        $rel = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'flipper-prep' }
        $tag = $rel.tag_name
        Write-Host "Marauder latest tag: $tag"
        # Grab the most relevant Flipper-WiFi-Devboard variants
        $patterns = @('*flipper*', '*flipper_zero*', '*flipperzero*', '*marauder*flipper*')
        $assets = $rel.assets | Where-Object {
            $name = $_.name.ToLower()
            $name -match 'flipper' -or $name -match 'devboard'
        }
        if (-not $assets) { $assets = $rel.assets | Select-Object -First 6 }
        foreach ($a in $assets) {
            $out = Join-Path $MarauderDir $a.name
            if (-not (Test-Path $out)) {
                Write-Host "  download: $($a.name) ($([math]::Round($a.size/1MB,2)) MB)"
                Invoke-WebRequest -Uri $a.browser_download_url -OutFile $out -UseBasicParsing
            }
        }
        # Stamp the version on the SD for later reference
        Set-Content -Path (Join-Path $MarauderDir 'VERSION.txt') -Value @"
ESP32Marauder release: $tag
Pulled: $(Get-Date -Format o)
URL: $($rel.html_url)
"@ -Encoding ASCII
    } catch {
        Write-Host "WARN: Marauder fetch failed: $_"
    }
}

# =====================================================================
# 5. Evil Portal templates (captive portals for Marauder Evil Portal mode)
# =====================================================================
$EpDir = 'F:\apps_data\evil_portal'
$EpSrc = Join-Path $Tmp 'evil-portal'
if (-not (Test-Path $EpSrc)) {
    Write-Host "cloning bigbrodude6119/flipper-zero-evil-portal ..."
    git clone --depth 1 https://github.com/bigbrodude6119/flipper-zero-evil-portal.git $EpSrc 2>&1 | Out-Null
}
if (Test-Path $EpSrc) {
    New-Item -ItemType Directory -Force -Path $EpDir | Out-Null
    # Copy any HTML or .index.html templates and example portals
    $templates = Get-ChildItem $EpSrc -Recurse -Include *.html, *.index.html -ErrorAction SilentlyContinue
    foreach ($t in $templates) {
        $rel = $t.FullName.Substring($EpSrc.Length).TrimStart('\','/')
        $out = Join-Path $EpDir $rel
        New-Item -ItemType Directory -Force -Path (Split-Path $out -Parent) | Out-Null
        Copy-Item $t.FullName $out -Force
    }
    Write-Host "Evil Portal templates copied to $EpDir"
}

# =====================================================================
# 6. ESP Flasher app data (companion app stores .bin selections here)
# =====================================================================
$FlasherDir = 'F:\apps_data\esp_flasher'
New-Item -ItemType Directory -Force -Path $FlasherDir | Out-Null
# Symlink (or copy) Marauder bins into esp_flasher dir for one-tap flashing
foreach ($bin in Get-ChildItem $MarauderDir -Filter *.bin -ErrorAction SilentlyContinue) {
    $dest = Join-Path $FlasherDir $bin.Name
    if (-not (Test-Path $dest)) { Copy-Item $bin.FullName $dest -Force }
}

# =====================================================================
# 7. Final inventory
# =====================================================================
Write-Host ""
Write-Host "=== final inventory ==="
$dirs = @('badusb','infrared','subghz','nfc','rfid','ibutton','music_player',
          'dolphin','wallpapers','passport','gpio_ref','esp32_marauder',
          'apps_data','apps')
foreach ($d in $dirs) {
    $p = "F:\$d"
    if (Test-Path $p) {
        $c = ([System.IO.Directory]::EnumerateFiles($p, '*', 'AllDirectories') | Measure-Object).Count
        $s = ([System.IO.Directory]::EnumerateFiles($p, '*', 'AllDirectories') | ForEach-Object { (Get-Item $_).Length } | Measure-Object -Sum).Sum
        $sm = if ($s) { [math]::Round($s/1MB,1) } else { 0 }
        "{0,-16} {1,8} files {2,10} MB" -f $d, $c, $sm | Write-Host
    } else {
        "{0,-16}   missing" -f $d | Write-Host
    }
}
$vol = Get-Volume -DriveLetter F
$usedGB = [math]::Round(($vol.Size - $vol.SizeRemaining)/1GB, 2)
$freeGB = [math]::Round($vol.SizeRemaining/1GB, 2)
Write-Host ""
Write-Host ("F: used {0} GB | free {1} GB" -f $usedGB, $freeGB)
Write-Host "=== prep-flipper-sd-v4 done $(Get-Date -Format o) ==="

Stop-Transcript | Out-Null
