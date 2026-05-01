#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# GCP Workload Identity Federation setup for GitHub Actions CI/CD
#
# Run once from a shell with Owner/Editor on project abm-isu.
# Output: prints the two values you need to add as GitHub Secrets.
#
# Usage:
#   bash gcp/setup-wif.sh
#   bash gcp/setup-wif.sh --repo AlexiosBluffMara/mercury
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_ID="abm-isu"
REGION="us-central1"
POOL_NAME="github-actions-pool"
PROVIDER_NAME="github-provider"
SA_NAME="github-actions-deploy"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Repositories that are allowed to use these credentials.
# Add mercury here when ready.
ALLOWED_OWNER="AlexiosBluffMara"

# Positional arg: override repo constraint to a single repo
SINGLE_REPO="${1:-}"

echo "=== Setting up WIF for ${PROJECT_ID} ==="

# 1. Enable required APIs
echo "Enabling APIs..."
gcloud services enable \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  run.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  firebase.googleapis.com \
  artifactregistry.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# 2. Create Workload Identity Pool (idempotent)
if ! gcloud iam workload-identity-pools describe "${POOL_NAME}" \
    --project="${PROJECT_ID}" --location=global &>/dev/null; then
  echo "Creating WIF pool ${POOL_NAME}..."
  gcloud iam workload-identity-pools create "${POOL_NAME}" \
    --project="${PROJECT_ID}" \
    --location=global \
    --display-name="GitHub Actions pool" \
    --quiet
else
  echo "Pool ${POOL_NAME} already exists."
fi

POOL_ID=$(gcloud iam workload-identity-pools describe "${POOL_NAME}" \
  --project="${PROJECT_ID}" --location=global --format="value(name)")

# 3. Create OIDC provider (idempotent)
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_NAME}" \
    --project="${PROJECT_ID}" --location=global \
    --workload-identity-pool="${POOL_NAME}" &>/dev/null; then
  echo "Creating OIDC provider ${PROVIDER_NAME}..."
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_NAME}" \
    --project="${PROJECT_ID}" \
    --location=global \
    --workload-identity-pool="${POOL_NAME}" \
    --display-name="GitHub Actions OIDC" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="assertion.repository_owner=='${ALLOWED_OWNER}'" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --quiet
else
  echo "Provider ${PROVIDER_NAME} already exists."
fi

# 4. Create service account (idempotent)
if ! gcloud iam service-accounts describe "${SA_EMAIL}" \
    --project="${PROJECT_ID}" &>/dev/null; then
  echo "Creating service account ${SA_NAME}..."
  gcloud iam service-accounts create "${SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="GitHub Actions CI/CD deploy" \
    --quiet
else
  echo "Service account ${SA_EMAIL} already exists."
fi

# 5. Grant IAM roles to the service account
echo "Granting IAM roles..."
ROLES=(
  roles/run.admin                        # deploy Cloud Run services
  roles/storage.admin                    # manage GCS buckets (CORS, uploads)
  roles/secretmanager.secretAccessor     # read secrets at deploy time
  roles/cloudbuild.builds.editor         # trigger Cloud Build jobs
  roles/iam.serviceAccountUser           # act-as the SA in Cloud Run
  roles/datastore.user                   # write Firestore job records (project-level Firestore role)
  roles/firebase.admin                   # Firebase Hosting deploy
  roles/artifactregistry.admin           # push + create-on-push container images (gcr.io is deprecated)
)

for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet
done

# Also allow Container Registry (gcr.io is backed by GCS)
gsutil iam ch "serviceAccount:${SA_EMAIL}:roles/storage.admin" \
  "gs://artifacts.${PROJECT_ID}.appspot.com" 2>/dev/null || true

# 6. Bind the WIF pool → SA (allow any repo under ALLOWED_OWNER)
echo "Binding WIF principal to service account..."
if [ -n "${SINGLE_REPO}" ]; then
  PRINCIPAL="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${SINGLE_REPO}"
  echo "  Scoped to single repo: ${SINGLE_REPO}"
else
  PRINCIPAL="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository_owner/${ALLOWED_OWNER}"
  echo "  Scoped to all repos under ${ALLOWED_OWNER}"
fi

gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${PRINCIPAL}" \
  --quiet

# 7. Get the values to put in GitHub Secrets
PROVIDER_FULL=$(gcloud iam workload-identity-pools providers describe "${PROVIDER_NAME}" \
  --project="${PROJECT_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_NAME}" \
  --format="value(name)")

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Add these two values as GitHub Secrets in EACH repo:"
echo "  Settings → Secrets and variables → Actions → New repository secret"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Secret name : GCP_WIF_PROVIDER"
echo "  Secret value: ${PROVIDER_FULL}"
echo ""
echo "  Secret name : GCP_SA_EMAIL"
echo "  Secret value: ${SA_EMAIL}"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Also add the following repo-specific secrets (see"
echo "  docs/GITHUB_SECRETS.md for full inventory):"
echo "  OLLAMA_URL, CLOUDFLARE_TUNNEL_URL, GEMINI_API_KEY,"
echo "  GCP_INFERENCE_ENDPOINT, FIREBASE_SA_JSON"
echo "═══════════════════════════════════════════════════════════════"
