# admin-recovery.ps1
# Run from an ELEVATED PowerShell window (right-click PowerShell -> Run as administrator).
# Performs the post-incident infra cleanup that requires admin privileges:
#   1. Restart the cloudflared Windows service to load updated ingress config
#   2. Disable the rtk-cortex-webapp service (port 8765 conflict)
#   3. Install wrangler + register www.redteamkitchen.com as a Pages domain
#   4. Probe all redteamkitchen.com hostnames end-to-end
#
# This script is idempotent -- running it twice is safe.

$ErrorActionPreference = 'Continue'

# ---------- result tracking ----------
$script:Passed  = 0
$script:Skipped = 0
$script:Failed  = 0
$script:Results = New-Object System.Collections.ArrayList

function Write-Header {
    param([string]$Name)
    Write-Host ""
    Write-Host ("==> " + $Name) -ForegroundColor Cyan
}

function Write-OK {
    param([string]$Name, [string]$Reason)
    Write-Host ("[OK]   " + $Reason) -ForegroundColor Green
    $script:Passed++
    [void]$script:Results.Add(@{ Name = $Name; Status = 'OK';   Reason = $Reason })
}

function Write-Skip {
    param([string]$Name, [string]$Reason)
    Write-Host ("[SKIP] " + $Reason) -ForegroundColor Yellow
    $script:Skipped++
    [void]$script:Results.Add(@{ Name = $Name; Status = 'SKIP'; Reason = $Reason })
}

function Write-Fail {
    param([string]$Name, [string]$Reason)
    Write-Host ("[FAIL] " + $Reason) -ForegroundColor Red
    $script:Failed++
    [void]$script:Results.Add(@{ Name = $Name; Status = 'FAIL'; Reason = $Reason })
}

# ---------- elevation guard ----------
$currentUser   = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrinc  = New-Object Security.Principal.WindowsPrincipal($currentUser)
$isAdmin       = $currentPrinc.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run from an ELEVATED PowerShell window." -ForegroundColor Red
    Write-Host "Right-click PowerShell and choose 'Run as administrator', then re-run." -ForegroundColor Red
    exit 1
}

Write-Host "admin-recovery.ps1 starting (elevated session detected)" -ForegroundColor Cyan
Write-Host ("Timestamp: " + (Get-Date -Format 's'))

# =====================================================================
# STEP 1 -- Restart cloudflared
# =====================================================================
Write-Header "Step 1: Restart cloudflared Windows service"
$step1Name = 'restart-cloudflared'
try {
    $serviceCandidates = @('cloudflared', 'rtk-cloudflared')
    $foundService = $null
    foreach ($svcName in $serviceCandidates) {
        $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if ($null -ne $svc) {
            $foundService = $svc
            Write-Host ("  found service: " + $svc.Name + " (status=" + $svc.Status + ")")
            break
        }
    }

    if ($null -ne $foundService) {
        try {
            Restart-Service -Name $foundService.Name -Force -ErrorAction Stop
            Start-Sleep -Seconds 3
            $svcAfter = Get-Service -Name $foundService.Name
            if ($svcAfter.Status -eq 'Running') {
                Write-OK $step1Name ("Service '" + $foundService.Name + "' restarted and Running")
            } else {
                Write-Fail $step1Name ("Service '" + $foundService.Name + "' status after restart: " + $svcAfter.Status)
            }
        } catch {
            Write-Fail $step1Name ("Restart-Service failed: " + $_.Exception.Message)
        }
    } else {
        Write-Host "  no cloudflared/rtk-cloudflared service found; falling back to process restart"
        $proc = Get-Process -Name cloudflared -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                Start-Sleep -Seconds 2
                $cfgPath = "C:\Users\soumi\.cloudflared\config.yml"
                if (Test-Path $cfgPath) {
                    $cfBin = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
                    if ($null -ne $cfBin) {
                        Start-Process -FilePath $cfBin.Source -ArgumentList @('tunnel','--config',$cfgPath,'run') -WindowStyle Hidden
                        Start-Sleep -Seconds 3
                        $check = Get-Process -Name cloudflared -ErrorAction SilentlyContinue
                        if ($null -ne $check) {
                            Write-OK $step1Name "cloudflared process killed and re-launched from config"
                        } else {
                            Write-Fail $step1Name "cloudflared process did not come back after relaunch"
                        }
                    } else {
                        Write-Fail $step1Name "cloudflared.exe not on PATH; cannot relaunch"
                    }
                } else {
                    Write-Fail $step1Name ("config.yml not found at " + $cfgPath)
                }
            } catch {
                Write-Fail $step1Name ("Stop-Process failed: " + $_.Exception.Message)
            }
        } else {
            Write-Skip $step1Name "no cloudflared service or process present; nothing to restart"
        }
    }
} catch {
    Write-Fail $step1Name ("unexpected error: " + $_.Exception.Message)
}

