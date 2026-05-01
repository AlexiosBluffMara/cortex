# bootstrap-cortex.ps1 — one-shot setup for Cortex on Windows (PowerShell).
#
# Cortex is a multimodal brain-response analysis system: TRIBE v2 (Meta) +
# Gemma 4 (Google), wrapped in a webapp that renders predicted BOLD activity
# on a 3D cortical surface ("Brain Cinema").
#
# Project: Alexios Bluff Mara LLC (dba Red Team Kitchen) x Illinois State University
# Repo:    https://github.com/AlexiosBluffMara/cortex
# Page:    https://alexiosbluffmara.github.io/cortex/
# License: Apache-2.0 (code) - TRIBE v2 is CC-BY-NC 4.0 (Meta), Gemma 4 is
#          under the Gemma Terms of Use (Google). Weights are pulled at
#          runtime, never bundled in the image.
#
# Honors env vars: HF_TOKEN, CORTEX_OFFLINE, HF_HUB_OFFLINE, CORTEX_CACHE_DIR,
# CORTEX_TAG, CORTEX_PORT, CORTEX_GPU_INDEX, CORTEX_CONTAINER_NAME.

[CmdletBinding()]
param(
  [string]$Tag           = $env:CORTEX_TAG,
  [int]   $Port          = 0,
  [int]   $GpuIndex      = -1,
  [string]$ContainerName = $env:CORTEX_CONTAINER_NAME,
  [string]$CacheDir      = $env:CORTEX_CACHE_DIR,
  [switch]$Offline
)

$ErrorActionPreference = 'Stop'

# __CORTEX_DEFAULT_TAG__ is rewritten by the release workflow.
$DefaultTag = '__CORTEX_DEFAULT_TAG__'
if ($DefaultTag -eq '__CORTEX_DEFAULT_TAG__') { $DefaultTag = 'latest' }

if (-not $Tag)           { $Tag = $DefaultTag }
if ($Port -le 0)         { $Port = if ($env:CORTEX_PORT) { [int]$env:CORTEX_PORT } else { 8765 } }
if ($GpuIndex -lt 0)     { $GpuIndex = if ($env:CORTEX_GPU_INDEX) { [int]$env:CORTEX_GPU_INDEX } else { 0 } }
if (-not $ContainerName) { $ContainerName = 'cortex-webapp' }
if (-not $CacheDir)      { $CacheDir = Join-Path $HOME '.cortex\cache' }
$CortexOffline = if ($Offline -or $env:CORTEX_OFFLINE -eq '1') { '1' } else { '0' }
$Image = "ghcr.io/alexiosbluffmara/cortex-webapp:$Tag"
$GemmaFastModel = if ($env:GEMMA_FAST_MODEL) { $env:GEMMA_FAST_MODEL } else { 'gemma4:e4b' }
$TribeRepo = if ($env:TRIBE_HF_REPO) { $env:TRIBE_HF_REPO } else { 'facebook/tribe-v2' }
$HealthTimeout = if ($env:HEALTH_TIMEOUT_S) { [int]$env:HEALTH_TIMEOUT_S } else { 180 }

# ─── pretty output ───────────────────────────────────────────────────────────
function Write-Step($msg) { Write-Host "[cortex] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[cortex] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[cortex] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[cortex] $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "+-- Cortex bootstrap (PowerShell) --------------------------+" -ForegroundColor White
Write-Host "| Brain Cinema * TRIBE v2 + Gemma 4                         |" -ForegroundColor White
Write-Host "| Alexios Bluff Mara LLC x Illinois State University        |" -ForegroundColor White
Write-Host "+-----------------------------------------------------------+" -ForegroundColor White
Write-Host ""

# ─── Step 1: required commands ──────────────────────────────────────────────
function Require-Cmd($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    Write-Fail "Required command not found: $name. Install it and re-run."
  }
}
Require-Cmd 'docker'
Require-Cmd 'nvidia-smi'

# ─── Step 2: GPU detection ──────────────────────────────────────────────────
Write-Step "Querying GPU $GpuIndex..."
$nvOut = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits -i $GpuIndex 2>$null
if ($LASTEXITCODE -ne 0 -or -not $nvOut) {
  Write-Fail "nvidia-smi failed for GPU index $GpuIndex. Is the driver installed?"
}
$parts = $nvOut.Split(',') | ForEach-Object { $_.Trim() }
$GpuName = $parts[0]
$VramMiB = [int]$parts[1]
Write-Step ("GPU: {0} ({1} MiB VRAM)" -f $GpuName, $VramMiB)

