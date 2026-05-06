# admin-fix-all.ps1 - elevated script that fixes everything that needs admin.
# Run from elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File D:\cortex\scripts\admin-fix-all.ps1
# Idempotent. Re-runnable. ASCII only (works in Windows PowerShell 5.1).

$ErrorActionPreference = "Continue"

function Section($name) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor Cyan
    Write-Host ("  " + $name) -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor Cyan
}
function OK   { param($msg) Write-Host ("  [ok]   " + $msg) -ForegroundColor Green }
function WARN { param($msg) Write-Host ("  [warn] " + $msg) -ForegroundColor Yellow }
function FAIL { param($msg) Write-Host ("  [fail] " + $msg) -ForegroundColor Red }

# Verify elevation
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    FAIL "Not running as administrator. Right-click PowerShell -> Run as administrator, then re-run this script."
    exit 1
}
OK "Running with administrator privileges"

# ----- 1. IPv6 prefix policy --------------------------------------------------
Section "1. Fix IPv6 prefix policy (browser DNS fix)"
try {
    netsh interface ipv6 set prefixpolicy "::ffff:0:0/96" 60 4 | Out-Null
    OK "Set ::ffff:0:0/96 precedence to 60 (IPv4 now beats IPv6 on this network)"
} catch {
    FAIL ("netsh failed: " + $_.Exception.Message)
}
Write-Host ""
Write-Host "  Current prefix policies:"
netsh interface ipv6 show prefixpolicies | Select-Object -Skip 3 | ForEach-Object { "    $_" }

# ----- 2. hosts file pin ------------------------------------------------------
Section "2. Pin redteamkitchen.com hostnames to Cloudflare IPv4 in hosts file"
$hostsFile = "$env:WINDIR\System32\drivers\etc\hosts"
$marker = "# Cortex / Red Team Kitchen - IPv4 pin"
$content = Get-Content $hostsFile -Raw -ErrorAction SilentlyContinue
if ($content -and $content.Contains($marker)) {
    OK "Already pinned (marker found in hosts file)"
} else {
    $today = Get-Date -Format "yyyy-MM-dd"
    $lines = @(
        "",
        "$marker (added $today)",
        "104.21.58.37    redteamkitchen.com",
        "172.67.199.126  www.redteamkitchen.com",
        "104.21.58.37    cortex.redteamkitchen.com",
        "172.67.199.126  mercury.redteamkitchen.com",
        "104.21.58.37    ollama.redteamkitchen.com",
        "172.67.199.126  inference.redteamkitchen.com"
    )
    try {
        Add-Content -Path $hostsFile -Value $lines -Encoding ASCII
        OK "Added 6 hostname pins"
    } catch {
        FAIL ("hosts write failed: " + $_.Exception.Message)
    }
}
ipconfig /flushdns | Out-Null
OK "DNS cache flushed"

# ----- 3. Stop rtk-cortex-webapp ----------------------------------------------
Section "3. Stop rtk-cortex-webapp service (frees port 8765)"
$svc = Get-Service rtk-cortex-webapp -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -eq "Running") {
        try {
            Stop-Service rtk-cortex-webapp -Force
            OK "Stopped rtk-cortex-webapp"
        } catch {
            FAIL ("stop failed: " + $_.Exception.Message)
        }
    } else {
        OK "rtk-cortex-webapp already stopped"
    }
    try {
        Set-Service -Name rtk-cortex-webapp -StartupType Manual
        OK "Set startup type to Manual (will not auto-start on next boot)"
    } catch {
        WARN ("could not change startup type: " + $_.Exception.Message)
    }
} else {
    OK "Service rtk-cortex-webapp not present"
}

# ----- 4. Restart rtk-cloudflared ---------------------------------------------
Section "4. Restart rtk-cloudflared (loads new ingress: Mercury httpHostHeader, ollama:11434, inference:8765)"
try {
    Restart-Service rtk-cloudflared -Force
    Start-Sleep -Seconds 8
    $st = (Get-Service rtk-cloudflared).Status
    if ($st -eq "Running") {
        OK "rtk-cloudflared restarted (status: Running)"
    } else {
        WARN ("status after restart: " + $st)
    }
} catch {
    FAIL ("restart failed: " + $_.Exception.Message)
}

