# flash-baby-pi.ps1
# Wipe the SD card on Disk 4, flash Raspberry Pi OS Lite 64-bit, drop a
# headless-boot config so the Pi comes up with hostname=baby-pi, user
# soumitlahiri, SSH enabled with our key, and a firstrun.sh that installs
# Ollama + Tailscale on first boot.
#
# Must be run elevated.

#Requires -RunAsAdministrator

$ErrorActionPreference = 'Stop'
$DiskNumber = 4    # <-- the 238 GB removable SD via the USB3 reader
$ImagerExe = 'C:\Program Files\Raspberry Pi Ltd\Imager\rpi-imager.exe'
$ImageUrl = 'https://downloads.raspberrypi.com/raspios_lite_arm64_latest'
$WorkDir = 'C:\Users\soumi\AppData\Local\Temp\baby-pi-flash'
$ImgArchive = Join-Path $WorkDir 'pios-lite-arm64.img.xz'
$ImgFile = Join-Path $WorkDir 'pios-lite-arm64.img'
$LogPath = Join-Path $WorkDir 'flash.log'

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Start-Transcript -Path $LogPath -Append | Out-Null
Write-Host "=== flash-baby-pi $(Get-Date -Format o) ==="

# ---------- 1. Verify disk identity ----------
$disk = Get-Disk -Number $DiskNumber
if (-not $disk) { throw "Disk $DiskNumber not present" }
Write-Host "Disk $DiskNumber: $($disk.FriendlyName) | $([math]::Round($disk.Size/1GB,1)) GB | $($disk.BusType)"
if ($disk.Size -gt 300GB) {
    throw "Refusing to wipe a >300 GB disk (got $([math]::Round($disk.Size/1GB,1)) GB) — wrong target?"
}
if ($disk.BusType -notin @('USB','SD')) {
    throw "Disk $DiskNumber is $($disk.BusType), not USB/SD — wrong target."
}

# ---------- 2. Wipe ----------
Write-Host "--- wiping disk $DiskNumber ---"
$dpScript = @"
select disk $DiskNumber
clean
"@
$dpFile = Join-Path $WorkDir 'wipe.dp'
Set-Content -Path $dpFile -Value $dpScript -Encoding ASCII
diskpart /s $dpFile | Out-Null
Write-Host "disk wiped."

# ---------- 3. Download Pi OS Lite 64-bit ----------
if (-not (Test-Path $ImgArchive)) {
    Write-Host "--- downloading $ImageUrl ---"
    Invoke-WebRequest -Uri $ImageUrl -OutFile $ImgArchive -UseBasicParsing
}
Write-Host "archive: $((Get-Item $ImgArchive).Length / 1MB) MB"

if (-not (Test-Path $ImgFile)) {
    Write-Host "--- decompressing ---"
    # 7-Zip if installed, else WSL xz
    $sevenZip = 'C:\Program Files\7-Zip\7z.exe'
    if (Test-Path $sevenZip) {
        & $sevenZip x -y "-o$WorkDir" $ImgArchive | Out-Null
    } else {
        # use WSL to decompress
        $wslArchive = $ImgArchive -replace '\\','/' -replace '^C:','/mnt/c'
        $wslImg = $ImgFile -replace '\\','/' -replace '^C:','/mnt/c'
        wsl -d Ubuntu bash -c "xz -dkc '$wslArchive' > '$wslImg'"
    }
}
Write-Host "img: $((Get-Item $ImgFile).Length / 1GB) GB"

# ---------- 4. Flash via rpi-imager CLI ----------
Write-Host "--- flashing $ImgFile -> \\.\PhysicalDrive$DiskNumber ---"
$arglist = @(
    '--cli',
    '--debug-output',
    '--quit',
    '"' + $ImgFile + '"',
    '\\.\PhysicalDrive' + $DiskNumber
)
$proc = Start-Process -FilePath $ImagerExe -ArgumentList ($arglist -join ' ') -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    throw "rpi-imager exited $($proc.ExitCode)"
}
Write-Host "flash complete."

# ---------- 5. Wait for Windows to mount the boot partition ----------
Write-Host "--- waiting for boot partition mount ---"
$bootDrive = $null
for ($i = 0; $i -lt 30; $i++) {
    Update-HostStorageCache -ErrorAction SilentlyContinue
    $vol = Get-Volume | Where-Object { $_.FileSystemLabel -in @('bootfs','boot') -and $_.FileSystem -eq 'FAT32' } | Select-Object -First 1
    if ($vol -and $vol.DriveLetter) { $bootDrive = "$($vol.DriveLetter):"; break }
    Start-Sleep -Seconds 2
}
if (-not $bootDrive) { throw "boot partition never mounted" }
Write-Host "boot partition: $bootDrive"

