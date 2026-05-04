# record_desktop_demo.ps1 — runs the full desktop demo recording.
# Compatible with both Windows PowerShell 5.1 and pwsh 7+.
#
# Just double-click or run from any PowerShell:
#   D:\cortex\scripts\record_desktop_demo.ps1
#
# The Python recorder uses your SYSTEM Chrome (not Playwright's bundled binary,
# which Windows Defender keeps quarantining). Defender trusts your real Chrome,
# so the recording loop is reliable.

$ErrorActionPreference = "Stop"

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Cortex full-desktop demo recording" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

$python = "C:\Users\soumi\cortex\.venv\Scripts\python.exe"
$script = "D:\cortex\scripts\record_desktop_demo.py"
$out    = "D:\cortex\scans\recordings\_mp4\cortex_desktop_full.mp4"

if (-not (Test-Path $python)) { throw "python not found: $python" }
if (-not (Test-Path $script)) { throw "script not found: $script" }

# 1. Verify system Chrome is present (the recorder uses it via CDP, no Playwright chromium needed)
$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chromePath)) {
    $chromePath = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
}
if (-not (Test-Path $chromePath)) {
    throw "Neither Chrome nor Edge found. Install Chrome from https://www.google.com/chrome/"
}
Write-Host "OK system browser: $chromePath" -ForegroundColor Green

# 2. Load environment from ~/.hermes/.env
$envFile = "$env:USERPROFILE\.hermes\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match "^[A-Z_]+=" -and -not $_.StartsWith("#") } | ForEach-Object {
        $parts = $_ -split "=", 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }
    Write-Host "OK loaded ~/.hermes/.env" -ForegroundColor Green
} else {
    Write-Host "WARN no ~/.hermes/.env found - Discord posts will be skipped" -ForegroundColor Yellow
}

# 3. UTF-8 stdout for Python so we don't crash on emoji
$env:PYTHONIOENCODING = "utf-8"

# 4. Pre-cleanup: kill any prior chrome we spawned + ffmpeg + temp profiles
Get-Process chrome,chromium,ffmpeg -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowTitle -notmatch "Chrome"   # don't touch the user's main browser
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Get-ChildItem "$env:TEMP\cortex_demo_profile_*" -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 5. Run the recorder
Write-Host ""
Write-Host "Starting recording..." -ForegroundColor Cyan
Write-Host "A Chrome window will open and drive the demo automatically." -ForegroundColor Cyan
Write-Host "DO NOT close it. The script closes it when done (~6-10 min)." -ForegroundColor Cyan
Write-Host ""

& $python $script
$exitCode = $LASTEXITCODE

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Done." -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

if (Test-Path $out) {
    $sizeMB = [math]::Round((Get-Item $out).Length / 1MB, 1)
    Write-Host ("Output: " + $out + " (" + $sizeMB + " MB)") -ForegroundColor Green
} else {
    Write-Host ("WARN output file not produced: " + $out) -ForegroundColor Yellow
    if ($exitCode -ne 0) {
        Write-Host ("Python exited with code " + $exitCode) -ForegroundColor Yellow
    }
}