if ($VramMiB -lt 24000) {
  Write-Fail "GPU has only $VramMiB MiB VRAM. Cortex needs >=24 GB. See https://alexiosbluffmara.github.io/cortex/#hardware for supported tiers."
} elseif ($VramMiB -lt 48000) {
  $CortexMode = 'swap'
  Write-Step "Selected execution mode: swap (TRIBE <-> Gemma 4 sequential, ~10 s per swap)"
} else {
  $CortexMode = 'both-resident'
  Write-Step "Selected execution mode: both-resident (no swap)"
}

# ─── Step 3: Docker daemon reachable ────────────────────────────────────────
& docker info 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Fail "Docker is installed but the daemon is not reachable. Start Docker Desktop and re-run."
}

# ─── Step 4: pull image ─────────────────────────────────────────────────────
if ($CortexOffline -eq '1') {
  Write-Step "CORTEX_OFFLINE=1 - skipping image pull. Verifying local image..."
  & docker image inspect $Image 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Fail "Image $Image not present locally. Either unset offline mode or 'docker load' the saved tarball first."
  }
  Write-Ok "Local image present: $Image"
} else {
  Write-Step "Pulling $Image from GHCR (public, no login required)..."
  & docker pull $Image
  if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to pull $Image." }
  Write-Ok "Image pulled."
}

# ─── Step 5: TRIBE v2 weights ───────────────────────────────────────────────
$TribeDir = Join-Path $CacheDir 'tribev2'
New-Item -ItemType Directory -Force -Path $TribeDir | Out-Null

if ($env:HF_HUB_OFFLINE -eq '1') {
  if (-not (Get-ChildItem $TribeDir -Force -ErrorAction SilentlyContinue)) {
    Write-Warn2 "HF_HUB_OFFLINE=1 but $TribeDir is empty. First scan will fail."
  } else {
    Write-Ok "HF_HUB_OFFLINE=1 - using existing TRIBE cache at $TribeDir"
  }
} elseif ($CortexOffline -eq '1') {
  Write-Step "CORTEX_OFFLINE=1 - skipping TRIBE v2 download."
  if (-not (Get-ChildItem $TribeDir -Force -ErrorAction SilentlyContinue)) {
    Write-Warn2 "Cache directory is empty. First scan will fail."
  }
} else {
  if (-not $env:HF_TOKEN) {
    Write-Warn2 "HF_TOKEN not set. TRIBE v2 is gated (CC-BY-NC 4.0, Meta)."
    Write-Warn2 "Get a token: https://huggingface.co/settings/tokens"
    Write-Warn2 "Accept model terms: https://huggingface.co/$TribeRepo"
    $tokenSecure = Read-Host -AsSecureString -Prompt "Paste HF token (input hidden)"
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure)
    $env:HF_TOKEN = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    if (-not $env:HF_TOKEN) { Write-Fail "No token provided." }
  }

  Write-Step "Fetching TRIBE v2 weights into $TribeDir..."
  $hfCli = Get-Command 'huggingface-cli' -ErrorAction SilentlyContinue
  $py    = Get-Command 'python' -ErrorAction SilentlyContinue
  if ($hfCli) {
    & huggingface-cli download $TribeRepo --local-dir $TribeDir --local-dir-use-symlinks False
    if ($LASTEXITCODE -ne 0) { Write-Fail "huggingface-cli download failed." }
  } elseif ($py) {
    $pyScript = @"
import os
try:
    from huggingface_hub import snapshot_download
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'huggingface_hub>=0.24'])
    from huggingface_hub import snapshot_download
snapshot_download(repo_id='$TribeRepo', local_dir=r'$TribeDir',
                  local_dir_use_symlinks=False, token=os.environ['HF_TOKEN'])
"@
    $pyScript | & python -
    if ($LASTEXITCODE -ne 0) { Write-Fail "Python HF download failed." }
  } else {
    Write-Fail "Neither huggingface-cli nor python is on PATH."
  }
  Write-Ok "TRIBE v2 weights cached at $TribeDir"
}

