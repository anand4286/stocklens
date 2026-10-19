#!/usr/bin/env bash
# Deploy to Google Cloud Run. Every identifier comes from your environment;
# nothing project-specific is committed.
#
#   export PROJECT_ID=my-project REGION=us-central1
#   ./deploy/cloudrun/deploy.sh
#
# One-time setup (secrets live in Secret Manager, never in this script):
#   printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create stocklens-api-key --data-file=-
#   printf '%s' "$ANTHROPIC_KEY"          | gcloud secrets create stocklens-anthropic-key --data-file=-
#   gcloud iam service-accounts create stocklens-run
#   gcloud secrets add-iam-policy-binding stocklens-api-key \
#     --member="serviceAccount:stocklens-run@${PROJECT_ID}.iam.gserviceaccount.com" \
#     --role=roles/secretmanager.secretAccessor   # repeat per secret
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-stocklens}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${SERVICE}/${SERVICE}:$(git rev-parse --short HEAD)"

gcloud artifacts repositories describe "$SERVICE" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$SERVICE" --repository-format=docker --location "$REGION" --project "$PROJECT_ID"

gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID"

SECRETS="STOCKLENS_API_KEY=stocklens-api-key:latest"
if gcloud secrets describe stocklens-anthropic-key --project "$PROJECT_ID" >/dev/null 2>&1; then
  SECRETS="${SECRETS},STOCKLENS_ANTHROPIC_API_KEY=stocklens-anthropic-key:latest"
fi

gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" --image "$IMAGE" \
  --service-account "stocklens-run@${PROJECT_ID}.iam.gserviceaccount.com" \
  --allow-unauthenticated \
  --ingress all \
  --cpu 1 --memory 512Mi --concurrency 40 \
  --min-instances 0 --max-instances 4 \
  --set-env-vars "STOCKLENS_DATA_PROVIDER=demo,STOCKLENS_TRUST_PROXY_HEADERS=true,FORWARDED_ALLOW_IPS=*,STOCKLENS_DATABASE_URL=sqlite:////tmp/stocklens.db" \
  --set-secrets "$SECRETS"

# Production hardening, not done by this script:
#  * Cloud SQL (PostgreSQL) via --add-cloudsql-instances and a private connector
#  * --ingress internal-and-cloud-load-balancing behind an HTTPS LB with Cloud Armor
#    (rate-based ban rule + OWASP preconfigured WAF rules), see docs/DEPLOYMENT.md
gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
