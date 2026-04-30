#!/bin/bash
# Cortex Cloud GPU configuration — TRIBE v2 + Gemma 4 on Google Cloud
#
# COST CONTEXT:
#   Local RTX 5090 (32 GB GDDR7): ~$2,500 street price, amortized $0/hr once bought
#   Cloud L4 GPU (24 GB GDDR6):   ~$0.59/hr running, $0.00/hr scaled to zero
#   Cloud A100 40GB:               ~$2.93/hr running, $0.00/hr scaled to zero
#
#   For the demo: run on the 5090 (free). Cloud GPU is standby-only.
#   A single scan costs ~$0.06 on L4 (6 min × $0.59/hr) if cloud inference is needed.
#
# PREREQUISITES:
#   1. Cloud Run GPU must be enabled on your project (request via Google form):
#      https://cloud.google.com/run/docs/configuring/services/gpu
#   2. Build TRIBE v2 inference container first (see gcp/tribe-inference/)
#
set -euo pipefail

PROJECT=abm-isu
REGION=us-central1
ARTIFACT_REPO=cortex

# ─── Gemma 4 via Vertex AI Model Garden ───────────────────────────────────────
# Gemma 4 is available as a managed endpoint on Vertex AI.
# Cost: $0.00015/1K input tokens, $0.00060/1K output tokens (as of 2025).
# Free tier: 60 requests/min on Gemini Flash (proxy); no free tier on custom endpoints.
#
# To deploy Gemma 4 as a Vertex endpoint (optional, for full isolation):
#   gcloud ai endpoints create --region=$REGION --project=$PROJECT --display-name="gemma4-e4b"
#   gcloud ai models upload --region=$REGION --project=$PROJECT --display-name="gemma4-e4b" \
#     --container-image-uri=us-docker.pkg.dev/vertex-ai/prediction/pytorch-gpu.2-2:latest
# NOTE: For the hackathon, local Gemma 4 via Ollama is primary; Gemini Flash API is fallback.

# ─── Cloud Run with L4 GPU for TRIBE v2 inference ────────────────────────────
# Status: PREVIEW — requires allowlisting for your project.
# Request: https://cloud.google.com/run/docs/configuring/services/gpu
#
# Once allowlisted, deploy with:
deploy_tribe_cloud_run() {
    local IMAGE="$REGION-docker.pkg.dev/$PROJECT/$ARTIFACT_REPO/tribe-inference:latest"
    gcloud run deploy tribe-inference \
        --image="$IMAGE" \
        --region="$REGION" \
        --project="$PROJECT" \
        --allow-unauthenticated=false \
        --service-account="846100819386-compute@developer.gserviceaccount.com" \
        --memory=16Gi \
        --cpu=4 \
        --gpu=1 \
        --gpu-type=nvidia-l4 \
        --min-instances=0 \
        --max-instances=2 \
        --timeout=600 \
        --concurrency=1 \
        2>&1
    echo "TRIBE cloud inference deployed."
    echo "Cost: ~\$0.59/hr when active, \$0/hr when idle (scale-to-zero)"
    echo "Startup: ~90s cold start (GPU warmup + model load)"
}

# ─── Cost model for cloud GPU inference ──────────────────────────────────────
# L4 GPU instance (1 GPU, 4 vCPU, 16 GB RAM):
#   CPU:    $0.000048/vCPU/sec × 4 = $0.000192/sec
#   RAM:    $0.0000048/GB/sec × 16 = $0.0000768/sec
#   GPU L4: $0.000568/GPU/sec
#   TOTAL:  ~$0.000837/sec = ~$0.59/hr
#
# Per scan (6 min inference):
#   $0.000837/sec × 360 sec = $0.30/scan
#
# For 100 scans/month entirely on cloud L4:
#   100 × $0.30 = $30/month
#
# For 100 scans/month on local 5090 (already paid for):
#   $0.00/month (electricity cost: 6 min × 500W ≈ 0.05 kWh ≈ $0.006/scan)
#
# HYBRID STRATEGY (current):
#   Primary:  Local 5090 → $0/scan
#   Fallback: Cloud Run L4 → $0.30/scan
#   Budget:   $50/month cap = up to 166 cloud fallback scans before alert

# ─── Firestore indexes for fast gallery queries ───────────────────────────────
setup_firestore_indexes() {
    cat > /tmp/firestore-indexes.json << 'EOFI'
{
  "indexes": [
    {
      "collectionGroup": "scans",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "status", "order": "ASCENDING"},
        {"fieldPath": "created_at", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "scans",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "submitted_by_domain", "order": "ASCENDING"},
        {"fieldPath": "created_at", "order": "DESCENDING"}
      ]
    }
  ],
  "fieldOverrides": []
}
EOFI
    gcloud firestore indexes composite create \
        --collection-group=scans \
        --query-scope=COLLECTION \
        --field-config=field-path=status,order=ascending \
        --field-config=field-path=created_at,order=descending \
        --project=$PROJECT 2>&1 || echo "Index may already exist"
    echo "Firestore indexes configured"
}

# ─── Google OAuth Client ID setup ────────────────────────────────────────────
# MANUAL STEPS (2 min in GCP Console):
# 1. Go to: https://console.cloud.google.com/apis/credentials?project=abm-isu
# 2. Click "+ CREATE CREDENTIALS" → "OAuth client ID"
# 3. Application type: "Web application"
# 4. Name: "Cortex Web Client"
# 5. Authorized JavaScript origins:
#    - https://cortex.redteamkitchen.com
#    - https://cortex-relay-846100819386.us-central1.run.app
#    - http://localhost:8765 (for local dev)
# 6. Authorized redirect URIs: (leave empty for token flow)
# 7. Click "CREATE" → copy the Client ID
#
# Then update Cloud Run with the client ID:
update_oauth_client_id() {
    local CLIENT_ID="${1:?Pass the OAuth client ID as first argument}"
    gcloud run services update cortex-relay \
        --project=$PROJECT \
        --region=$REGION \
        --update-env-vars="GOOGLE_CLIENT_ID=$CLIENT_ID" \
        2>&1
    echo "OAuth client ID set on Cloud Run."
}

# ─── Run the setup steps ─────────────────────────────────────────────────────
case "${1:-help}" in
    tribe)       deploy_tribe_cloud_run ;;
    indexes)     setup_firestore_indexes ;;
    oauth)       update_oauth_client_id "${2:-}" ;;
    all)         setup_firestore_indexes ;;
    help|*)
        echo "Usage: $0 [tribe|indexes|oauth <CLIENT_ID>|all]"
        echo ""
        echo "  tribe   — Deploy TRIBE v2 to Cloud Run with L4 GPU (requires allowlisting)"
        echo "  indexes — Create Firestore composite indexes for gallery queries"
        echo "  oauth   — Set Google OAuth client ID on Cloud Run"
        echo "  all     — Run all non-GPU steps"
        ;;
esac
