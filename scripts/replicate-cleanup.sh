#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Cortex replicator — cleanup / teardown
#
# Deletes everything `scripts/replicate.sh` created in a given GCP project so
# you can test the replicator and roll back without leaving orphan resources
# on your billing account.
#
# Idempotent — safe to re-run; missing resources are skipped.
#
# Usage:
#   bash scripts/replicate-cleanup.sh --project my-gcp-id
#   bash scripts/replicate-cleanup.sh --project my-gcp-id --delete-project
#
# By default the GCP project itself is KEPT (only resources inside it are
# deleted). Pass --delete-project to also `gcloud projects delete`.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_RED=$'\033[31m'
else
  C_RESET=''; C_BOLD=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_RED=''
fi
pass() { echo "${C_GREEN}[PASS]${C_RESET} $*"; }
warn() { echo "${C_YELLOW}[WARN]${C_RESET} $*"; }
info() { echo "${C_BLUE}[INFO]${C_RESET} $*"; }
fail() { echo "${C_RED}[FAIL]${C_RESET} $*" >&2; }
step() { echo; echo "${C_BOLD}── $* ──${C_RESET}"; }

PROJECT_ID=""
REGION="us-central1"
DELETE_PROJECT=0
GH_REPO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region)  REGION="$2"; shift 2 ;;
    --gh-repo) GH_REPO="$2"; shift 2 ;;
    --delete-project) DELETE_PROJECT=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) fail "Unknown arg: $1"; exit 2 ;;
  esac
done

[[ -z "$PROJECT_ID" ]] && { fail "--project required."; exit 2; }

POOL_NAME="github-actions-pool"
PROVIDER_NAME="github-provider"
SA_NAME="github-actions-deploy"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
AR_REPO_NAME="cortex"

cat <<EOF
${C_RED}${C_BOLD}TEARDOWN${C_RESET} for project ${C_BOLD}${PROJECT_ID}${C_RESET}.
This will delete:
  • Cloud Run services: cortex-webapp, cortex-relay, cortex-tribe-worker
  • Artifact Registry repo: $AR_REPO_NAME
  • Secret Manager: gcp-inference-token, hf-token
  • GCS bucket: cortex-public-scans / cortex-public-scans-${PROJECT_ID}
  • Firestore (default) database     ${C_YELLOW}NOTE: Firestore deletion may require manual confirmation in console${C_RESET}
  • Firebase Hosting site: $PROJECT_ID
  • Service account: $SA_EMAIL
  • WIF provider + pool: $PROVIDER_NAME / $POOL_NAME
$( [[ $DELETE_PROJECT -eq 1 ]] && echo "  • The GCP project itself ($PROJECT_ID)" )

EOF
read -r -p "Type the project ID to confirm: " confirm
[[ "$confirm" != "$PROJECT_ID" ]] && { fail "Confirmation mismatch. Aborted."; exit 1; }

gcloud config set project "$PROJECT_ID" --quiet >/dev/null

# ─── Cloud Run services ──────────────────────────────────────────────────────
step "Cloud Run services"
for svc in cortex-webapp cortex-relay cortex-tribe-worker; do
  if gcloud run services describe "$svc" --region="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    info "Deleting Cloud Run service $svc..."
    gcloud run services delete "$svc" --region="$REGION" --project="$PROJECT_ID" --quiet \
      && pass "Deleted $svc." || warn "Failed to delete $svc."
  else
    pass "$svc not present — skipping."
  fi
done

# ─── Artifact Registry ───────────────────────────────────────────────────────
step "Artifact Registry repo"
if gcloud artifacts repositories describe "$AR_REPO_NAME" \
    --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  info "Deleting AR repo $AR_REPO_NAME (this also removes images)..."
  gcloud artifacts repositories delete "$AR_REPO_NAME" \
    --location="$REGION" --project="$PROJECT_ID" --quiet \
    && pass "AR repo deleted." || warn "Failed to delete AR repo."
else
  pass "AR repo $AR_REPO_NAME not present — skipping."
fi

# ─── Secrets ─────────────────────────────────────────────────────────────────
step "Secret Manager"
for s in gcp-inference-token hf-token; do
  if gcloud secrets describe "$s" --project="$PROJECT_ID" &>/dev/null; then
    info "Deleting secret $s..."
    gcloud secrets delete "$s" --project="$PROJECT_ID" --quiet \
      && pass "Deleted $s." || warn "Failed to delete $s."
  else
    pass "Secret $s not present — skipping."
  fi
done

