#!/usr/bin/env bash
# Deploys the whole Unmangle stack to one GCP project. Idempotent: re-run after code changes.
#
#   PROJECT_ID=my-proj ./deploy.sh
#
# Optional env: REGION (us-central1), SERVICE (unmangle), BASE_URL (custom domain; default is the
# Cloud Run URL), APP_NAME, SUPPORT_EMAIL, MAIL_FROM, SMTP_HOST, SMTP_USER, STRIPE_PORTAL_CONFIG.
# Secrets are read from same-named env vars on first run, or prompted for.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-unmangle}"
APP_NAME="${APP_NAME:-Unmangle}"
SUPPORT_EMAIL="${SUPPORT_EMAIL:-support@example.com}"
MAIL_FROM="${MAIL_FROM:-$APP_NAME <digest@example.com>}"
SMTP_HOST="${SMTP_HOST:-}"
SMTP_USER="${SMTP_USER:-}"
STRIPE_PORTAL_CONFIG="${STRIPE_PORTAL_CONFIG:-}"

TOPIC="gmail-push"
SUB="gmail-push-sub"
QUEUE="backfill"
APP_SA="${SERVICE}-app@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="${SERVICE}-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

cd "$(dirname "$0")"
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
step() { printf '\n==> %s\n' "$*"; }

step "Enabling APIs"
gcloud services enable run.googleapis.com firestore.googleapis.com pubsub.googleapis.com \
  cloudtasks.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com gmail.googleapis.com iam.googleapis.com

step "Firestore (Native mode)"
if ! gcloud firestore databases describe --database='(default)' >/dev/null 2>&1; then
  gcloud firestore databases create --location=nam5 --type=firestore-native
fi
# Auto-delete activity rows and webhook ids when expires_at passes.
gcloud firestore fields ttls update expires_at --collection-group=activity --enable-ttl --async --quiet >/dev/null 2>&1 || true
gcloud firestore fields ttls update expires_at --collection-group=stripe_events --enable-ttl --async --quiet >/dev/null 2>&1 || true

step "Service accounts"
for sa in "${SERVICE}-app" "${SERVICE}-invoker"; do
  gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$sa" --display-name="$APP_NAME ${sa##*-}"
done
for role in roles/datastore.user roles/cloudtasks.enqueuer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$APP_SA" \
    --role="$role" --condition=None >/dev/null
done
# The app creates Cloud Tasks that authenticate as the invoker SA.
gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" \
  --member="serviceAccount:$APP_SA" --role=roles/iam.serviceAccountUser >/dev/null
# Pub/Sub must mint OIDC tokens for push (default on newer projects; explicit here).
gcloud iam service-accounts add-iam-policy-binding "$INVOKER_SA" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null

step "Secrets"
ensure_secret() {  # name, env var, generator (optional), required (1/0)
  local name="$1" var="$2" gen="${3:-}" required="${4:-1}" value=""
  if gcloud secrets describe "$name" >/dev/null 2>&1 && \
     gcloud secrets versions list "$name" --filter=state=ENABLED --format='value(name)' | grep -q .; then
    if [[ -n "${!var:-}" && "$var" != "TOKEN_ENC_KEY" ]]; then
      printf '%s' "${!var}" | gcloud secrets versions add "$name" --data-file=- >/dev/null
      echo "  $name: updated from \$$var"
    else
      echo "  $name: exists"
    fi
  else
    gcloud secrets describe "$name" >/dev/null 2>&1 || \
      gcloud secrets create "$name" --replication-policy=automatic >/dev/null
    if [[ -n "${!var:-}" ]]; then value="${!var}"
    elif [[ -n "$gen" ]]; then value="$(eval "$gen")"
    elif [[ "$required" == "1" ]]; then read -r -s -p "  Enter $var: " value; echo
    else echo "  $name: skipped (optional)"; gcloud secrets delete "$name" --quiet >/dev/null; return 0
    fi
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    echo "  $name: created"
  fi
  gcloud secrets add-iam-policy-binding "$name" --member="serviceAccount:$APP_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
}
ensure_secret "${SERVICE}-secret-key" SECRET_KEY "python3 -c 'import secrets;print(secrets.token_urlsafe(48))'"
# Never regenerate: losing this key makes every stored Gmail token unreadable.
ensure_secret "${SERVICE}-token-key" TOKEN_ENC_KEY \
  "python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'"
ensure_secret "${SERVICE}-google-client-id" GOOGLE_CLIENT_ID
ensure_secret "${SERVICE}-google-client-secret" GOOGLE_CLIENT_SECRET
ensure_secret "${SERVICE}-stripe-secret-key" STRIPE_SECRET_KEY
ensure_secret "${SERVICE}-stripe-webhook-secret" STRIPE_WEBHOOK_SECRET "echo pending-run-stripe-setup"
ensure_secret "${SERVICE}-smtp-password" SMTP_PASSWORD "" 0

SECRETS="SECRET_KEY=${SERVICE}-secret-key:latest,TOKEN_ENC_KEY=${SERVICE}-token-key:latest"
SECRETS+=",GOOGLE_CLIENT_ID=${SERVICE}-google-client-id:latest,GOOGLE_CLIENT_SECRET=${SERVICE}-google-client-secret:latest"
SECRETS+=",STRIPE_SECRET_KEY=${SERVICE}-stripe-secret-key:latest,STRIPE_WEBHOOK_SECRET=${SERVICE}-stripe-webhook-secret:latest"
if gcloud secrets describe "${SERVICE}-smtp-password" >/dev/null 2>&1; then
  SECRETS+=",SMTP_PASSWORD=${SERVICE}-smtp-password:latest"
fi

step "Pub/Sub topic for Gmail push"
gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPIC"
gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" --role=roles/pubsub.publisher >/dev/null

step "Cloud Tasks queue"
if gcloud tasks queues describe "$QUEUE" --location="$REGION" >/dev/null 2>&1; then
  gcloud tasks queues update "$QUEUE" --location="$REGION" --max-concurrent-dispatches=5 --max-attempts=3 >/dev/null
else
  gcloud tasks queues create "$QUEUE" --location="$REGION" --max-concurrent-dispatches=5 --max-attempts=3
fi

env_vars() {
  local base="$1"
  # '|' delimiter so values may contain commas and spaces.
  echo "^|^APP_ENV=prod|STORE=firestore|GCP_PROJECT=${PROJECT_ID}|BASE_URL=${base}|APP_NAME=${APP_NAME}|SUPPORT_EMAIL=${SUPPORT_EMAIL}|MAIL_FROM=${MAIL_FROM}|SMTP_HOST=${SMTP_HOST}|SMTP_USER=${SMTP_USER}|PUBSUB_TOPIC=projects/${PROJECT_ID}/topics/${TOPIC}|INVOKER_SA_EMAIL=${INVOKER_SA}|TASKS_LOCATION=${REGION}|TASKS_QUEUE=${QUEUE}|STRIPE_PORTAL_CONFIG=${STRIPE_PORTAL_CONFIG}"
}

step "Deploying Cloud Run service (builds the container)"
EXISTING_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)' 2>/dev/null || true)"
URL_FOR_ENV="${BASE_URL:-${EXISTING_URL:-https://placeholder.invalid}}"
gcloud run deploy "$SERVICE" --source=. --region="$REGION" --service-account="$APP_SA" \
  --allow-unauthenticated --timeout=900 --memory=512Mi --cpu=1 --concurrency=40 \
  --min-instances=0 --max-instances=10 \
  --set-secrets="$SECRETS" --set-env-vars="$(env_vars "$URL_FOR_ENV")"