# ─── Step 6: Gemma 4 via Ollama ─────────────────────────────────────────────
if ($CortexOffline -eq '1') {
  Write-Step "CORTEX_OFFLINE=1 - skipping Ollama pull."
} else {
  $ollama = Get-Command 'ollama' -ErrorAction SilentlyContinue
  if (-not $ollama) {
    Write-Warn2 "Ollama not found. Install: https://ollama.com/download/windows then re-run."
  } else {
    try {
      $resp = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
      if ($resp.StatusCode -ne 200) { throw "non-200" }
      Write-Step "Pulling Ollama model $GemmaFastModel..."
      & ollama pull $GemmaFastModel
      if ($LASTEXITCODE -ne 0) { Write-Warn2 "Ollama pull failed; retry after launch." }
    } catch {
      Write-Warn2 "Ollama daemon not running on :11434. Start it (system tray icon or 'ollama serve') and re-run for Gemma support."
    }
  }
}

# ─── Step 7: launch container ───────────────────────────────────────────────
$existing = & docker ps -a --format '{{.Names}}'
if ($existing -contains $ContainerName) {
  Write-Step "Removing existing container $ContainerName..."
  & docker rm -f $ContainerName 1>$null
}

$ollamaUrl = 'http://host.docker.internal:11434'
$hfOfflineFlag = if ($env:HF_HUB_OFFLINE) { $env:HF_HUB_OFFLINE } else { '0' }
$dockerArgs = @(
  'run','-d',
  '--name', $ContainerName,
  '--gpus', "device=$GpuIndex",
  '--restart','unless-stopped',
  '-v', "${CacheDir}:/root/.cortex/cache",
  '-e', "CORTEX_MODE=$CortexMode",
  '-e', 'CORTEX_CACHE_DIR=/root/.cortex/cache',
  '-e', "OLLAMA_URL=$ollamaUrl",
  '-e', "GEMMA_FAST_MODEL=$GemmaFastModel",
  '-e', 'WEBAPP_HOST=0.0.0.0',
  '-e', "WEBAPP_PORT=$Port",
  '-e', "HF_HUB_OFFLINE=$hfOfflineFlag",
  '-e', "CORTEX_OFFLINE=$CortexOffline",
  '-p', "${Port}:${Port}",
  '--add-host=host.docker.internal:host-gateway',
  $Image
)
Write-Step "Launching $ContainerName on :$Port (mode=$CortexMode)..."
& docker @dockerArgs 1>$null
if ($LASTEXITCODE -ne 0) { Write-Fail "docker run failed." }
Write-Ok "Container started."

# ─── Step 8: wait for /api/health ───────────────────────────────────────────
$healthUrl = "http://127.0.0.1:$Port/api/health"
Write-Step "Waiting for $healthUrl (up to $HealthTimeout s)..."
$deadline = (Get-Date).AddSeconds($HealthTimeout)
$healthy = $false
while ((Get-Date) -lt $deadline) {
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 5
    if ($r.StatusCode -eq 200) { $healthy = $true; break }
  } catch { Start-Sleep -Seconds 3 }
}
if (-not $healthy) {
  & docker logs --tail 80 $ContainerName
  Write-Fail "Health check did not pass within $HealthTimeout s. Logs above."
}
Write-Ok "Cortex is healthy."

# ─── Step 9: summary ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Cortex is up." -ForegroundColor Green
Write-Host ""
Write-Host "  Open:        http://localhost:$Port/" -ForegroundColor White
Write-Host "  Health:      http://localhost:$Port/api/health"
Write-Host "  Container:   $ContainerName   (image: $Image)"
Write-Host ("  GPU:         {0} ({1} MiB) - mode={2}" -f $GpuName, $VramMiB, $CortexMode)
Write-Host "  Cache:       $CacheDir"
Write-Host ""
Write-Host "  Stop:        docker stop $ContainerName"
Write-Host "  Logs:        docker logs -f $ContainerName"
Write-Host ""
Write-Host "Brain Cinema: https://alexiosbluffmara.github.io/cortex/" -ForegroundColor DarkGray
Write-Host "Alexios Bluff Mara LLC x Illinois State University" -ForegroundColor DarkGray
Write-Host ""
