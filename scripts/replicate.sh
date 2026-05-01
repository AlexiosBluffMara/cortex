#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Cortex one-command cloud replicator
#
# Spins up the full Cortex GCP infrastructure stack (APIs, WIF for GitHub
# Actions, Artifact Registry, Secret Manager, GCS bucket, Firestore, Firebase
# Hosting link, GitHub Secrets) so a forker can run `bash scripts/replicate.sh`
# and end up with their own working copy.
#
# Idempotent: safe to re-run. Pauses for one-time interactive bootstraps
# (Firebase ToS, Cloudflare Tunnel cert, HF token entry).
#
# Usage:
#   bash scripts/replicate.sh                          # interactive
#   bash scripts/replicate.sh --project my-gcp-id      # non-interactive project
#   bash scripts/replicate.sh --project my-id --gh-user myuser --region us-central1
#
# Apache-2.0 — same project license. See docs/REPLICATE.md for the long form.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ─── Pretty output ───────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_RED=$'\033[31m'
else
  C_RESET=''; C_BOLD=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_RED=''
fi

pass()  { echo "${C_GREEN}[PASS]${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}[WARN]${C_RESET} $*"; }
info()  { echo "${C_BLUE}[INFO]${C_RESET} $*"; }
fail()  { echo "${C_RED}[FAIL]${C_RESET} $*" >&2; }
step()  { echo; echo "${C_BOLD}── $* ──${C_RESET}"; }

trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "Aborted on line $LINENO (exit $rc). Re-run is safe — script is idempotent."; fi' ERR

# ─── Defaults / args ─────────────────────────────────────────────────────────
PROJECT_ID=""
GH_USER=""
REGION="us-central1"
HF_TOKEN=""
NONINTERACTIVE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --gh-user) GH_USER="$2"; shift 2 ;;
    --region)  REGION="$2"; shift 2 ;;
    --hf-token) HF_TOKEN="$2"; shift 2 ;;
    --yes|-y) NONINTERACTIVE=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    *) fail "Unknown arg: $1"; exit 2 ;;
  esac
done

prompt() {
  # prompt VAR_NAME "Question text" "default"
  local __var="$1" __q="$2" __def="${3:-}" __reply
  if [[ $NONINTERACTIVE -eq 1 ]]; then
    printf -v "$__var" '%s' "$__def"
    return 0
  fi
  if [[ -n "$__def" ]]; then
    read -r -p "$__q [$__def]: " __reply
    __reply="${__reply:-$__def}"
  else
    read -r -p "$__q: " __reply
  fi
  printf -v "$__var" '%s' "$__reply"
}

prompt_secret() {
  local __var="$1" __q="$2" __reply
  read -r -s -p "$__q (input hidden, blank to skip): " __reply
  echo
  printf -v "$__var" '%s' "$__reply"
}

pause_enter() {
  if [[ $NONINTERACTIVE -eq 1 ]]; then return 0; fi
  read -r -p "$* Press Enter to continue, Ctrl-C to abort... " _
}

# ─── Step 0: Preflight ───────────────────────────────────────────────────────
step "Step 0 / 10  ·  Preflight: required CLIs"

need_install=0
check_cli() {
  local name="$1" install_hint="$2"
  if command -v "$name" &>/dev/null; then
    pass "$name found ($(command -v "$name"))"
  else
    fail "$name not found — install: $install_hint"
    need_install=1
  fi
}

check_cli gcloud    "https://cloud.google.com/sdk/docs/install   (or: brew install --cask google-cloud-sdk)"
check_cli gh        "https://cli.github.com/                       (or: brew install gh, winget install GitHub.cli)"
check_cli firebase  "npm install -g firebase-tools"
check_cli cloudflared "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/  (optional — only if you want a custom domain)"

if [[ $need_install -eq 1 ]]; then
  fail "Install the missing tools above, then re-run this script."
  exit 1
fi

# ─── Step 0b: Auth ───────────────────────────────────────────────────────────
step "Step 0b / 10  ·  Auth: gcloud + gh"

if ! gcloud auth list --format="value(account)" 2>/dev/null | grep -q .; then
  info "No gcloud account active — running 'gcloud auth login'..."
  gcloud auth login
else
  pass "gcloud authed as: $(gcloud config get-value account 2>/dev/null)"
fi

# Application-default credentials are useful for firebase-tools and other SDKs.
if ! gcloud auth application-default print-access-token &>/dev/null; then
  info "No application-default credentials — running 'gcloud auth application-default login'..."
  gcloud auth application-default login