# =====================================================================
# STEP 2 -- Disable rtk-cortex-webapp service
# =====================================================================
Write-Header "Step 2: Stop + disable rtk-cortex-webapp (port 8765 conflict)"
$step2Name = 'disable-rtk-cortex-webapp'
try {
    $webapp = Get-Service -Name 'rtk-cortex-webapp' -ErrorAction SilentlyContinue
    if ($null -eq $webapp) {
        Write-Skip $step2Name "rtk-cortex-webapp service not installed; nothing to do"
    } else {
        Stop-Service -Name 'rtk-cortex-webapp' -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        try {
            Set-Service -Name 'rtk-cortex-webapp' -StartupType Manual -ErrorAction Stop
            $after = Get-Service -Name 'rtk-cortex-webapp'
            $startMode = (Get-CimInstance -ClassName Win32_Service -Filter "Name='rtk-cortex-webapp'").StartMode
            Write-OK $step2Name ("status=" + $after.Status + ", startup=" + $startMode)
        } catch {
            Write-Fail $step2Name ("Set-Service failed: " + $_.Exception.Message)
        }
    }
} catch {
    Write-Fail $step2Name ("unexpected error: " + $_.Exception.Message)
}

# =====================================================================
# STEP 3 -- Install wrangler + add Pages domain
# =====================================================================
Write-Header "Step 3a: Install wrangler globally via npm"
$step3aName = 'install-wrangler'
try {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if ($null -eq $npm) {
        Write-Fail $step3aName "npm not found on PATH; install Node.js first"
    } else {
        $existing = & $npm.Source ls -g wrangler --depth=0 2>$null
        $alreadyInstalled = $false
        if ($LASTEXITCODE -eq 0 -and $existing -match 'wrangler@') {
            $alreadyInstalled = $true
        }
        if ($alreadyInstalled) {
            Write-OK $step3aName "wrangler already installed globally"
        } else {
            & $npm.Source install -g wrangler 2>&1 | ForEach-Object { Write-Host ("  " + $_) }
            if ($LASTEXITCODE -eq 0) {
                Write-OK $step3aName "wrangler installed via npm -g"
            } else {
                Write-Fail $step3aName ("npm install -g wrangler exited with code " + $LASTEXITCODE)
            }
        }
    }
} catch {
    Write-Fail $step3aName ("unexpected error: " + $_.Exception.Message)
}

Write-Header "Step 3b: wrangler login (interactive) + add www.redteamkitchen.com"
$step3bName = 'wrangler-pages-domain-add'
try {
    $wrangler = Get-Command wrangler.cmd -ErrorAction SilentlyContinue
    if ($null -eq $wrangler) { $wrangler = Get-Command wrangler -ErrorAction SilentlyContinue }
    if ($null -eq $wrangler) {
        Write-Fail $step3bName "wrangler not on PATH after install; restart shell and re-run"
    } else {
        # Check whoami first
        $whoami = & $wrangler.Source whoami 2>&1
        $isLoggedIn = ($LASTEXITCODE -eq 0) -and ($whoami -notmatch 'not authenticated' -and $whoami -notmatch 'You are not')
        if (-not $isLoggedIn) {
            Write-Host ""
            Write-Host "  >>> wrangler is not logged in." -ForegroundColor Yellow
            Write-Host "  >>> A browser window will open for OAuth login." -ForegroundColor Yellow
            Write-Host "  >>> Press ENTER when you're ready to continue (or Ctrl+C to abort)." -ForegroundColor Yellow
            [void](Read-Host "  press ENTER to launch wrangler login")
            & $wrangler.Source login
            if ($LASTEXITCODE -ne 0) {
                Write-Fail $step3bName ("wrangler login exited with code " + $LASTEXITCODE)
            }
        } else {
            Write-Host ("  already logged in: " + ($whoami -join ' '))
        }

        # Now add the Pages domain (idempotent: if it already exists, wrangler returns nonzero with a known message)
        $domainOut = & $wrangler.Source pages domain add www.redteamkitchen.com --project-name=redteamkitchen 2>&1
        $domainOutStr = ($domainOut | Out-String)
        Write-Host ("  " + $domainOutStr.Trim())
        if ($LASTEXITCODE -eq 0) {
            Write-OK $step3bName "www.redteamkitchen.com added to Pages project 'redteamkitchen'"
        } elseif ($domainOutStr -match 'already' -or $domainOutStr -match 'exists') {
            Write-OK $step3bName "www.redteamkitchen.com already attached to Pages project"
        } else {
            Write-Fail $step3bName ("wrangler pages domain add failed (exit " + $LASTEXITCODE + ")")
        }
    }
} catch {
    Write-Fail $step3bName ("unexpected error: " + $_.Exception.Message)
}

