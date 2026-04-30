#!/bin/bash
# One-shot GCP setup for Cortex public site
set -euo pipefail

PROJECT=abm-isu
REGION=us-central1
BUCKET=cortex-public-scans

# Enable APIs
gcloud services enable firestore.googleapis.com storage.googleapis.com \
  run.googleapis.com firebase.googleapis.com cloudbuild.googleapis.com \
  --project=$PROJECT

# Create GCS bucket (public read for /videos/ and /thumbnails/)
gsutil mb -p $PROJECT -l $REGION gs://$BUCKET
gsutil iam ch allUsers:objectViewer gs://$BUCKET/videos
gsutil iam ch allUsers:objectViewer gs://$BUCKET/thumbnails
gsutil cors set gcp/bucket-cors.json gs://$BUCKET

# Create Firestore database (native mode)
gcloud firestore databases create --project=$PROJECT --location=$REGION

# Deploy Cloud Run relay
gcloud run deploy cortex-relay \
  --source=gcp/relay/ \
  --region=$REGION \
  --project=$PROJECT \
  --allow-unauthenticated \
  --set-env-vars="TUNNEL_URL=${CLOUDFLARE_TUNNEL_URL},GCS_BUCKET=$BUCKET,GCP_PROJECT=$PROJECT"

# Deploy Firebase Hosting
npm install -g firebase-tools
firebase use $PROJECT
firebase deploy --only hosting
