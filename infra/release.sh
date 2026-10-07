#!/usr/bin/env bash
# Build the app in Cloud Build and roll it out to Cloud Run. Run from the repo root:
#   ./infra/release.sh
# Every release gets a new image tag, so it also picks up secrets changed with set-secret.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF="terraform -chdir=$ROOT/infra/terraform"

out() { $TF output -raw "$1"; }
PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
REGION="$(out region)"
SERVICE="$(out service_name)"
REPO="$(out image_repo)"
BUILD_SA="$(out build_service_account)"

TAG="$(date -u +%Y%m%d-%H%M%S)"
if git -C "$ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
  TAG="$TAG-$(git -C "$ROOT" rev-parse --short HEAD)"
fi
IMAGE="$REPO/app:$TAG"

echo "==> Building $IMAGE"
gcloud builds submit "$ROOT" --project="$PROJECT_ID" --region="$REGION" \
  --config="$ROOT/infra/cloudbuild.yaml" --substitutions="_IMAGE=$IMAGE" \
  --service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA"

echo "==> Deploying to Cloud Run"
gcloud run services update "$SERVICE" --project="$PROJECT_ID" --region="$REGION" --image="$IMAGE"

URL="$(out base_url)"
echo "==> Health check $URL/healthz"
for _ in 1 2 3 4 5; do
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/healthz")" == "200" ]]; then
    echo "Released $TAG"; exit 0
  fi
  sleep 3
done
echo "Health check failed. Logs: gcloud run services logs read $SERVICE --region $REGION --limit 50" >&2
exit 1
