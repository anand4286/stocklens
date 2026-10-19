#!/usr/bin/env bash
# Cloud Armor policy for an HTTPS load balancer in front of Cloud Run.
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
POLICY="${POLICY:-stocklens-edge}"

gcloud compute security-policies create "$POLICY" --project "$PROJECT_ID" \
  --description "StockLens edge: WAF + per-IP rate limits"

# OWASP preconfigured rules (SQLi, XSS, LFI, RCE, scanner detection).
priority=1000
for rule in sqli-v33-stable xss-v33-stable lfi-v33-stable rce-v33-stable scannerdetection-v33-stable; do
  priority=$((priority + 1))
  gcloud compute security-policies rules create "$priority" --project "$PROJECT_ID" \
    --security-policy "$POLICY" --expression "evaluatePreconfiguredWaf('${rule}')" --action deny-403
done

# Tight limit on the paid agent endpoint, looser on the rest.
gcloud compute security-policies rules create 100 --project "$PROJECT_ID" --security-policy "$POLICY" \
  --expression "request.path.matches('/api/v1/symbols/[A-Z.-]+/analyst')" \
  --action throttle --rate-limit-threshold-count 5 --rate-limit-threshold-interval-sec 60 \
  --conform-action allow --exceed-action deny-429 --enforce-on-key IP

gcloud compute security-policies rules create 200 --project "$PROJECT_ID" --security-policy "$POLICY" \
  --src-ip-ranges "*" \
  --action rate-based-ban --rate-limit-threshold-count 300 --rate-limit-threshold-interval-sec 60 \
  --ban-duration-sec 600 --conform-action allow --exceed-action deny-429 --enforce-on-key IP
