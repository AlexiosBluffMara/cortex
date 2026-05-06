# prep-flipper-sd-v3.ps1
# Top-up the Flipper SD on F: with everything it could possibly need:
#   - iButton dumps        (UberGuidoZ)
#   - RFID 125kHz dumps    (UberGuidoZ)
#   - NFC MIFARE dicts     (flipperdevices firmware assets)
#   - NFC extra dumps      (UberGuidoZ)
#   - Music_Player RTTTL   (UberGuidoZ)
#   - Animations           (UberGuidoZ)
#   - Wallpapers / Passport (UberGuidoZ)
#   - Wifi Devboard fw     (UberGuidoZ Wifi_DevBoard)
#   - GPIO pinout refs     (UberGuidoZ GPIO)
#   - BadUSB extras        (FalsePhilosopher/badusb)
#
# Idempotent: skips any directory that already has > $MinFiles files.
# Writes a transcript to F:\.prep-v3.log and prints final stats.

$ErrorActionPreference = 'Stop'
$F = 'F:\'
$Tmp = 'C:\Users\soumi\AppData\Local\Temp\flipper-prep-v3'
$MinFiles = 5

Start-Transcript -Path 'F:\.prep-v3.log' -Append | Out-Null
Write-Host "=== prep-flipper-sd-v3 starting $(Get-Date -Format o) ==="

if (-not (Test-Path $F)) { throw "F:\ not present (Flipper SD not mounted)" }
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

