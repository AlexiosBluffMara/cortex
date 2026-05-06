# flash-baby-pi-haos.ps1
# Flash Home Assistant OS to the SD on Disk 4. Replaces flash-baby-pi-v2.ps1.
# After flash + first boot, all customization happens in the Home Assistant
# web UI - no firstrun.sh, no AdGuardHome.yaml, no kiosk script.

#Requires -RunAsAdministrator

$ErrorActionPreference = 'Continue'
$Marker = 'C:\Users\soumi\AppData\Local\Temp\baby-pi-flash-marker.txt'
$WorkDir = 'C:\Users\soumi\AppData\Local\Temp\baby-pi-flash-haos'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# Drop marker IMMEDIATELY so we can detect this script ever launched.
"flash-baby-pi-haos launched at $(Get-Date -Format o)" | Out-File -FilePath $Marker -Encoding ASCII -Force
Write-Host "MARKER written: $Marker"

Start-Transcript -Path "$WorkDir\flash.log" -Append -Force | Out-Null
Write-Host "=== flash-baby-pi-haos $(Get-Date -Format o) ==="

$DiskNumber = 3
$ImagerExe  = 'C:\Program Files\Raspberry Pi Ltd\Imager\rpi-imager.exe'

# 0. Resolve current HAOS Pi 5 asset URL from the GitHub Releases API.
#    The asset name embeds the version (e.g. haos_rpi5-64-17.2.img.xz) so a
#    static "latest/download" URL 404s. Query the API for the current asset.
Write-Host "--- resolving latest HAOS Pi 5 asset ---"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$apiUrl = 'https://api.github.com/repos/home-assistant/operating-system/releases/latest'
$rel = $null
try {
    $rel = Invoke-RestMethod -Uri $apiUrl -UseBasicParsing -Headers @{ 'User-Agent'='cortex-flash' }
} catch {
    Write-Host "ERROR: GitHub API fetch failed: $_"
    Stop-Transcript | Out-Null
    return
}
$asset = $rel.assets | Where-Object { $_.name -match '^haos_rpi5-64-.*\.img\.xz$' } | Select-Object -First 1
if (-not $asset) {
    Write-Host "ERROR: no haos_rpi5-64-*.img.xz asset in release $($rel.tag_name)."
    Stop-Transcript | Out-Null
    return
}
$ImageUrl = $asset.browser_download_url
$ImgArchive = Join-Path $WorkDir $asset.name
$ImgFile    = Join-Path $WorkDir ($asset.name -replace '\.xz$', '')
$expectedBytes = $asset.size
Write-Host "HAOS release: $($rel.tag_name)"
Write-Host "Asset:        $($asset.name) ($([math]::Round($expectedBytes/1MB,1)) MB)"
Write-Host "URL:          $ImageUrl"

# 1. Verify disk identity
$disk = Get-Disk -Number $DiskNumber
if (-not $disk) {
    Write-Host "ERROR: Disk $DiskNumber not present. Plug in the SD reader and retry."
    Stop-Transcript | Out-Null
    return
}
Write-Host ("Disk {0}: {1} | {2} GB | {3}" -f $DiskNumber, $disk.FriendlyName, [math]::Round($disk.Size/1GB,1), $disk.BusType)
if ($disk.Size -gt 300GB -or $disk.BusType -notin @('USB','SD')) {
    Write-Host "REFUSING: disk doesn't look like a removable SD card (>300 GB or not USB/SD)."
    Stop-Transcript | Out-Null
    return
}

# 2. Wipe via diskpart
Write-Host "--- wiping disk $DiskNumber ---"
"select disk $DiskNumber`r`nclean`r`nrescan" | Out-File -FilePath "$WorkDir\wipe.dp" -Encoding ASCII -Force
& diskpart /s "$WorkDir\wipe.dp" 2>&1 | ForEach-Object { Write-Host "  diskpart: $_" }