# =====================================================================
# STEP 4 -- Probe all hostnames
# =====================================================================
Write-Header "Step 4: End-to-end probes"

$probes = @(
    @{ Url = 'https://redteamkitchen.com/';                       Hint = 'apex (Cloudflare Pages)' },
    @{ Url = 'https://www.redteamkitchen.com/';                   Hint = 'www (Pages custom domain)' },
    @{ Url = 'https://cortex.redteamkitchen.com/';                Hint = 'cortex tunnel route' },
    @{ Url = 'https://mercury.redteamkitchen.com/';               Hint = 'mercury tunnel (Host-header fix)' },
    @{ Url = 'https://ollama.redteamkitchen.com/api/tags';        Hint = 'Ollama tag list (port 11434)' },
    @{ Url = 'https://inference.redteamkitchen.com/healthz';      Hint = 'inference router (port 8765)' }
)

# Force TLS 1.2 for PS5.1
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}

foreach ($p in $probes) {
    $url = $p.Url
    $hint = $p.Hint
    $probeName = "probe " + $url
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            Write-OK $probeName ("200 " + $url + "  -- " + $hint)
        } else {
            Write-Fail $probeName ($resp.StatusCode.ToString() + " " + $url + "  -- " + $hint)
        }
    } catch [System.Net.WebException] {
        $code = ''
        try {
            if ($_.Exception.Response -ne $null) {
                $code = [int]$_.Exception.Response.StatusCode
            }
        } catch {}
        if ($code -ne '') {
            Write-Fail $probeName ($code.ToString() + " " + $url + "  -- " + $hint)
        } else {
            Write-Fail $probeName ("ERR " + $url + "  -- " + $_.Exception.Message)
        }
    } catch {
        # Newer PS may surface HttpResponseException differently
        $msg = $_.Exception.Message
        $code = ''
        if ($msg -match '\((\d{3})\)') { $code = $matches[1] }
        if ($code -ne '') {
            Write-Fail $probeName ($code + " " + $url + "  -- " + $hint)
        } else {
            Write-Fail $probeName ("ERR " + $url + "  -- " + $msg)
        }
    }
}

# =====================================================================
# Summary
# =====================================================================
Write-Host ""
Write-Host "================ SUMMARY ================" -ForegroundColor Cyan
Write-Host ("Passed:  " + $script:Passed)  -ForegroundColor Green
Write-Host ("Skipped: " + $script:Skipped) -ForegroundColor Yellow
Write-Host ("Failed:  " + $script:Failed)  -ForegroundColor Red
Write-Host "-----------------------------------------"
foreach ($r in $script:Results) {
    $color = 'White'
    if ($r.Status -eq 'OK')   { $color = 'Green' }
    if ($r.Status -eq 'SKIP') { $color = 'Yellow' }
    if ($r.Status -eq 'FAIL') { $color = 'Red' }
    Write-Host (("  [{0,-4}] {1}  -- {2}") -f $r.Status, $r.Name, $r.Reason) -ForegroundColor $color
}
Write-Host "========================================="

if ($script:Failed -gt 0) {
    exit 1
} else {
    exit 0
}