else
  pass "gcloud ADC ready"
fi

if ! gh auth status &>/dev/null; then
  info "gh not authed — running 'gh auth login'..."
  gh auth login
else
  pass "gh authed as: $(gh api user -q .login 2>/dev/null || echo unknown)"
fi

# ─── Step 0c: Inputs ─────────────────────────────────────────────────────────
step "Step 0c / 10  ·  Inputs"

if [[ -z "$PROJECT_ID" ]]; then
  prompt PROJECT_ID "GCP project ID to use or create (lowercase, 6-30 chars, hyphens ok)" ""
fi
[[ -z "$PROJECT_ID" ]] && { fail "PROJECT_ID required."; exit 2; }

if [[ -z "$GH_USER" ]]; then
  GH_DEFAULT="$(gh api user -q .login 2>/dev/null || echo "")"
  prompt GH_USER "GitHub username (owner of your fork of cortex)" "$GH_DEFAULT"
fi
[[ -z "$GH_USER" ]] && { fail "GH_USER required."; exit 2; }

prompt REGION "GCP region" "$REGION"

GH_REPO="${GH_USER}/cortex"
SA_NAME="github-actions-deploy"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
POOL_NAME="github-actions-pool"
PROVIDER_NAME="github-provider"
AR_REPO_NAME="cortex"
GCS_BUCKET="cortex-public-scans-${PROJECT_ID}"   # bucket names are global; suffix to avoid collisions
SCAN_BUCKET_LEGACY="cortex-public-scans"          # original name, in case it exists

info "Project:          ${C_BOLD}${PROJECT_ID}${C_RESET}"
info "GitHub repo:      ${C_BOLD}${GH_REPO}${C_RESET}"
info "Region:           ${C_BOLD}${REGION}${C_RESET}"
info "Service account:  ${C_BOLD}${SA_EMAIL}${C_RESET}"
info "Bucket:           ${C_BOLD}${GCS_BUCKET}${C_RESET}"
pause_enter "Ready to provision."

# ─── Step 1: Project ─────────────────────────────────────────────────────────
step "Step 1 / 10  ·  GCP project"

if gcloud projects describe "$PROJECT_ID" &>/dev/null; then
  pass "Project $PROJECT_ID already exists — using it."
else
  info "Creating project $PROJECT_ID..."
  gcloud projects create "$PROJECT_ID" --name="Cortex ($PROJECT_ID)" --quiet
  pass "Created project $PROJECT_ID."
  warn "If billing is not linked, the next steps will fail with 'billing not enabled'."
  warn "Link billing at: https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT_ID"
  pause_enter "Link billing now if needed, then continue."
fi

gcloud config set project "$PROJECT_ID" --quiet
pass "Active project = $PROJECT_ID"

# ─── Step 2: Enable APIs ─────────────────────────────────────────────────────
step "Step 2 / 10  ·  Enable APIs"

APIS=(
  iamcredentials.googleapis.com
  cloudresourcemanager.googleapis.com
  run.googleapis.com
  storage.googleapis.com
  secretmanager.googleapis.com
  cloudbuild.googleapis.com
  firestore.googleapis.com
  firebase.googleapis.com
  artifactregistry.googleapis.com
)

info "Enabling: ${APIS[*]}"
gcloud services enable "${APIS[@]}" --project="$PROJECT_ID" --quiet
pass "APIs enabled."

# ─── Step 3: WIF + SA + roles ────────────────────────────────────────────────
step "Step 3 / 10  ·  Workload Identity Federation + service account + IAM roles"

if ! gcloud iam workload-identity-pools describe "$POOL_NAME" \
    --project="$PROJECT_ID" --location=global &>/dev/null; then
  info "Creating WIF pool $POOL_NAME..."
  gcloud iam workload-identity-pools create "$POOL_NAME" \
    --project="$PROJECT_ID" --location=global \
    --display-name="GitHub Actions pool" --quiet
  pass "Pool created."
else
  pass "Pool $POOL_NAME already exists."
fi

POOL_ID=$(gcloud iam workload-identity-pools describe "$POOL_NAME" \
  --project="$PROJECT_ID" --location=global --format="value(name)")

if ! gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_NAME" &>/dev/null; then
  info "Creating OIDC provider $PROVIDER_NAME (scoped to repo_owner=$GH_USER)..."
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_NAME" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_NAME" \
    --display-name="GitHub Actions OIDC" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="assertion.repository_owner=='${GH_USER}'" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --quiet
  pass "Provider created."
else
  pass "Provider $PROVIDER_NAME already exists."
fi