# 3. Download HAOS image (skip if already present and complete)
$needsDownload = $true
if (Test-Path $ImgArchive) {
    $sz = (Get-Item $ImgArchive).Length
    if ($sz -ge $expectedBytes) {
        Write-Host "archive cached: $([math]::Round($sz/1MB,1)) MB (matches expected)"
        $needsDownload = $false
    } else {
        Write-Host ("partial archive ({0} MB / {1} MB) - re-downloading" -f [math]::Round($sz/1MB,1), [math]::Round($expectedBytes/1MB,1))
        Remove-Item $ImgArchive -Force
    }
}
if ($needsDownload) {
    Write-Host "--- downloading ---"
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $ImageUrl -OutFile $ImgArchive -UseBasicParsing
    } catch {
        Write-Host "ERROR: download failed: $_"
        Stop-Transcript | Out-Null
        return
    }
    $ProgressPreference = 'Continue'
    $sz = (Get-Item $ImgArchive).Length
    Write-Host "archive: $([math]::Round($sz/1MB,1)) MB"
    if ($sz -lt $expectedBytes - 1024) {
        Write-Host ("ERROR: download truncated. got={0} expected={1}" -f $sz, $expectedBytes)
        Stop-Transcript | Out-Null
        return
    }
}

# 4. Decompress via WSL xz. Build paths from $ImgArchive / $ImgFile so we
#    follow the asset name change (was hard-coded before).
if (-not (Test-Path $ImgFile)) {
    Write-Host "--- decompressing via WSL xz ---"
    $wslArchive = ($ImgArchive -replace '\\','/' -replace '^C:','/mnt/c')
    $wslImg     = ($ImgFile     -replace '\\','/' -replace '^C:','/mnt/c')
    $bashCmd = "xz -dkfc '$wslArchive' > '$wslImg'"
    & wsl -d Ubuntu bash -c $bashCmd
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $ImgFile)) {
        Write-Host "ERROR: xz decompression failed (exit $LASTEXITCODE)"
        Stop-Transcript | Out-Null
        return
    }
}
Write-Host "img: $([math]::Round((Get-Item $ImgFile).Length / 1GB, 2)) GB"

# 5. Flash via rpi-imager. ArgumentList must be a string[] so embedded spaces
#    in $ImgFile don't break.
Write-Host "--- flashing via rpi-imager ---"
if (-not (Test-Path $ImagerExe)) {
    Write-Host "ERROR: $ImagerExe missing. Reinstall Raspberry Pi Imager."
    Stop-Transcript | Out-Null
    return
}
$imagerArgs = @(
    '--cli',
    $ImgFile,
    "\\.\PhysicalDrive$DiskNumber"
)
Write-Host ("  cmd: {0} {1}" -f $ImagerExe, ($imagerArgs -join ' '))
$proc = Start-Process -FilePath $ImagerExe -ArgumentList $imagerArgs -Wait -PassThru -NoNewWindow
Write-Host "rpi-imager exit: $($proc.ExitCode)"
if ($proc.ExitCode -ne 0) {
    Write-Host "rpi-imager failed; falling back to PowerShell raw write..."
    $sourceStream = [System.IO.File]::OpenRead($ImgFile)
    $destPath = "\\.\PhysicalDrive$DiskNumber"
    try {
        $destStream = New-Object System.IO.FileStream($destPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write)
        $buf = New-Object byte[] (4MB)
        $total = $sourceStream.Length
        $written = 0
        while ($true) {
            $n = $sourceStream.Read($buf, 0, $buf.Length)
            if ($n -le 0) { break }
            $destStream.Write($buf, 0, $n)
            $written += $n
            if (($written % 256MB) -lt $buf.Length) {
                Write-Host ("  {0:N0} / {1:N0} MB ({2:P1})" -f ($written/1MB), ($total/1MB), ($written/$total))
            }
        }
        $destStream.Flush()
        $destStream.Close()
        Write-Host "raw write complete: $written bytes"
    } finally {
        $sourceStream.Close()
    }
}

# 6. Trigger Windows to re-read partitions
"select disk $DiskNumber`r`nrescan" | Out-File -FilePath "$WorkDir\rescan.dp" -Encoding ASCII -Force
& diskpart /s "$WorkDir\rescan.dp" 2>&1 | Out-Null

Write-Host ""
Write-Host "=== HAOS flash complete $(Get-Date -Format o) ==="
"flash-baby-pi-haos done at $(Get-Date -Format o)" | Out-File -FilePath $Marker -Append -Encoding ASCII

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Eject SD safely from F:"
Write-Host "  2. Insert into Pi 5; plug ethernet + power (no monitor needed)"
Write-Host "  3. Wait ~5-8 min for HAOS first-boot install"
Write-Host "  4. Browse to http://homeassistant.local:8123/  (or http://[pi-ip]:8123/)"
Write-Host "  5. Create your admin account and continue with the smart-home wizard"

Stop-Transcript | Out-Null
