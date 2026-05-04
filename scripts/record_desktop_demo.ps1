# record_desktop_demo.ps1 — runs the full desktop demo recording.
# MUST be run from a normal PowerShell terminal (NOT inside the Claude Code sandbox)
# because launching a visible Chromium window requires desktop session access.
#
# Usage:
#   pwsh -ExecutionPolicy Bypass -File D:\cortex\scripts\record_desktop_demo.ps1
#
# Or just open PowerShell and paste:
#   D:\cortex\scripts\record_desktop_demo.ps1

$ErrorActionPreference = "Stop"

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Cortex full-desktop demo recording" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Load the Discord token from ~/.hermes/.env
$envFile = "$env:USERPROFILE\.hermes\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match "^[A-Z_]+=" } | ForEach-Object {
        $parts = $_ -split "=", 2
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
    Write-Host "✓ Loaded ~/.hermes/.env" -ForegroundColor Green
} else {
    Write-Host "! No ~/.hermes/.env found — Discord posts will be skipped" -ForegroundColor Yellow
}

# 2. Ensure UTF-8 stdout
$env:PYTHONIOENCODING = "utf-8"

# 3. Pre-cleanup
Get-Process chrome,chromium,ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Get-ChildItem "$env:TEMP\cortex_demo_profile_*" -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

# 4. Run the recorder
$python = "C:\Users\soumi\cortex\.venv\Scripts\python.exe"
$script = "D:\cortex\scripts\record_desktop_demo.py"

if (-not (Test-Path $python)) { throw "python not found: $python" }
if (-not (Test-Path $script)) { throw "script not found: $script" }

Write-Host ""
Write-Host "Starting recording... a Chrome window will open." -ForegroundColor Cyan
Write-Host "Don't close it. The script will close it when done (~6-8 min)." -ForegroundColor Cyan
Write-Host ""

& $python $script

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Done." -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Output: D:\cortex\scans\recordings\_mp4\cortex_desktop_full.mp4"
Write-Host ""