# ---------- 6. Drop headless-boot config ----------
Write-Host "--- writing headless config to $bootDrive ---"

# 6a. SSH enabled flag
New-Item -ItemType File -Force -Path "$bootDrive\ssh" | Out-Null

# 6b. userconf.txt — username:cryptedpassword. crypt(3) "Pi rocks!" SHA512.
# (User can change after login; we mainly want SSH key auth anyway.)
$userconf = 'soumitlahiri:$6$rBoBy4r0lN.K8E2J$0lJfRxfQUzqhUiYY1mh3dGq5nnGd/eKJYJJjdsLFJ6gA5vHeBEZBg5wHe5aD3cOrC4VYUZ0JfVfH9jYpPYxqe.'
Set-Content -Path "$bootDrive\userconf.txt" -Value $userconf -Encoding ASCII -NoNewline

# 6c. firstrun.sh — runs once on first boot
$pubkey = (Get-Content 'C:\Users\soumi\.ssh\id_ed25519.pub' -Raw -ErrorAction SilentlyContinue).Trim()
$firstrun = @"
#!/bin/bash
set +e
exec >> /var/log/firstrun.log 2>&1
echo "=== firstrun \$(date) ==="

# Hostname
CURRENT_HOSTNAME=\$(tr -d '\0' < /etc/hostname | tr -d '\n')
echo baby-pi > /etc/hostname
sed -i "s/127.0.1.1.*\$CURRENT_HOSTNAME/127.0.1.1\tbaby-pi/g" /etc/hosts

# SSH key for soumitlahiri
SSH_DIR=/home/soumitlahiri/.ssh
mkdir -p \$SSH_DIR
echo "$pubkey" > \$SSH_DIR/authorized_keys
chmod 700 \$SSH_DIR
chmod 600 \$SSH_DIR/authorized_keys
chown -R soumitlahiri:soumitlahiri /home/soumitlahiri

# Enable SSH
systemctl enable --now ssh

# Apt
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl wget jq git build-essential cmake python3-venv python3-pip ca-certificates

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
# user runs `sudo tailscale up --ssh` after first SSH

# Ollama (arm64 binary)
curl -fsSL https://ollama.com/install.sh | sh

# Bind Ollama to LAN
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
EOF
systemctl daemon-reload
systemctl enable --now ollama

# Self-disable firstrun
rm -f /boot/firmware/firstrun.sh /boot/firstrun.sh
sed -i 's| systemd.run.*||' /boot/firmware/cmdline.txt 2>/dev/null || \
sed -i 's| systemd.run.*||' /boot/cmdline.txt 2>/dev/null

echo "=== firstrun done \$(date) ==="
exit 0
"@
Set-Content -Path "$bootDrive\firstrun.sh" -Value $firstrun -Encoding ASCII

# 6d. cmdline.txt — append systemd.run hook so firstrun.sh actually runs
$cmdlinePath = "$bootDrive\cmdline.txt"
$cmdline = (Get-Content $cmdlinePath -Raw).Trim()
if ($cmdline -notmatch 'firstrun\.sh') {
    $cmdline += ' systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target'
    Set-Content -Path $cmdlinePath -Value $cmdline -Encoding ASCII -NoNewline
}

# 6e. config.txt — enable HDMI hotplug for the dual-monitor dock setup later
$configPath = "$bootDrive\config.txt"
if (Test-Path $configPath) {
    Add-Content -Path $configPath -Value "`n# Cortex baby-pi additions"
    Add-Content -Path $configPath -Value "hdmi_force_hotplug=1"
    Add-Content -Path $configPath -Value "dtoverlay=vc4-kms-v3d"
    Add-Content -Path $configPath -Value "max_framebuffers=2"
}

Write-Host "--- inventory ---"
Get-ChildItem $bootDrive | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize

# ---------- 7. Eject ----------
Write-Host "--- safely ejecting ---"
$shell = New-Object -ComObject Shell.Application
$drive = $shell.Namespace(17).ParseName("$($bootDrive.Trim(':')):\")
if ($drive) { $drive.InvokeVerb('Eject') }

Write-Host ""
Write-Host "=== done $(Get-Date -Format o) ==="
Write-Host ""
Write-Host "Next steps for the user:"
Write-Host "  1. Move the SD into the Pi 5"
Write-Host "  2. Plug Pi into Targus dock + ethernet + power"
Write-Host "  3. Wait ~3 min for first boot (firstrun.sh runs apt + Tailscale + Ollama)"
Write-Host "  4. From WSL2: ssh soumitlahiri@<pi-lan-ip>"
Write-Host "  5. Run: sudo tailscale up --ssh --accept-routes"
Write-Host "  6. Verify: ollama list (no models yet); orchestra picks up baby-pi:11434"

Stop-Transcript | Out-Null
