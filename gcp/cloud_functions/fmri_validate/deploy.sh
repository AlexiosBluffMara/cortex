#!/usr/bin/env bash
# Deploys the fMRI validate Cloud Function (gen 2). Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"

PROJECT="${GCP_PROJECT:-abm-isu}"
REGION="${REGION:-us-central1}"
NAME="cortex-fmri-validate"
SA="github-actions-deploy@${PROJECT}.iam.gserviceaccount.com"
TOPIC="cortex-fmri-finalized"

# We need cortex/fmri_validator.py inside the deploy bundle.
STAGE="$(mktemp -d)"
cp -r "${HERE}"/* "${STAGE}/"
mkdir -p "${STAGE}/cortex"
cp "${REPO_ROOT}/cortex/__init__.py" "${STAGE}/cortex/"
cp "${REPO_ROOT}/cortex/fmri_validator.py" "${STAGE}/cortex/"

gcloud functions deploy "${NAME}" \
  --gen2 --runtime=python312 \
  --region="${REGION}" --project="${PROJECT}" \
  --source="${STAGE}" \
  --entry-point=on_finalize \
  --trigger-topic="${TOPIC}" \
  --service-account="${SA}" \
  --memory=1024Mi --timeout=540s \
  --set-env-vars="GCP_PROJECT=${PROJECT},HEADER_BYTES=16777216" \
  --quiet
echo "Deployed ${NAME} in ${REGION}"
