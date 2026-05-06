#Requires -RunAsAdministrator
# flash-baby-pi-v2.ps1 — robust elevated flash with diagnostics.

$ErrorActionPreference = 'Continue'
$Marker = 'C:\Users\soumi\AppData\Local\Temp\baby-pi-flash-marker.txt'
$WorkDir = 'C:\Users\soumi\AppData\Local\Temp\baby-pi-flash'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# Drop marker IMMEDIATELY so we can detect this script ever launched.
"flash-baby-pi-v2 launched at $(Get-Date -Format o)" | Out-File -FilePath $Marker -Encoding ASCII -Force
Write-Host "MARKER written: $Marker"

# Transcript everything that follows
Start-Transcript -Path "$WorkDir\flash.log" -Append -Force | Out-Null
Write-Host "=== flash-baby-pi-v2 $(Get-Date -Format o) ==="

$DiskNumber = 4
$ImagerExe = 'C:\Program Files\Raspberry Pi Ltd\Imager\rpi-imager.exe'
$ImageUrl = 'https://downloads.raspberrypi.com/raspios_lite_arm64_latest'
$ImgArchive = Join-Path $WorkDir 'pios-lite-arm64.img.xz'
$ImgFile = Join-Path $WorkDir 'pios-lite-arm64.img'

# 1. Verify disk
$disk = Get-Disk -Number $DiskNumber
Write-Host "Disk $DiskNumber: $($disk.FriendlyName) | $([math]::Round($disk.Size/1GB,1)) GB | $($disk.BusType)"
if ($disk.Size -gt 300GB -or $disk.BusType -notin @('USB','SD')) {
    Write-Host "REFUSING: disk doesn't look like an SD card"
    Stop-Transcript | Out-Null
    return
}

# 2. Wipe
Write-Host "--- wiping disk $DiskNumber ---"
"select disk $DiskNumber`r`nclean`r`nrescan" | Out-File -FilePath "$WorkDir\wipe.dp" -Encoding ASCII -Force
& diskpart /s "$WorkDir\wipe.dp" 2>&1 | ForEach-Object { Write-Host "  diskpart: $_" }
Write-Host "wipe done."

# 3. Download
if (-not (Test-Path $ImgArchive) -or (Get-Item $ImgArchive).Length -lt 100MB) {
    Write-Host "--- downloading $ImageUrl ---"
    $progressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $ImageUrl -OutFile $ImgArchive -UseBasicParsing
    $progressPreference = 'Continue'
}
$archSizeMB = [math]::Round((Get-Item $ImgArchive).Length / 1MB, 1)
Write-Host "archive: $archSizeMB MB"

# 4. Decompress via WSL xz
if (-not (Test-Path $ImgFile)) {
    Write-Host "--- decompressing via WSL xz ---"
    & wsl -d Ubuntu bash -c "xz -dkfc '/mnt/c/Users/soumi/AppData/Local/Temp/baby-pi-flash/pios-lite-arm64.img.xz' > '/mnt/c/Users/soumi/AppData/Local/Temp/baby-pi-flash/pios-lite-arm64.img'"
}
$imgSizeGB = [math]::Round((Get-Item $ImgFile).Length / 1GB, 2)
Write-Host "img: $imgSizeGB GB"

