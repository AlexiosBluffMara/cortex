<#
.SYNOPSIS
  Cortex one-command cloud replicator (PowerShell variant).

.DESCRIPTION
  Spins up the full Cortex GCP infrastructure stack so a forker can run one
  command and end up with their own working copy. Same logic as
  scripts/replicate.sh; idiomatic PowerShell.

  Idempotent: safe to re-run. Pauses for one-time interactive bootstraps
  (Firebase ToS, Cloudflare Tunnel cert, HF token entry).

.PARAMETER ProjectId
  GCP project ID to use or create.

.PARAMETER GhUser
  GitHub username (owner of your fork of cortex).

.PARAMETER Region
  GCP region (default us-central1).

.PARAMETER HfToken
  HuggingFace token for TRIBE v2 weights. Optional.

.PARAMETER NonInteractive
  Skip all prompts.

.EXAMPLE
  .\scripts\replicate.ps1 -ProjectId my-cortex -GhUser myuser

.NOTES
  License: Apache-2.0. See docs/REPLICATE.md.
#>
[CmdletBinding()]
param(
  [string]$ProjectId,
  [string]$GhUser,
  [string]$Region = "us-central1",
  [string]$HfToken,
  [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function Pass($m) { Write-Host "[PASS] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Info($m) { Write-Host "[INFO] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[FAIL] $m" -ForegroundColor Red }
function Step($m) { Write-Host ""; Write-Host "── $m ──" -ForegroundColor White -BackgroundColor DarkBlue }

function Test-Cli($name) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  return [bool]$cmd
}

function Read-Default($prompt, $default) {
  if ($NonInteractive) { return $default }
  if ($default) {
    $r = Read-Host "$prompt [$default]"
    if ([string]::IsNullOrWhiteSpace($r)) { return $default } else { return $r }
  } else {
    return Read-Host $prompt
  }
}

function Read-Secret($prompt) {
  $sec = Read-Host -AsSecureString "$prompt (input hidden, blank to skip)"
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Pause-Enter($msg) {
  if ($NonInteractive) { return }
  Read-Host "$msg Press Enter to continue (Ctrl-C to abort)"
}

function Invoke-Quiet {
  # Run a native command and swallow output; return $true on exit 0.
  param([string]$Exe, [string[]]$Args)
  $null = & $Exe @Args 2>&1
  return ($LASTEXITCODE -eq 0)
}

# ─── Step 0: Preflight ───────────────────────────────────────────────────────
Step "Step 0 / 10  ·  Preflight: required CLIs"

$missing = @()
foreach ($tool in @(
  @{ Name="gcloud";      Hint="https://cloud.google.com/sdk/docs/install" },
  @{ Name="gh";          Hint="winget install GitHub.cli  or  https://cli.github.com/" },
  @{ Name="firebase";    Hint="npm install -g firebase-tools" },
  @{ Name="cloudflared"; Hint="winget install Cloudflare.cloudflared (optional)" }
)) {
  if (Test-Cli $tool.Name) { Pass "$($tool.Name) found" }
  else { Fail "$($tool.Name) not found — install: $($tool.Hint)"; $missing += $tool.Name }
}
if ($missing.Count -gt 0 -and $missing -notcontains "cloudflared") {
  Fail "Install the missing tools above, then re-run."
  exit 1
}

# ─── Step 0b: Auth ───────────────────────────────────────────────────────────
Step "Step 0b / 10  ·  Auth"

$gcloudAccount = (& gcloud auth list --format="value(account)" 2>$null) | Select-Object -First 1
if (-not $gcloudAccount) {
  Info "Running gcloud auth login..."
  & gcloud auth login
} else { Pass "gcloud authed as: $gcloudAccount" }

if (-not (Invoke-Quiet "gcloud" @("auth","application-default","print-access-token"))) {
  Info "Running gcloud auth application-default login..."
  & gcloud auth application-default login
} else { Pass "gcloud ADC ready" }

if (-not (Invoke-Quiet "gh" @("auth","status"))) {
  Info "Running gh auth login..."
  & gh auth login
} else {
  $ghUser = (& gh api user -q .login 2>$null)
  Pass "gh authed as: $ghUser"
}

# ─── Step 0c: Inputs ─────────────────────────────────────────────────────────
Step "Step 0c / 10  ·  Inputs"

if (-not $ProjectId) { $ProjectId = Read-Default "GCP project ID to use or create" "" }
if (-not $ProjectId) { Fail "ProjectId required."; exit 2 }

if (-not $GhUser) {
  $defaultUser = (& gh api user -q .login 2>$null)
  $GhUser = Read-Default "GitHub username (owner of your fork of cortex)" $defaultUser
}
if (-not $GhUser) { Fail "GhUser required."; exit 2 }

$Region = Read-Default "GCP region" $Region

$GhRepo        = "$GhUser/cortex"
$SaName        = "github-actions-deploy"
$SaEmail       = "$SaName@$ProjectId.iam.gserviceaccount.com"
$PoolName      = "github-actions-pool"
$ProviderName  = "github-provider"
$ArRepoName    = "cortex"
$BucketLegacy  = "cortex-public-scans"
$BucketSuffix  = "cortex-public-scans-$ProjectId"
$GcsBucket     = $BucketSuffix

Info "Project:          $ProjectId"
Info "GitHub repo:      $GhRepo"
Info "Region:           $Region"
Info "Service account:  $SaEmail"
Pause-Enter "Ready to provision."

# ─── Step 1: Project ─────────────────────────────────────────────────────────
Step "Step 1 / 10  ·  GCP project"

if (Invoke-Quiet "gcloud" @("projects","describe",$ProjectId)) {
  Pass "Project $ProjectId already exists — using it."
} else {
  Info "Creating project $ProjectId..."
  & gcloud projects create $ProjectId --name="Cortex ($ProjectId)" --quiet
  Pass "Created."
  Warn "If billing is not linked, the next steps will fail with 'billing not enabled'."
  Warn "Link billing: https://console.cloud.google.com/billing/linkedaccount?project=$ProjectId"
  Pause-Enter "Link billing now if needed."
}
& gcloud config set project $ProjectId --quiet | Out-Null
Pass "Active project = $ProjectId"

# ─── Step 2: Enable APIs ─────────────────────────────────────────────────────
Step "Step 2 / 10  ·  Enable APIs"
$apis = @(
  "iamcredentials.googleapis.com",
  "cloudresourcemanager.googleapis.com",
  "run.googleapis.com",
  "storage.googleapis.com",
  "secretmanager.googleapis.com",
  "cloudbuild.googleapis.com",
  "firestore.googleapis.com",
  "firebase.googleapis.com",
  "artifactregistry.googleapis.com"
)
Info "Enabling: $($apis -join ' ')"
& gcloud services enable @apis --project=$ProjectId --quiet
Pass "APIs enabled."

# ─── Step 3: WIF + SA + roles ────────────────────────────────────────────────
Step "Step 3 / 10  ·  WIF + service account + IAM roles"

if (-not (Invoke-Quiet "gcloud" @("iam","workload-identity-pools","describe",$PoolName,"--project=$ProjectId","--location=global"))) {
  Info "Creating WIF pool $PoolName..."
  & gcloud iam workload-identity-pools create $PoolName --project=$ProjectId --location=global --display-name="GitHub Actions pool" --quiet
  Pass "Pool created."
} else { Pass "Pool $PoolName already exists." }

$poolId = (& gcloud iam workload-identity-pools describe $PoolName --project=$ProjectId --location=global --format="value(name)").Trim()

if (-not (Invoke-Quiet "gcloud" @("iam","workload-identity-pools","providers","describe",$ProviderName,"--project=$ProjectId","--location=global","--workload-identity-pool=$PoolName"))) {
  Info "Creating OIDC provider (scoped to repo_owner=$GhUser)..."
  & gcloud iam workload-identity-pools providers create-oidc $ProviderName `
    --project=$ProjectId --location=global `
    --workload-identity-pool=$PoolName `
    --display-name="GitHub Actions OIDC" `
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" `
    --attribute-condition="assertion.repository_owner=='$GhUser'" `
    --issuer-uri="https://token.actions.githubusercontent.com" `
    --quiet
  Pass "Provider created."
} else { Pass "Provider $ProviderName already exists." }

if (-not (Invoke-Quiet "gcloud" @("iam","service-accounts","describe",$SaEmail,"--project=$ProjectId"))) {
  Info "Creating service account $SaName..."
  & gcloud iam service-accounts create $SaName --project=$ProjectId --display-name="GitHub Actions CI/CD deploy" --quiet
  Pass "Service account created."
} else { Pass "Service account $SaEmail already exists." }

$roles = @(
  "roles/run.admin",
  "roles/storage.admin",
  "roles/secretmanager.secretAccessor",
  "roles/cloudbuild.builds.editor",
  "roles/iam.serviceAccountUser",
  "roles/datastore.user",
  "roles/firebase.admin",
  "roles/artifactregistry.admin"
)
Info "Granting $($roles.Count) roles..."
foreach ($r in $roles) {
  & gcloud projects add-iam-policy-binding $ProjectId `
    --member="serviceAccount:$SaEmail" --role=$r --condition=None --quiet | Out-Null
}
Pass "Roles bound."

$principal = "principalSet://iam.googleapis.com/$poolId/attribute.repository/$GhRepo"
Info "Binding WIF principal -> SA (repo-scoped to $GhRepo)..."
& gcloud iam service-accounts add-iam-policy-binding $SaEmail `
  --project=$ProjectId --role="roles/iam.workloadIdentityUser" `
  --member=$principal --quiet | Out-Null
Pass "WIF binding set."

$providerFull = (& gcloud iam workload-identity-pools providers describe $ProviderName `
  --project=$ProjectId --location=global `
  --workload-identity-pool=$PoolName --format="value(name)").Trim()
Pass "Provider resource name: $providerFull"

# ─── Step 4: Artifact Registry ───────────────────────────────────────────────
Step "Step 4 / 10  ·  Artifact Registry"
if (Invoke-Quiet "gcloud" @("artifacts","repositories","describe",$ArRepoName,"--location=$Region","--project=$ProjectId")) {
  Pass "AR repo $ArRepoName exists in $Region."
} else {
  Info "Creating AR repo $ArRepoName..."
  & gcloud artifacts repositories create $ArRepoName `
    --repository-format=docker --location=$Region --project=$ProjectId `
    --description="Cortex container images" --quiet
  Pass "AR repo created."
}

# ─── Step 5: Secret Manager ──────────────────────────────────────────────────
Step "Step 5 / 10  ·  Secret Manager"

function Ensure-Secret($name, $placeholder) {
  if (Invoke-Quiet "gcloud" @("secrets","describe",$name,"--project=$ProjectId")) {
    Pass "Secret $name exists — leaving its value alone."
  } else {
    Info "Creating secret $name (placeholder)..."
    $tmp = [IO.Path]::GetTempFileName()
    try {
      [IO.File]::WriteAllText($tmp, $placeholder)
      & gcloud secrets create $name --data-file=$tmp --project=$ProjectId --replication-policy=automatic --quiet
      Pass "Secret $name created."
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
  }
}

Ensure-Secret "gcp-inference-token" "REPLACE_WITH_IDENTITY_TOKEN"

if (-not $HfToken -and -not $NonInteractive) {
  $HfToken = Read-Secret "HuggingFace token for TRIBE v2 weights (hf_...)"
}

$hfExists = Invoke-Quiet "gcloud" @("secrets","describe","hf-token","--project=$ProjectId")
if ($hfExists) {
  if ($HfToken) {
    Info "Adding new version to hf-token..."
    $tmp = [IO.Path]::GetTempFileName()
    try {
      [IO.File]::WriteAllText($tmp, $HfToken)
      & gcloud secrets versions add hf-token --data-file=$tmp --project=$ProjectId --quiet
      Pass "hf-token updated."
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
  } else { Pass "hf-token exists — leaving value alone." }
} else {
  $val = if ($HfToken) { $HfToken } else { "REPLACE_WITH_HF_TOKEN" }
  $tmp = [IO.Path]::GetTempFileName()
  try {
    [IO.File]::WriteAllText($tmp, $val)
    & gcloud secrets create hf-token --data-file=$tmp --project=$ProjectId --replication-policy=automatic --quiet
    if ($HfToken) { Pass "hf-token created." } else { Pass "hf-token placeholder created — populate later." }
  } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

# ─── Step 6: GCS bucket ──────────────────────────────────────────────────────
Step "Step 6 / 10  ·  GCS bucket"

if (Invoke-Quiet "gcloud" @("storage","buckets","describe","gs://$BucketLegacy","--project=$ProjectId")) {
  $GcsBucket = $BucketLegacy
  Pass "Bucket gs://$GcsBucket exists in this project."
} elseif (Invoke-Quiet "gcloud" @("storage","buckets","describe","gs://$BucketSuffix","--project=$ProjectId")) {
  Pass "Bucket gs://$BucketSuffix exists."
} else {
  Info "Trying to create gs://$BucketLegacy (preferred name)..."
  if (Invoke-Quiet "gcloud" @("storage","buckets","create","gs://$BucketLegacy","--project=$ProjectId","--location=$Region","--uniform-bucket-level-access","--quiet")) {
    $GcsBucket = $BucketLegacy
    Pass "Created gs://$GcsBucket."
  } else {
    Warn "Legacy name taken — falling back to $BucketSuffix."
    & gcloud storage buckets create "gs://$BucketSuffix" --project=$ProjectId --location=$Region --uniform-bucket-level-access --quiet
    Pass "Created gs://$BucketSuffix."
  }
}

$corsFile = Join-Path $PSScriptRoot "..\gcp\bucket-cors.json"
if (Test-Path $corsFile) {
  Info "Applying CORS from $corsFile..."
  & gcloud storage buckets update "gs://$GcsBucket" --cors-file=$corsFile --quiet
  Pass "CORS applied."
} else {
  Warn "gcp/bucket-cors.json not found — skipping CORS."
}

# ─── Step 7: Firestore ───────────────────────────────────────────────────────
Step "Step 7 / 10  ·  Firestore (Native mode)"
if (Invoke-Quiet "gcloud" @("firestore","databases","describe","--database=(default)","--project=$ProjectId")) {
  Pass "Firestore (default) exists."
} else {
  Info "Creating Firestore (default) in $Region..."
  & gcloud firestore databases create --location=$Region --type=firestore-native --project=$ProjectId --quiet
  Pass "Firestore created."
}

# ─── Step 8: Firebase Hosting bootstrap ──────────────────────────────────────
Step "Step 8 / 10  ·  Firebase project link + Hosting site"
Write-Host ""
Write-Host "One-time manual step required." -ForegroundColor Yellow
Write-Host @"

Firebase requires accepting the Terms of Service in the web console — there
is no API path. Do this once:

  1. Open: https://console.firebase.google.com/
  2. Click 'Add project', choose '$ProjectId' from the existing GCP list.
  3. Accept the ToS, finish the wizard.

After that, this script runs 'firebase projects:addfirebase' and
'hosting:sites:create' — both idempotent.
"@
Pause-Enter "Hit Enter once Firebase is linked to $ProjectId."

Info "Verifying Firebase link..."
$addLog = "$env:TEMP\cortex-replicate-addfb.log"
& firebase projects:addfirebase $ProjectId --non-interactive 2>&1 | Tee-Object -FilePath $addLog
if ((Get-Content $addLog -Raw) -match "already.*Firebase|already exists|409") {
  Pass "Project is already a Firebase project."
} else {
  Pass "firebase projects:addfirebase completed (or check log)."
}

Info "Ensuring Hosting site '$ProjectId' exists..."
$siteLog = "$env:TEMP\cortex-replicate-site.log"
& firebase hosting:sites:create $ProjectId --project $ProjectId --non-interactive 2>&1 | Tee-Object -FilePath $siteLog
if ((Get-Content $siteLog -Raw) -match "already exists|already taken|409") {
  Pass "Hosting site already exists."
} else {
  Pass "Hosting site created."
}

Write-Host ""
Write-Host "Cloudflare Tunnel (optional)" -ForegroundColor Yellow
Write-Host @"
This step cannot be automated — 'cloudflared tunnel login' opens a browser.
Run once on your dev machine:

  cloudflared tunnel login
  cloudflared tunnel create cortex
  cloudflared tunnel route dns cortex cortex.yourdomain.com

Then put the tunnel HTTPS URL into GitHub secret CLOUDFLARE_TUNNEL_URL.
"@

# ─── Step 9: GitHub Secrets ──────────────────────────────────────────────────
Step "Step 9 / 10  ·  GitHub Secrets on $GhRepo"

if (-not (Invoke-Quiet "gh" @("repo","view",$GhRepo))) {
  Warn "Repo $GhRepo not found / not accessible."
  Warn "Fork https://github.com/AlexiosBluffMara/cortex into $GhUser/cortex first, then re-run."
  Write-Host ""
  Write-Host "  GCP_WIF_PROVIDER : $providerFull"
  Write-Host "  GCP_SA_EMAIL     : $SaEmail"
  Write-Host ""
} else {
  function Set-GhSecret($name, $value) {
    if ([string]::IsNullOrEmpty($value)) { Warn "Skipping $name (empty)."; return }
    $value | & gh secret set $name --repo $GhRepo --body -
    if ($LASTEXITCODE -ne 0) { $value | & gh secret set $name --repo $GhRepo }
    Pass "Set secret $name."
  }
  Set-GhSecret "GCP_WIF_PROVIDER" $providerFull
  Set-GhSecret "GCP_SA_EMAIL" $SaEmail

  if (-not $NonInteractive) {
    $ollama = Read-Default "OLLAMA_URL (blank to skip)" ""
    $cf     = Read-Default "CLOUDFLARE_TUNNEL_URL (blank to skip)" ""
    $gem    = Read-Default "GEMINI_API_KEY (blank to skip)" ""
    if ($ollama) { Set-GhSecret "OLLAMA_URL" $ollama }
    if ($cf)     { Set-GhSecret "CLOUDFLARE_TUNNEL_URL" $cf }
    if ($gem)    { Set-GhSecret "GEMINI_API_KEY" $gem }
  }

  Warn "GCP_INFERENCE_ENDPOINT is set AFTER the first tribe-worker deploy."
  Warn "Run: gh secret set GCP_INFERENCE_ENDPOINT --repo $GhRepo --body https://cortex-tribe-worker-XXXX-uc.a.run.app"
}

# ─── Step 10: Summary ────────────────────────────────────────────────────────
Step "Step 10 / 10  ·  Summary"
Write-Host @"

Cortex infrastructure is provisioned.

  GCP project       : $ProjectId
  Region            : $Region
  WIF provider      : $providerFull
  Deploy SA         : $SaEmail
  Artifact Registry : $Region-docker.pkg.dev/$ProjectId/$ArRepoName
  GCS bucket        : gs://$GcsBucket
  Firestore         : (default) — Native mode in $Region
  Firebase Hosting  : site '$ProjectId' linked
  GitHub repo       : $GhRepo

Next steps:
  1. Push to main on your fork — the deploy workflow will run.
       git remote set-url origin https://github.com/$GhRepo.git
       git push origin main
  2. After the first tribe-worker deploy, set GCP_INFERENCE_ENDPOINT:
       gh secret set GCP_INFERENCE_ENDPOINT --repo $GhRepo --body <URL>
  3. Watch the deploy:
       gh run watch --repo $GhRepo
  4. Open your hosted site:
       https://$ProjectId.web.app

Rollback: .\scripts\replicate-cleanup.sh -ProjectId $ProjectId  (use bash via WSL/Git-Bash)

Docs: docs/REPLICATE.md
"@ -ForegroundColor Green

Pass "Done."