# ----- 5. www.redteamkitchen.com on Pages -------------------------------------
Section "5. Add www.redteamkitchen.com as Pages custom domain via wrangler"
$wrangler = (Get-Command wrangler -ErrorAction SilentlyContinue)
if (-not $wrangler) {
    Write-Host "  wrangler not found; trying npm install -g wrangler..."
    try {
        npm install -g wrangler --silent 2>&1 | Out-Null
        $wrangler = (Get-Command wrangler -ErrorAction SilentlyContinue)
    } catch {
        WARN ("npm install failed: " + $_.Exception.Message)
    }
}
if ($wrangler) {
    OK ("wrangler at " + $wrangler.Source)
    try {
        $cfCreds = Get-Content "$env:USERPROFILE\.cloudflare\credentials" -ErrorAction Stop
        $env:CLOUDFLARE_API_TOKEN  = ($cfCreds | Select-String "CLOUDFLARE_API_TOKEN=")  | ForEach-Object { $_.ToString().Split("=",2)[1].Trim() }
        $env:CLOUDFLARE_ACCOUNT_ID = ($cfCreds | Select-String "CLOUDFLARE_ACCOUNT_ID=") | ForEach-Object { $_.ToString().Split("=",2)[1].Trim() }
        $output = & wrangler pages domain add www.redteamkitchen.com --project-name=redteamkitchen 2>&1 | Out-String
        if ($output -match "added|already") {
            OK "www custom domain configured on Pages"
        } else {
            WARN ("wrangler output: " + $output.Trim())
        }
    } catch {
        WARN ("wrangler add failed: " + $_.Exception.Message)
        Write-Host ("  Manual fallback: https://dash.cloudflare.com/" + $env:CLOUDFLARE_ACCOUNT_ID + "/pages/view/redteamkitchen/domains")
    }
} else {
    WARN "wrangler unavailable; install Node.js then re-run."
}

# ----- 6. Final verification --------------------------------------------------
Section "6. Verify all 7 public endpoints"
$urls = @(
    @{u = "https://redteamkitchen.com/";                   n = "redteamkitchen.com /"},
    @{u = "https://www.redteamkitchen.com/";               n = "www.redteamkitchen.com /"},
    @{u = "https://cortex.redteamkitchen.com/";            n = "cortex.redteamkitchen.com /"},
    @{u = "https://cortex.redteamkitchen.com/api/healthz"; n = "cortex /api/healthz"},
    @{u = "https://mercury.redteamkitchen.com/";           n = "mercury.redteamkitchen.com /"},
    @{u = "https://ollama.redteamkitchen.com/api/tags";    n = "ollama /api/tags"},
    @{u = "https://inference.redteamkitchen.com/healthz";  n = "inference /healthz"}
)
foreach ($t in $urls) {
    try {
        $r = Invoke-WebRequest -Uri $t.u -SkipHttpErrorCheck -TimeoutSec 12 -UseBasicParsing -ErrorAction Stop
        $line = ("    {0,-45} -> {1}  ({2}B)" -f $t.n, $r.StatusCode, $r.Content.Length)
        if ($r.StatusCode -eq 200) {
            Write-Host $line -ForegroundColor Green
        } elseif ($r.StatusCode -lt 400) {
            Write-Host $line -ForegroundColor Yellow
        } else {
            Write-Host $line -ForegroundColor Red
        }
    } catch {
        Write-Host ("    {0,-45} -> ERR" -f $t.n) -ForegroundColor Red
    }
}

Section "Done"
Write-Host "  Restart your browser and try https://mercury.redteamkitchen.com/" -ForegroundColor Green
Write-Host "  If a step above failed, scroll up to find [fail] lines and tell me." -ForegroundColor Green