# 5. Flash via rpi-imager
Write-Host "--- flashing via rpi-imager ---"
$args = "--cli --debug-output --quit `"$ImgFile`" \\.\PhysicalDrive$DiskNumber"
Write-Host "  cmd: $ImagerExe $args"
$proc = Start-Process -FilePath $ImagerExe -ArgumentList $args -Wait -PassThru -NoNewWindow
Write-Host "rpi-imager exit code: $($proc.ExitCode)"

if ($proc.ExitCode -ne 0) {
    Write-Host "rpi-imager failed; falling back to direct dd-style write..."
    # Direct copy via WSL2 dd to PhysicalDrive
    $bashCmd = "dd if='/mnt/c/Users/soumi/AppData/Local/Temp/baby-pi-flash/pios-lite-arm64.img' of=/dev/sdX bs=4M status=progress 2>&1"
    Write-Host "  (would need usbipd to expose SD as /dev/sdX in WSL2)"
    Write-Host "  trying PowerShell raw write instead..."

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
                Write-Host ("  {0:N0} / {1:N0} MB ({2:P})" -f ($written/1MB), ($total/1MB), ($written/$total))
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
Write-Host "--- waiting for boot partition ---"
"select disk $DiskNumber`r`nrescan" | Out-File -FilePath "$WorkDir\rescan.dp" -Encoding ASCII -Force
& diskpart /s "$WorkDir\rescan.dp" 2>&1 | Out-Null

$bootDrive = $null
for ($i = 0; $i -lt 60; $i++) {
    Update-HostStorageCache -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $vol = Get-Volume | Where-Object {
        $_.FileSystemLabel -in @('bootfs','boot') -and $_.FileSystem -eq 'FAT32'
    } | Select-Object -First 1
    if ($vol -and $vol.DriveLetter) {
        $bootDrive = "$($vol.DriveLetter):"
        break
    }
}
if (-not $bootDrive) {
    Write-Host "WARN: boot partition didn't auto-mount. Trying alternate detection..."
    $vol = Get-Disk -Number $DiskNumber | Get-Partition | Where-Object Type -ne 'Reserved' | Get-Volume -ErrorAction SilentlyContinue | Where-Object FileSystem -eq 'FAT32' | Select-Object -First 1
    if ($vol -and $vol.DriveLetter) { $bootDrive = "$($vol.DriveLetter):" }
}
Write-Host "boot partition: $bootDrive"

if ($bootDrive) {
    # 7. Configure
    Write-Host "--- writing headless boot config ---"
    New-Item -ItemType File -Force -Path "$bootDrive\ssh" | Out-Null
    'soumitlahiri:$6$rBoBy4r0lN.K8E2J$0lJfRxfQUzqhUiYY1mh3dGq5nnGd/eKJYJJjdsLFJ6gA5vHeBEZBg5wHe5aD3cOrC4VYUZ0JfVfH9jYpPYxqe.' | Out-File -FilePath "$bootDrive\userconf.txt" -Encoding ASCII -NoNewline -Force

    # Load the canonical firstrun.sh from the repo (Tailscale + AdGuard Home).
    # Replace the __SSH_PUBKEY__ placeholder with the real pubkey at flash time.
    $pubkey = (Get-Content 'C:\Users\soumi\.ssh\id_ed25519.pub' -Raw -ErrorAction SilentlyContinue).Trim()
    $firstrunSrc = Get-Content 'D:\cortex\baby-pi\firstrun.sh' -Raw -ErrorAction Stop
    $firstrun = $firstrunSrc -replace '__SSH_PUBKEY__', $pubkey
    # firstrun.sh wants Unix line-endings + executable bit (the latter set by Linux on first read)
    [System.IO.File]::WriteAllText("$bootDrive\firstrun.sh", $firstrun.Replace("`r`n", "`n"), [System.Text.Encoding]::UTF8)

    # Drop the AdGuardHome.yaml on the boot partition; firstrun.sh moves it
    # into /opt/AdGuardHome/ after the AdGuard installer runs.
    $agh = Get-Content 'D:\cortex\baby-pi\AdGuardHome.yaml' -Raw -ErrorAction Stop
    [System.IO.File]::WriteAllText("$bootDrive\AdGuardHome.yaml", $agh.Replace("`r`n", "`n"), [System.Text.Encoding]::UTF8)
    Write-Host "AdGuardHome.yaml dropped on $bootDrive ($((Get-Item "$bootDrive\AdGuardHome.yaml").Length) bytes)"

    # Drop the kiosk-setup.sh (Chromium dual-monitor kiosk for the Targus dock).
    $kiosk = Get-Content 'D:\cortex\baby-pi\kiosk-setup.sh' -Raw -ErrorAction Stop
    [System.IO.File]::WriteAllText("$bootDrive\kiosk-setup.sh", $kiosk.Replace("`r`n", "`n"), [System.Text.Encoding]::UTF8)
    Write-Host "kiosk-setup.sh dropped on $bootDrive ($((Get-Item "$bootDrive\kiosk-setup.sh").Length) bytes)"

    $cmdlinePath = "$bootDrive\cmdline.txt"
    if (Test-Path $cmdlinePath) {
        $cmdline = (Get-Content $cmdlinePath -Raw).Trim()
        if ($cmdline -notmatch 'firstrun\.sh') {
            $cmdline += ' systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target'
            Set-Content -Path $cmdlinePath -Value $cmdline -Encoding ASCII -NoNewline
        }
    }

    $configPath = "$bootDrive\config.txt"
    if (Test-Path $configPath) {
        $cfg = Get-Content $configPath -Raw
        if ($cfg -notmatch 'cortex baby-pi') {
            Add-Content -Path $configPath -Value "`n# cortex baby-pi additions"
            Add-Content -Path $configPath -Value "hdmi_force_hotplug=1"
            Add-Content -Path $configPath -Value "max_framebuffers=2"
        }
    }

    Get-ChildItem $bootDrive | Format-Table Name, Length, LastWriteTime -AutoSize
    Write-Host "config done on $bootDrive"
}

Write-Host "=== flash-baby-pi-v2 done $(Get-Date -Format o) ==="
"flash-baby-pi-v2 done at $(Get-Date -Format o)" | Out-File -FilePath $Marker -Append -Encoding ASCII

Stop-Transcript | Out-Null