if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  info "Creating service account $SA_NAME..."
  gcloud iam service-accounts create "$SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name="GitHub Actions CI/CD deploy" --quiet
  pass "Service account created."
else
  pass "Service account $SA_EMAIL already exists."
fi

ROLES=(
  roles/run.admin
  roles/storage.admin
  roles/secretmanager.secretAccessor
  roles/cloudbuild.builds.editor
  roles/iam.serviceAccountUser
  roles/datastore.user
  roles/firebase.admin
  roles/artifactregistry.admin
)

info "Granting ${#ROLES[@]} roles to $SA_EMAIL..."
for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$ROLE" \
    --condition=None --quiet >/dev/null
done
pass "IAM roles bound."

PRINCIPAL="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GH_REPO}"
info "Binding WIF principal $PRINCIPAL → $SA_EMAIL..."
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="$PRINCIPAL" \
  --quiet >/dev/null
pass "WIF binding set (repo-scoped to $GH_REPO)."

PROVIDER_FULL=$(gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
  --project="$PROJECT_ID" --location=global \
  --workload-identity-pool="$POOL_NAME" --format="value(name)")
pass "Provider resource name: $PROVIDER_FULL"

# ─── Step 4: Artifact Registry ───────────────────────────────────────────────
step "Step 4 / 10  ·  Artifact Registry repo"

if gcloud artifacts repositories describe "$AR_REPO_NAME" \
    --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  pass "Artifact Registry repo $AR_REPO_NAME exists in $REGION."
else
  info "Creating AR repo $AR_REPO_NAME in $REGION..."
  gcloud artifacts repositories create "$AR_REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --project="$PROJECT_ID" \
    --description="Cortex container images (webapp/worker/relay)" \
    --quiet
  pass "AR repo created."
fi

# ─── Step 5: Secret Manager ──────────────────────────────────────────────────
step "Step 5 / 10  ·  Secret Manager secrets"

ensure_secret() {
  local name="$1" placeholder="$2"
  if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
    pass "Secret $name exists — leaving its value alone."
  else
    info "Creating secret $name with placeholder (replace before deploy)..."
    printf '%s' "$placeholder" | gcloud secrets create "$name" \
      --data-file=- --project="$PROJECT_ID" --replication-policy=automatic --quiet
    pass "Secret $name created."
  fi
}

ensure_secret gcp-inference-token "REPLACE_WITH_IDENTITY_TOKEN"

if [[ -z "$HF_TOKEN" && $NONINTERACTIVE -eq 0 ]]; then
  prompt_secret HF_TOKEN "HuggingFace token for TRIBE v2 weights (hf_...)"
fi

if gcloud secrets describe hf-token --project="$PROJECT_ID" &>/dev/null; then
  if [[ -n "$HF_TOKEN" ]]; then
    info "hf-token exists — adding new version with provided token."
    printf '%s' "$HF_TOKEN" | gcloud secrets versions add hf-token \
      --data-file=- --project="$PROJECT_ID" --quiet
    pass "hf-token updated."
  else
    pass "hf-token exists — leaving its value alone."
  fi
else
  if [[ -n "$HF_TOKEN" ]]; then
    info "Creating hf-token..."
    printf '%s' "$HF_TOKEN" | gcloud secrets create hf-token \
      --data-file=- --project="$PROJECT_ID" --replication-policy=automatic --quiet
    pass "hf-token created."
  else
    info "Creating hf-token with placeholder — populate later via:"
    echo "  echo -n 'hf_xxx' | gcloud secrets versions add hf-token --data-file=- --project=$PROJECT_ID"
    printf '%s' "REPLACE_WITH_HF_TOKEN" | gcloud secrets create hf-token \
      --data-file=- --project="$PROJECT_ID" --replication-policy=automatic --quiet
    pass "hf-token placeholder created."
  fi
fi

# ─── Step 6: GCS bucket ──────────────────────────────────────────────────────
step "Step 6 / 10  ·  GCS bucket for scans + thumbnails"

# Prefer the legacy name if it exists in this project; otherwise create a project-suffixed one.
if gcloud storage buckets describe "gs://${SCAN_BUCKET_LEGACY}" --project="$PROJECT_ID" &>/dev/null; then
  GCS_BUCKET="$SCAN_BUCKET_LEGACY"
  pass "Bucket gs://$GCS_BUCKET already exists in this project."
elif gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="$PROJECT_ID" &>/dev/null; then
  pass "Bucket gs://$GCS_BUCKET already exists."
