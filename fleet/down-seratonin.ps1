# down-seratonin.ps1 — stop the entire Seratonin stack cleanly
# Run from PowerShell or pwsh.
$ErrorActionPreference = "Continue"

function StopOnPort($port, $name) {
    $conns = netstat -ano | Select-String ":$port\s.*LISTENING"
    foreach ($c in $conns) {
        $tokens = ($c.Line -split '\s+') | Where-Object { $_ }
        $pid = $tokens[-1]
        if ($pid -match '^\d+$') {
            Write-Host "[down] $name (port $port) PID=$pid"
            taskkill /PID $pid /F /T 2>&1 | Out-Null
        }
    }
}

StopOnPort 8773 "cortex-backend"
StopOnPort 5173 "vite"
StopOnPort 8766 "inference-router"
StopOnPort 9119 "mercury-dashboard"

# Kill any remaining mercury.exe instances (gateway has no port)
Get-Process mercury -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "[down] mercury PID=$($_.Id)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Seratonin stack stopped. Ollama (system service) and Tailscale left running."