# ─── GCS buckets ─────────────────────────────────────────────────────────────
step "GCS bucket"
for b in "cortex-public-scans" "cortex-public-scans-${PROJECT_ID}"; do
  if gcloud storage buckets describe "gs://$b" --project="$PROJECT_ID" &>/dev/null; then
    info "Emptying + deleting bucket gs://$b..."
    gcloud storage rm --recursive "gs://$b" --project="$PROJECT_ID" --quiet 2>/dev/null || true
    gcloud storage buckets delete "gs://$b" --project="$PROJECT_ID" --quiet \
      && pass "Deleted gs://$b." || warn "Failed to delete gs://$b."
  fi
done

# ─── Firebase Hosting site ───────────────────────────────────────────────────
step "Firebase Hosting site"
if command -v firebase &>/dev/null; then
  info "Attempting to delete Hosting site '$PROJECT_ID' (idempotent)..."
  firebase hosting:sites:delete "$PROJECT_ID" --project "$PROJECT_ID" --force --non-interactive 2>&1 \
    | tee /tmp/cortex-cleanup-fb.log || true
  if grep -qE "deleted|not found|404" /tmp/cortex-cleanup-fb.log; then
    pass "Hosting site cleanup done (deleted or already absent)."
  else
    warn "Could not delete Hosting site — check Firebase console manually."
  fi
else
  warn "firebase CLI not installed — skip Hosting site delete."
fi

# ─── Firestore (best-effort) ─────────────────────────────────────────────────
step "Firestore database"
if gcloud firestore databases describe --database="(default)" --project="$PROJECT_ID" &>/dev/null; then
  info "Attempting to delete Firestore (default)..."
  gcloud firestore databases delete --database="(default)" --project="$PROJECT_ID" --quiet 2>&1 \
    | tee /tmp/cortex-cleanup-fs.log || true
  if grep -qE "deleted|404" /tmp/cortex-cleanup-fs.log; then
    pass "Firestore deleted."
  else
    warn "Firestore could not be deleted automatically — delete it from"
    warn "  https://console.cloud.google.com/firestore/databases?project=$PROJECT_ID"
    warn "(Firestore deletes need a Delete Protection toggle in the console.)"
  fi
else
  pass "Firestore (default) not present — skipping."
fi

# ─── Service account + WIF ───────────────────────────────────────────────────
step "Service account + WIF"
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  info "Deleting service account $SA_EMAIL..."
  gcloud iam service-accounts delete "$SA_EMAIL" --project="$PROJECT_ID" --quiet \
    && pass "SA deleted." || warn "Failed to delete SA."
else
  pass "SA $SA_EMAIL not present — skipping."
fi

if gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_NAME" &>/dev/null; then
  info "Deleting WIF provider $PROVIDER_NAME..."
  gcloud iam workload-identity-pools providers delete "$PROVIDER_NAME" \
    --project="$PROJECT_ID" --location=global \
    --workload-identity-pool="$POOL_NAME" --quiet \
    && pass "Provider deleted." || warn "Failed to delete provider."
fi

if gcloud iam workload-identity-pools describe "$POOL_NAME" \
    --project="$PROJECT_ID" --location=global &>/dev/null; then
  info "Deleting WIF pool $POOL_NAME..."
  gcloud iam workload-identity-pools delete "$POOL_NAME" \
    --project="$PROJECT_ID" --location=global --quiet \
    && pass "Pool deleted." || warn "Pool may be in 30-day soft-delete state — Google retains it for restore."
fi

# ─── GitHub Secrets (optional) ───────────────────────────────────────────────
if [[ -n "$GH_REPO" ]] && command -v gh &>/dev/null; then
  step "GitHub Secrets on $GH_REPO"
  for s in GCP_WIF_PROVIDER GCP_SA_EMAIL OLLAMA_URL CLOUDFLARE_TUNNEL_URL GEMINI_API_KEY GCP_INFERENCE_ENDPOINT; do
    gh secret remove "$s" --repo "$GH_REPO" 2>/dev/null && pass "Removed $s." || true
  done
fi

# ─── Project itself ──────────────────────────────────────────────────────────
if [[ $DELETE_PROJECT -eq 1 ]]; then
  step "GCP project"
  warn "Deleting project $PROJECT_ID — recoverable for 30 days via 'gcloud projects undelete'."
  gcloud projects delete "$PROJECT_ID" --quiet \
    && pass "Project scheduled for deletion." || warn "Project deletion failed."
fi

echo
pass "Cleanup complete."