function Need-Dir([string]$path, [int]$min = $MinFiles) {
    if (-not (Test-Path $path)) { return $true }
    $cnt = (Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    return ($cnt -lt $min)
}

function Robocopy-Dir([string]$src, [string]$dst) {
    if (-not (Test-Path $src)) { Write-Host "skip: src missing $src"; return }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    & robocopy $src $dst /E /NFL /NDL /NJH /NJS /NC /NS /NP /R:1 /W:1 | Out-Null
    Write-Host ("copied: {0} -> {1}" -f $src, $dst)
}

# ---------- 1. UberGuidoZ/Flipper (shallow clone, full repo) ----------
$Uber = Join-Path $Tmp 'UberGuidoZ-Flipper'
if (-not (Test-Path $Uber)) {
    Write-Host "cloning UberGuidoZ/Flipper (shallow) ..."
    git clone --depth 1 --filter=blob:none --sparse https://github.com/UberGuidoZ/Flipper.git $Uber
    Push-Location $Uber
    git sparse-checkout set iButton RFID NFC Music_Player Animations Wallpaper Wifi_DevBoard GPIO Sub-GHz BadUSB Infrared Passport
    Pop-Location
} else {
    Write-Host "uberguidoz clone already present"
}

# ---------- 2. iButton ----------
if (Need-Dir 'F:\ibutton') {
    Robocopy-Dir (Join-Path $Uber 'iButton') 'F:\ibutton'
} else { Write-Host "ibutton already populated" }

# ---------- 3. RFID 125kHz ----------
if (Need-Dir 'F:\rfid') {
    Robocopy-Dir (Join-Path $Uber 'RFID') 'F:\rfid'
} else { Write-Host "rfid already populated" }

# ---------- 4. Music_Player (RTTTL) ----------
if (Need-Dir 'F:\music_player') {
    Robocopy-Dir (Join-Path $Uber 'Music_Player') 'F:\music_player'
} else { Write-Host "music_player already populated" }

# ---------- 5. Animations (custom dolphin) ----------
if (Need-Dir 'F:\dolphin') {
    Robocopy-Dir (Join-Path $Uber 'Animations') 'F:\dolphin'
} else { Write-Host "dolphin already populated" }

# ---------- 6. Wallpapers / Passport ----------
if (Need-Dir 'F:\wallpapers' 20) {
    Robocopy-Dir (Join-Path $Uber 'Wallpaper') 'F:\wallpapers'
}
if (Need-Dir 'F:\passport') {
    Robocopy-Dir (Join-Path $Uber 'Passport') 'F:\passport'
}

# ---------- 7. Wifi DevBoard firmware ----------
if (Need-Dir 'F:\wifi_devboard') {
    Robocopy-Dir (Join-Path $Uber 'Wifi_DevBoard') 'F:\wifi_devboard'
}

# ---------- 8. GPIO pinout reference ----------
if (Need-Dir 'F:\gpio_ref' 1) {
    Robocopy-Dir (Join-Path $Uber 'GPIO') 'F:\gpio_ref'
}

# ---------- 9. NFC dictionaries (MIFARE keys) ----------
$NfcAssets = Join-Path $Tmp 'flipperzero-firmware-assets'
if (-not (Test-Path $NfcAssets)) {
    Write-Host "cloning flipperdevices/flipperzero-firmware (shallow, sparse) ..."
    git clone --depth 1 --filter=blob:none --sparse https://github.com/flipperdevices/flipperzero-firmware.git $NfcAssets
    Push-Location $NfcAssets
    git sparse-checkout set applications/main/nfc/resources lib/nfc
    Pop-Location
}
$DictSrc = Join-Path $NfcAssets 'applications\main\nfc\resources\nfc\assets'
if (Test-Path $DictSrc) {
    New-Item -ItemType Directory -Force -Path 'F:\nfc\assets' | Out-Null
    Get-ChildItem $DictSrc -File | ForEach-Object {
        Copy-Item $_.FullName -Destination 'F:\nfc\assets\' -Force
    }
    Write-Host "nfc dicts copied"
} else {
    Write-Host "WARN: nfc dict source not found at $DictSrc"
}

# Also copy any UberGuidoZ NFC content not already present
if ((Get-ChildItem F:\nfc -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count -lt 50) {
    Robocopy-Dir (Join-Path $Uber 'NFC') 'F:\nfc'
}

# ---------- 10. Extra Sub-GHz from UberGuidoZ (top-up only if low) ----------
$subCnt = (Get-ChildItem F:\subghz -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
if ($subCnt -lt 5000) {
    Robocopy-Dir (Join-Path $Uber 'Sub-GHz') 'F:\subghz'
} else {
    Write-Host "subghz already comprehensive ($subCnt files), skipping topup"
}

# ---------- 11. Extra BadUSB: FalsePhilosopher ----------
$FP = Join-Path $Tmp 'FalsePhilosopher-badusb'
if (-not (Test-Path $FP)) {
    Write-Host "cloning FalsePhilosopher/badusb ..."
    git clone --depth 1 https://github.com/FalsePhilosopher/badusb.git $FP 2>&1 | Out-Null
}
if (Test-Path $FP) {
    New-Item -ItemType Directory -Force -Path 'F:\badusb\falsephilosopher' | Out-Null
    Get-ChildItem $FP -Recurse -Filter *.txt -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName -Destination 'F:\badusb\falsephilosopher\' -Force -ErrorAction SilentlyContinue
    }
    Write-Host "FalsePhilosopher BadUSB payloads merged"
}

# ---------- 12. Extra Infrared (top-up if low) ----------
$irCnt = (Get-ChildItem F:\infrared -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
if ($irCnt -lt 1500) {
    Robocopy-Dir (Join-Path $Uber 'Infrared') 'F:\infrared'
} else {
    Write-Host "infrared already comprehensive ($irCnt files), skipping topup"
}

# ---------- Final stats ----------
Write-Host ""
Write-Host "=== final inventory ==="
$dirs = @('badusb','infrared','subghz','nfc','rfid','ibutton','music_player','dolphin','wallpapers','passport','wifi_devboard','gpio_ref','apps','apps_data')
foreach ($d in $dirs) {
    $p = "F:\$d"
    if (Test-Path $p) {
        $c = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
        $s = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        $sm = if ($s) { [math]::Round($s/1MB,1) } else { 0 }
        "{0,-16} {1,8} files {2,10} MB" -f $d, $c, $sm | Write-Host
    }
}
$vol = Get-Volume -DriveLetter F
$usedGB = [math]::Round(($vol.Size - $vol.SizeRemaining)/1GB, 2)
$freeGB = [math]::Round($vol.SizeRemaining/1GB, 2)
$totGB  = [math]::Round($vol.Size/1GB, 2)
Write-Host ""
Write-Host ("F: total {0} GB | used {1} GB | free {2} GB" -f $totGB, $usedGB, $freeGB)
Write-Host "=== prep-flipper-sd-v3 done $(Get-Date -Format o) ==="

Stop-Transcript | Out-Null
