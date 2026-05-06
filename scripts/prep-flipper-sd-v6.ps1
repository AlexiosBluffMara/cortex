# prep-flipper-sd-v6.ps1
# Plain-style: each step is independent, errors are tolerated.

$F = 'F:\'
$Tmp = 'C:\Users\soumi\AppData\Local\Temp\flipper-prep-v6'
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

Start-Transcript -Path 'F:\.prep-v6.log' -Append -Force | Out-Null
Write-Host "=== prep-flipper-sd-v6 starting $(Get-Date -Format o) ==="

# ---------- 1. Marauder bins ----------
$MarauderDir = 'F:\esp32_marauder'
New-Item -ItemType Directory -Force -Path $MarauderDir | Out-Null
Write-Host "--- Marauder ---"
$rel = $null
try { $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/justcallmekoko/ESP32Marauder/releases/latest' -Headers @{'User-Agent'='flipper-prep'} } catch { Write-Host "WARN Marauder API: $_" }
if ($rel) {
    Write-Host "tag: $($rel.tag_name)  assets: $($rel.assets.Count)"
    foreach ($a in $rel.assets) {
        $n = $a.name.ToLower()
        $want = ($n -match 'flipper' -or $n -match 'devboard' -or $n -match 'esp32_s2' -or $n -match 'wroom' -or $n -match 'generic')
        if (-not $want) { continue }
        $out = Join-Path $MarauderDir $a.name
        if (Test-Path $out) { continue }
        Write-Host ("  download {0} ({1:N1} MB)" -f $a.name, ($a.size/1MB))
        try { Invoke-WebRequest -Uri $a.browser_download_url -OutFile $out -UseBasicParsing } catch { Write-Host "  WARN: $_" }
    }
    Set-Content (Join-Path $MarauderDir 'VERSION.txt') "ESP32Marauder $($rel.tag_name)`nPulled $(Get-Date -Format o)" -Encoding ASCII
}

# ---------- 2. Blackmagic ----------
$BmDir = 'F:\esp32_blackmagic'
New-Item -ItemType Directory -Force -Path $BmDir | Out-Null
Write-Host "--- Blackmagic ---"
$bm = $null
try { $bm = Invoke-RestMethod -Uri 'https://api.github.com/repos/flipperdevices/blackmagic-esp32-s2/releases/latest' -Headers @{'User-Agent'='flipper-prep'} } catch { Write-Host "WARN BM API: $_" }
if ($bm) {
    foreach ($a in $bm.assets) {
        if ($a.name -notmatch '\.(bin|tgz|tar\.gz|zip)$') { continue }
        $out = Join-Path $BmDir $a.name
        if (Test-Path $out) { continue }
        Write-Host "  download $($a.name)"
        try { Invoke-WebRequest -Uri $a.browser_download_url -OutFile $out -UseBasicParsing } catch { Write-Host "  WARN: $_" }
    }
}

# ---------- 3. Evil Portal ----------
$EpDir = 'F:\apps_data\evil_portal'
New-Item -ItemType Directory -Force -Path $EpDir | Out-Null
Write-Host "--- Evil Portal ---"
$EpClone = Join-Path $Tmp 'evil-portal'
if (-not (Test-Path $EpClone)) {
    cmd /c "git clone --depth 1 https://github.com/bigbrodude6119/flipper-zero-evil-portal.git `"$EpClone`" 2>&1" | Out-Null
}
if (Test-Path $EpClone) {
    Get-ChildItem $EpClone -Recurse -Include *.html -ErrorAction SilentlyContinue | ForEach-Object {
        $r = $_.FullName.Substring($EpClone.Length).TrimStart('\','/').Replace('/','\')
        $out = Join-Path $EpDir $r
        $d = Split-Path $out -Parent
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
        Copy-Item $_.FullName $out -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  copied: $((Get-ChildItem $EpDir -Recurse -File | Measure-Object).Count) files"
}

# ---------- 4. Momentum wallpapers (sparse subset) ----------
$WpDir = 'F:\wallpapers'
New-Item -ItemType Directory -Force -Path $WpDir | Out-Null
Write-Host "--- Wallpapers ---"
$wpClone = Join-Path $Tmp 'momentum-fw'
if (-not (Test-Path $wpClone)) {
    cmd /c "git clone --depth 1 --filter=blob:none --sparse https://github.com/Next-Flip/Momentum-Firmware.git `"$wpClone`" 2>&1" | Out-Null
}
if (Test-Path $wpClone) {
    Push-Location $wpClone
    cmd /c "git sparse-checkout set assets/dolphin/internal 2>&1" | Out-Null
    Pop-Location
    $assetDir = Join-Path $wpClone 'assets\dolphin\internal'
    if (Test-Path $assetDir) {
        $cnt = 0
        foreach ($f in Get-ChildItem $assetDir -Recurse -Include *.bm, *.png -ErrorAction SilentlyContinue) {
            if ($cnt -ge 80) { break }
            $out = Join-Path $WpDir $f.Name
            if (-not (Test-Path $out)) {
                Copy-Item $f.FullName $out -Force -ErrorAction SilentlyContinue
                $cnt++
            }
        }
        Write-Host "  copied $cnt files"
    }
}

# ---------- 5. iButton starter ----------
$IbDir = 'F:\ibutton'
New-Item -ItemType Directory -Force -Path $IbDir | Out-Null
Write-Host "--- iButton ---"
if (-not (Test-Path (Join-Path $IbDir 'README.txt'))) {
    Set-Content -Path (Join-Path $IbDir 'README.txt') -Value "Capture iButton dumps via Apps -> Main -> iButton -> Read. Save as <name>.ibtn." -Encoding ASCII
    Set-Content -Path (Join-Path $IbDir 'common_keys.txt') -Value "# Common DS1990A test keys`n01000000000000F0`n0102030405060708`n0123456789ABCDEF`n" -Encoding ASCII
}

# ---------- 6. ESP Flasher staging dir + symlinked Marauder bins ----------
$FlasherDir = 'F:\apps_data\esp_flasher'
New-Item -ItemType Directory -Force -Path $FlasherDir | Out-Null
Write-Host "--- ESP Flasher staging ---"
foreach ($bin in Get-ChildItem $MarauderDir -Filter *.bin -ErrorAction SilentlyContinue) {
    $dest = Join-Path $FlasherDir $bin.Name
    if (-not (Test-Path $dest)) { Copy-Item $bin.FullName $dest -Force }
}

# ---------- 7. Common Momentum app data dirs ----------
foreach ($app in 'sub_ghz_bruteforcer','wii_ec','flipper_xremote','unitemp','signal_gen','flipfrid','picopass','gpio_reader','marauder','wifi_marauder') {
    $d = "F:\apps_data\$app"
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# ---------- 8. Inventory ----------
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
Write-Host "=== prep-flipper-sd-v6 done $(Get-Date -Format o) ==="

Stop-Transcript | Out-Null
