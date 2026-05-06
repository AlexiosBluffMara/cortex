# prep-flipper-sd-v7.ps1
# Final small additions: ESP-Flasher boot/partition bins + extra Marauder variants
# + confirm staging structure for one-tap Flipper ESP Flasher.

$F = 'F:\'
$Tmp = 'C:\Users\soumi\AppData\Local\Temp\flipper-prep-v7'
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

Start-Transcript -Path 'F:\.prep-v7.log' -Append -Force | Out-Null
Write-Host "=== prep-flipper-sd-v7 starting $(Get-Date -Format o) ==="

$MarauderDir = 'F:\esp32_marauder'
$FlasherDir = 'F:\apps_data\esp_flasher'
New-Item -ItemType Directory -Force -Path $MarauderDir | Out-Null
New-Item -ItemType Directory -Force -Path $FlasherDir | Out-Null

# ----------------------------------------------------------------------
# 1. Pull *all* Marauder release bins so the user has options for any
#    devboard they end up plugging in
# ----------------------------------------------------------------------
Write-Host "--- Marauder full release ---"
$rel = $null
try { $rel = Invoke-RestMethod 'https://api.github.com/repos/justcallmekoko/ESP32Marauder/releases/latest' -Headers @{'User-Agent'='x'} } catch {}
if ($rel) {
    Write-Host "tag: $($rel.tag_name)  assets: $($rel.assets.Count)"
    foreach ($a in $rel.assets) {
        if ($a.name -notlike '*.bin') { continue }
        $out = Join-Path $MarauderDir $a.name
        if (Test-Path $out) { continue }
        Write-Host ("  download {0} ({1:N2} MB)" -f $a.name, ($a.size/1MB))
        try { Invoke-WebRequest $a.browser_download_url -OutFile $out -UseBasicParsing }
        catch { Write-Host "  WARN: $_" }
    }
}

# ----------------------------------------------------------------------
# 2. Pull boot.bin + partitions.bin from the Marauder bootloader repo
#    (offline-ESP-Flasher flow needs both)
# ----------------------------------------------------------------------
Write-Host "--- ESP-S2 bootloader + partitions ---"
$bootSources = @(
    @{ name='boot.bin';        url='https://github.com/justcallmekoko/ESP32Marauder/raw/master/esp32_marauder/bootloader.bin' },
    @{ name='partitions.bin';  url='https://github.com/justcallmekoko/ESP32Marauder/raw/master/esp32_marauder/partitions.bin' }
)
foreach ($b in $bootSources) {
    $out = Join-Path $MarauderDir $b.name
    if (-not (Test-Path $out)) {
        try {
            Invoke-WebRequest $b.url -OutFile $out -UseBasicParsing
            Write-Host "  $($b.name) downloaded"
        } catch {
            Write-Host "  WARN: $($b.name) - $_"
        }
    }
}

# ----------------------------------------------------------------------
# 3. Re-stage ESP Flasher dir so all .bin files are easy to pick
# ----------------------------------------------------------------------
Write-Host "--- ESP Flasher staging ---"
$copied = 0
foreach ($bin in Get-ChildItem $MarauderDir -Filter *.bin) {
    $dest = Join-Path $FlasherDir $bin.Name
    if (-not (Test-Path $dest)) { Copy-Item $bin.FullName $dest -Force; $copied++ }
}
$totalBins = (Get-ChildItem $FlasherDir -Filter *.bin | Measure-Object).Count
Write-Host "  staged $copied new bins (total: $totalBins)"

# Drop a flash recipe README at the staging dir
$recipe = @"
# ESP Flasher one-tap recipe (Flipper Apps -> GPIO -> ESP Flasher)
#
# Hardware: Flipper Zero WiFi DevBoard (ESP32-S2)
#
# Three-file flash:
#   bootloader  -> boot.bin
#   partitions  -> partitions.bin
#   firmware    -> esp32_marauder_v1_12_0_*_flipper.bin
#
# Hold BOOT on the devboard before tapping FLASH. Release once progress starts.
#
# OTHER BOARDS (if you ever attach them):
#   Marauder Dev Board Pro  -> *_marauder_dev_board_pro.bin
#   Marauder v7             -> *_marauder_v7.bin
#   Marauder v8             -> *_v8.bin
#   M5StickC Plus           -> *_m5stickc_plus.bin
#   ESP32-S3 multiboard     -> *_multiboardS3.bin
#
# To revert the WiFi DevBoard to debug-bridge mode, flash the bins from
# F:\esp32_blackmagic instead.
"@
Set-Content -Path (Join-Path $FlasherDir 'FLASH_RECIPE.txt') -Value $recipe -Encoding ASCII

# ----------------------------------------------------------------------
# 4. Final inventory
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "=== final inventory ==="
$dirs = 'badusb','infrared','subghz','nfc','rfid','ibutton','music_player','dolphin','wallpapers','passport','gpio_ref','esp32_marauder','esp32_blackmagic','apps_data','apps'
foreach ($d in $dirs) {
    $p = "F:\$d"
    if (Test-Path $p) {
        $c = ([System.IO.Directory]::EnumerateFiles($p, '*', 'AllDirectories') | Measure-Object).Count
        $sum = 0
        foreach ($f in [System.IO.Directory]::EnumerateFiles($p, '*', 'AllDirectories')) {
            try { $sum += (Get-Item $f -ErrorAction SilentlyContinue).Length } catch {}
        }
        $sm = [math]::Round($sum/1MB,1)
        "{0,-18} {1,8} files {2,8} MB" -f $d, $c, $sm | Write-Host
    } else {
        "{0,-18}   missing" -f $d | Write-Host
    }
}
$v = Get-Volume -DriveLetter F
Write-Host ""
Write-Host ("F: used {0:N2} GB | free {1:N2} GB | total {2:N2} GB" -f (($v.Size-$v.SizeRemaining)/1GB), ($v.SizeRemaining/1GB), ($v.Size/1GB))
Write-Host "=== prep-flipper-sd-v7 done $(Get-Date -Format o) ==="
Stop-Transcript | Out-Null