else
  info "Trying to create gs://$SCAN_BUCKET_LEGACY (preferred name)..."
  if gcloud storage buckets create "gs://$SCAN_BUCKET_LEGACY" \
        --project="$PROJECT_ID" --location="$REGION" --uniform-bucket-level-access \
        --quiet 2>/dev/null; then
    GCS_BUCKET="$SCAN_BUCKET_LEGACY"
    pass "Created gs://$GCS_BUCKET."
  else
    warn "Legacy name taken — falling back to $GCS_BUCKET."
    gcloud storage buckets create "gs://$GCS_BUCKET" \
      --project="$PROJECT_ID" --location="$REGION" --uniform-bucket-level-access --quiet
    pass "Created gs://$GCS_BUCKET."
  fi
fi

# Apply CORS so the browser can upload directly.
if [[ -f "$(dirname "$0")/../gcp/bucket-cors.json" ]]; then
  info "Applying CORS from gcp/bucket-cors.json..."
  gcloud storage buckets update "gs://$GCS_BUCKET" \
    --cors-file="$(dirname "$0")/../gcp/bucket-cors.json" --quiet || \
    warn "CORS update failed — apply manually if needed."
  pass "CORS applied."
else
  warn "gcp/bucket-cors.json not found — skipping CORS. Apply manually before public uploads."
fi

# ─── Step 7: Firestore ───────────────────────────────────────────────────────
step "Step 7 / 10  ·  Firestore database (Native mode)"

if gcloud firestore databases describe --database="(default)" --project="$PROJECT_ID" &>/dev/null; then
  pass "Firestore (default) database already exists."
else
  info "Creating Firestore (default) in $REGION (Native mode)..."
  gcloud firestore databases create \
    --location="$REGION" \
    --type=firestore-native \
    --project="$PROJECT_ID" --quiet
  pass "Firestore created."
fi

# ─── Step 8: Firebase Hosting bootstrap (manual ToS) ────────────────────────
step "Step 8 / 10  ·  Firebase project link + Hosting site"

cat <<EOF
${C_YELLOW}One-time manual step required.${C_RESET}

Firebase requires accepting the Terms of Service in the web console — there
is no API path. Do this once:

  1. Open: ${C_BOLD}https://console.firebase.google.com/${C_RESET}
  2. Click ${C_BOLD}Add project${C_RESET}, choose ${C_BOLD}${PROJECT_ID}${C_RESET} from the existing GCP list.
  3. Accept the ToS, finish the wizard. (Skip Google Analytics — not needed.)