RUN_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
BASE_URL="${BASE_URL:-$RUN_URL}"
if [[ "$URL_FOR_ENV" != "$BASE_URL" ]]; then
  gcloud run services update "$SERVICE" --region="$REGION" --update-env-vars="$(env_vars "$BASE_URL")" >/dev/null
fi
gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
  --member="serviceAccount:$INVOKER_SA" --role=roles/run.invoker >/dev/null

step "Pub/Sub push subscription -> $BASE_URL/pubsub/gmail"
PUSH_ARGS=(--push-endpoint="$BASE_URL/pubsub/gmail" --push-auth-service-account="$INVOKER_SA"
           --push-auth-token-audience="$BASE_URL" --ack-deadline=120
           --min-retry-delay=10s --max-retry-delay=600s)
if gcloud pubsub subscriptions describe "$SUB" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "$SUB" "${PUSH_ARGS[@]}" >/dev/null
else
  gcloud pubsub subscriptions create "$SUB" --topic="$TOPIC" --message-retention-duration=1d "${PUSH_ARGS[@]}"
fi

step "Scheduler jobs"
schedule() {  # name, cron, path
  gcloud scheduler jobs delete "$1" --location="$REGION" --quiet >/dev/null 2>&1 || true
  gcloud scheduler jobs create http "$1" --location="$REGION" --schedule="$2" \
    --time-zone="America/Chicago" --uri="$BASE_URL$3" --http-method=POST \
    --oidc-service-account-email="$INVOKER_SA" --oidc-token-audience="$BASE_URL" \
    --attempt-deadline=900s >/dev/null
  echo "  $1: $2"
}
schedule renew-gmail-watches "13 6 * * *" /tasks/renew-watches
schedule weekly-digest "7 8 * * 1" /tasks/digest

cat <<EOF

Deployed: $BASE_URL

Next, if you haven't already:
  1. Google Auth Platform > Clients > your Web client: add redirect URI
       $BASE_URL/auth/callback
  2. Stripe (test key first, then live):
       STRIPE_SECRET_KEY=sk_... python3 scripts/stripe_setup.py --base-url $BASE_URL --webhook
     then re-run this script with STRIPE_WEBHOOK_SECRET=whsec_... and STRIPE_PORTAL_CONFIG=bpc_...
EOF
