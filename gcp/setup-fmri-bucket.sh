#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Cortex fMRI uploads bucket + CMEK keyring setup.
#
# Idempotent. Run once with Owner/Editor on abm-isu.
#
#   bash gcp/setup-fmri-bucket.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_ID="abm-isu"
REGION="us-central1"
BUCKET="cortex-fmri-uploads"
KEYRING="cortex-fmri-keys"
DEFAULT_KEY="default"
TOPIC="cortex-fmri-finalized"
SA_EMAIL="github-actions-deploy@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== fMRI bucket setup for ${PROJECT_ID} ==="

# 1. Required APIs
gcloud services enable \
  cloudkms.googleapis.com \
  pubsub.googleapis.com \
  storage.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudscheduler.googleapis.com \
  aiplatform.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# 2. KMS keyring + default key
if ! gcloud kms keyrings describe "${KEYRING}" \
     --project="${PROJECT_ID}" --location="${REGION}" &>/dev/null; then
  echo "Creating KMS keyring ${KEYRING}..."
  gcloud kms keyrings create "${KEYRING}" \
    --project="${PROJECT_ID}" --location="${REGION}" --quiet
fi

if ! gcloud kms keys describe "${DEFAULT_KEY}" \
     --project="${PROJECT_ID}" --location="${REGION}" --keyring="${KEYRING}" &>/dev/null; then
  echo "Creating default CMEK key..."
  gcloud kms keys create "${DEFAULT_KEY}" \
    --project="${PROJECT_ID}" --location="${REGION}" \
    --keyring="${KEYRING}" --purpose=encryption --quiet
fi

# 3. Allow GCS service agent to use the key
GCS_SERVICE_ACCOUNT=$(gcloud storage service-agent --project="${PROJECT_ID}")
gcloud kms keys add-iam-policy-binding "${DEFAULT_KEY}" \
  --project="${PROJECT_ID}" --location="${REGION}" --keyring="${KEYRING}" \
  --member="serviceAccount:${GCS_SERVICE_ACCOUNT}" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" \
  --quiet

# 4. Bucket
KEY_RESOURCE="projects/${PROJECT_ID}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${DEFAULT_KEY}"
if ! gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT_ID}" &>/dev/null; then
  echo "Creating bucket gs://${BUCKET}..."
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --default-storage-class=STANDARD \
    --uniform-bucket-level-access \
    --public-access-prevention \
    --default-encryption-key="${KEY_RESOURCE}" \
    --quiet
else
  gcloud storage buckets update "gs://${BUCKET}" \
    --default-encryption-key="${KEY_RESOURCE}" --quiet
fi

# 5. Lifecycle: 30-day soft delete, 1-year retention slot for opted-in
cat > /tmp/cortex-fmri-lifecycle.json <<EOF
{
  "lifecycle": {
    "rule": [
      {"action": {"type": "Delete"}, "condition": {"age": 365, "matchesPrefix": ["users/"]}}
    ]
  }
}
EOF
gcloud storage buckets update "gs://${BUCKET}" \
  --lifecycle-file=/tmp/cortex-fmri-lifecycle.json --quiet
gcloud storage buckets update "gs://${BUCKET}" --soft-delete-duration=30d --quiet || true

# 6. Deny-by-default IAM. Only the deploy SA gets storage.objectViewer.
echo "Setting bucket IAM..."
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin" --quiet
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.legacyBucketReader" --quiet

# Ensure the SA can sign URLs (needs the token creator role on itself)
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountTokenCreator" --quiet

# 7. Pub/Sub topic for object finalize events
if ! gcloud pubsub topics describe "${TOPIC}" --project="${PROJECT_ID}" &>/dev/null; then
  echo "Creating pubsub topic ${TOPIC}..."
  gcloud pubsub topics create "${TOPIC}" --project="${PROJECT_ID}" --quiet
fi

# Wire bucket → pubsub
gcloud storage buckets notifications create "gs://${BUCKET}" \
  --topic="projects/${PROJECT_ID}/topics/${TOPIC}" \
  --event-types=OBJECT_FINALIZE \
  --payload-format=json --quiet 2>/dev/null || \
  echo "  notification already configured"

# 8. KMS extra: allow deploy SA to read user keys (needed to mint per-user keys)
gcloud kms keyrings add-iam-policy-binding "${KEYRING}" \
  --project="${PROJECT_ID}" --location="${REGION}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/cloudkms.admin" --quiet

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  fMRI bucket ready:  gs://${BUCKET}"
echo "  Default CMEK:       ${KEY_RESOURCE}"
echo "  Pub/Sub topic:      projects/${PROJECT_ID}/topics/${TOPIC}"
echo ""
echo "  Next:"
echo "    bash gcp/cloud_functions/fmri_validate/deploy.sh"
echo "    gcloud scheduler jobs create http cortex-training-trigger \\"
echo "      --schedule='0 */6 * * *' --uri=<RELAY>/api/fmri/training-tick"
echo "═══════════════════════════════════════════════════════════════"