After that, the script will run \`firebase projects:addfirebase\` and
\`hosting:sites:create\` to verify, both of which are idempotent.

EOF
pause_enter "Hit Enter once Firebase is linked to $PROJECT_ID."

info "Verifying Firebase link..."
if firebase projects:addfirebase "$PROJECT_ID" --non-interactive 2>&1 | tee /tmp/cortex-replicate-addfb.log; then
  pass "firebase projects:addfirebase completed."
else
  if grep -qE "already.*Firebase|already exists|409" /tmp/cortex-replicate-addfb.log; then
    pass "Project is already a Firebase project — good."
  else
    warn "addfirebase reported an error — see /tmp/cortex-replicate-addfb.log."
    warn "If the ToS step is not yet done in the console, retry this script."
  fi
fi

info "Ensuring Hosting site '$PROJECT_ID' exists..."
if firebase hosting:sites:create "$PROJECT_ID" --project "$PROJECT_ID" --non-interactive 2>&1 \
    | tee /tmp/cortex-replicate-site.log; then
  pass "Hosting site created."
else
  if grep -qE "already exists|already taken|409" /tmp/cortex-replicate-site.log; then
    pass "Hosting site already exists."
  else
    warn "hosting:sites:create reported an error — see /tmp/cortex-replicate-site.log."
  fi
fi

# Cloudflare Tunnel is per-user. Print instructions; can't automate without browser auth.
cat <<EOF

${C_YELLOW}Cloudflare Tunnel (optional, for a custom domain like cortex.yourdomain.com):${C_RESET}
  This step cannot be automated — \`cloudflared tunnel login\` opens a browser
  for you to pick a Cloudflare zone. Run these once on your dev machine:

    cloudflared tunnel login
    cloudflared tunnel create cortex
    cloudflared tunnel route dns cortex cortex.yourdomain.com

  Then put the HTTPS URL of the tunnel into the GitHub secret
  ${C_BOLD}CLOUDFLARE_TUNNEL_URL${C_RESET}. If you skip this step, the relay just won't
  proxy through your tunnel — Cloud Run still works on its *.run.app URL.

EOF

# ─── Step 9: GitHub Secrets ──────────────────────────────────────────────────
step "Step 9 / 10  ·  GitHub Secrets on $GH_REPO"

if ! gh repo view "$GH_REPO" &>/dev/null; then
  warn "Repo $GH_REPO not found / not accessible."
  warn "Fork https://github.com/AlexiosBluffMara/cortex into $GH_USER/cortex first, then re-run."
  warn "Skipping secret upload — printing manual values instead:"
  cat <<EOF

  GCP_WIF_PROVIDER : $PROVIDER_FULL
  GCP_SA_EMAIL     : $SA_EMAIL

EOF
else
  set_secret() {
    local name="$1" value="$2"
    if [[ -z "$value" ]]; then warn "Skipping $name (empty value)."; return; fi
    printf '%s' "$value" | gh secret set "$name" --repo "$GH_REPO" --body - 2>/dev/null \
      || printf '%s' "$value" | gh secret set "$name" --repo "$GH_REPO"
    pass "Set secret $name."
  }

  set_secret GCP_WIF_PROVIDER "$PROVIDER_FULL"
  set_secret GCP_SA_EMAIL     "$SA_EMAIL"

  # Optional secrets — only set if the user has the values handy.
  if [[ $NONINTERACTIVE -eq 0 ]]; then
    prompt OLLAMA_URL_VAL          "OLLAMA_URL (e.g. https://ollama.example.com, blank to skip)" ""
    prompt CLOUDFLARE_TUNNEL_URL_VAL "CLOUDFLARE_TUNNEL_URL (blank to skip)" ""
    prompt GEMINI_API_KEY_VAL      "GEMINI_API_KEY (AIza..., blank to skip)" ""
    [[ -n "$OLLAMA_URL_VAL"            ]] && set_secret OLLAMA_URL            "$OLLAMA_URL_VAL"
    [[ -n "$CLOUDFLARE_TUNNEL_URL_VAL" ]] && set_secret CLOUDFLARE_TUNNEL_URL "$CLOUDFLARE_TUNNEL_URL_VAL"
    [[ -n "$GEMINI_API_KEY_VAL"        ]] && set_secret GEMINI_API_KEY        "$GEMINI_API_KEY_VAL"
  fi

  warn "GCP_INFERENCE_ENDPOINT is set ${C_BOLD}after${C_RESET} the first tribe-worker deploy."
  warn "After your first 'git push origin main', set it via:"
  echo "    gh secret set GCP_INFERENCE_ENDPOINT --repo $GH_REPO --body https://cortex-tribe-worker-XXXX-uc.a.run.app"
fi

# ─── Step 10: Final summary ──────────────────────────────────────────────────
step "Step 10 / 10  ·  Summary"

cat <<EOF

${C_GREEN}${C_BOLD}Cortex infrastructure is provisioned.${C_RESET}

  GCP project          : $PROJECT_ID
  Region               : $REGION
  WIF provider         : $PROVIDER_FULL
  Deploy SA            : $SA_EMAIL
  Artifact Registry    : ${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO_NAME}
  GCS bucket           : gs://${GCS_BUCKET}
  Firestore            : (default) — Native mode in $REGION
  Firebase Hosting     : site '$PROJECT_ID' linked
  GitHub repo          : $GH_REPO
  Secrets uploaded     : GCP_WIF_PROVIDER, GCP_SA_EMAIL (+ optional OLLAMA_URL/CF_TUNNEL/GEMINI)

${C_BOLD}Next steps:${C_RESET}
  1. ${C_BOLD}Push to main${C_RESET} on your fork — the deploy workflow will build and ship
     cortex-webapp + cortex-relay to Cloud Run, and Firebase Hosting frontend.
       cd /path/to/cortex
       git remote set-url origin https://github.com/${GH_REPO}.git   # if not already
       git push origin main

  2. Once cortex-tribe-worker is deployed (or you choose to skip the optional
     cloud worker), grab its URL and set:
       gh secret set GCP_INFERENCE_ENDPOINT --repo $GH_REPO --body <URL>

  3. Watch the deploy:
       gh run watch --repo $GH_REPO

  4. Open your hosted site:
       https://${PROJECT_ID}.web.app

${C_BOLD}Rollback:${C_RESET} run ${C_BOLD}bash scripts/replicate-cleanup.sh --project ${PROJECT_ID}${C_RESET} to tear it
all down. That script deletes everything this script created.

${C_BOLD}Docs:${C_RESET} docs/REPLICATE.md

EOF

pass "Done."
