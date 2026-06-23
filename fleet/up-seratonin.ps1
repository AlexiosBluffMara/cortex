# up-seratonin.ps1 — start the full Seratonin (Windows / RTX 5090) stack
# Run from PowerShell or pwsh; requires Python 3.12+ at C:\Users\soumi\cortex\.venv
#
# NEUTRALIZED 2026-05-15: this is the `Cortex_StackLaunch` logon task. It
# spawned the dead-architecture services (router :8766, backend :8773,
# vite :5173 via npm.cmd) which no longer exist; npm.cmd flashed a cmd.exe
# window. Live stack is mercury_watchdog + start-cortex (:8765). Exits
# immediately unless FLEET_WD_ENABLED=1 so the logon task is a clean no-op.
if ($env:FLEET_WD_ENABLED -ne "1") { exit 0 }
$ErrorActionPreference = "Stop"
$Repo  = "D:\cortex"
$Venv  = "C:\Users\soumi\cortex\.venv\Scripts\python.exe"
$MerVe = "D:\mercury\.venv\Scripts\mercury.exe"
$Logs  = "C:\Temp\logs"
New-Item -ItemType Directory -Path $Logs -Force | Out-Null

# Load ~/.hermes/.env into current env
$EnvFile = "$env:USERPROFILE\.hermes\.env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | Where-Object { $_ -and -not $_.StartsWith('#') -and $_.Contains('=') } | ForEach-Object {
        $parts = $_.Split('=', 2)
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
    }
}

function StartDetached($Name, $Exe, $Args, $Cwd, $Log) {
    Write-Host "[up] $Name -> $Log"
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Exe
    $info.Arguments = ($Args -join ' ')
    $info.WorkingDirectory = $Cwd
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError  = $true
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    # Inherit current env
    $proc = [System.Diagnostics.Process]::Start($info)
    Start-Job -ScriptBlock {
        param($p, $log)
        while (-not $p.HasExited) { $p.StandardOutput.ReadToEnd() | Out-File -Append -Encoding utf8 $log; Start-Sleep -Milliseconds 100 }
    } -ArgumentList $proc, $Log | Out-Null
    Write-Host "    PID=$($proc.Id)"
}

# 1. Inference router (8766)
$env:OLLAMA_BACKENDS = "http://localhost:11434"
$env:ROUTER_PORT     = "8766"
StartDetached "router" $Venv @("-m","uvicorn","inference_router.server:app","--host","0.0.0.0","--port","8766","--log-level","info") $Repo "$Logs\cortex_router.log"

# 2. Cortex backend (8773)
$env:OLLAMA_URL    = "http://localhost:8766"
$env:MODEL_FAST    = "gemma4:e4b"
$env:MODEL_DEEP    = "gemma4:26b"
$env:MODEL_EXPERT  = "gemma4:31b"
$env:PYTHONDONTWRITEBYTECODE = "1"
StartDetached "backend" $Venv @("-m","uvicorn","webapp.server:app","--host","0.0.0.0","--port","8773","--log-level","info") $Repo "$Logs\cortex_8773.log"

# 3. Vite frontend (5173)
StartDetached "vite" "C:\Program Files\nodejs\npm.cmd" @("run","dev") "$Repo\webapp" "$Logs\cortex_frontend.log"

# 4. Mercury gateway (Discord)
StartDetached "mercury-gw"   $MerVe @("gateway") "D:\mercury" "$Logs\mercury_gateway.log"
Start-Sleep -Seconds 4
# 5. Mercury dashboard (9119)
StartDetached "mercury-dash" $MerVe @("dashboard","--host","0.0.0.0","--port","9119","--insecure") "D:\mercury" "$Logs\mercury_dashboard.log"

Write-Host ""
Write-Host "Seratonin stack starting. Run 'bash fleet/status.sh' to verify."
Write-Host "Public URL (when funnel up): https://seratonin.scylla-betta.ts.net"
