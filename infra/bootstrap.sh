#!/usr/bin/env bash
# One-time: create the GCP project, link billing, and create the Terraform state bucket.
# Everything else is Terraform. Safe to re-run.
#
#   PROJECT_ID=unmangle-prod-1234 BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX ./infra/bootstrap.sh
#
# Find your billing account ID with:  gcloud billing accounts list
# Optional: REGION (us-central1), ORG_ID or FOLDER_ID (omit for a personal account).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID (6-30 chars, lowercase, globally unique)}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:?Set BILLING_ACCOUNT (run: gcloud billing accounts list)}"
REGION="${REGION:-us-central1}"
STATE_BUCKET="${STATE_BUCKET:-${PROJECT_ID}-tfstate}"
TF_DIR="$(cd "$(dirname "$0")/terraform" && pwd)"

step() { printf '\n==> %s\n' "$*"; }

step "Project $PROJECT_ID"
if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "  exists"
else
  PARENT=()
  [[ -n "${ORG_ID:-}" ]] && PARENT=(--organization="$ORG_ID")
  [[ -n "${FOLDER_ID:-}" ]] && PARENT=(--folder="$FOLDER_ID")
  gcloud projects create "$PROJECT_ID" --name="Unmangle" "${PARENT[@]}"
fi

step "Billing"
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
echo "  linked to $BILLING_ACCOUNT"

step "Base APIs (the rest are enabled by Terraform)"
gcloud services enable --project="$PROJECT_ID" \
  serviceusage.googleapis.com cloudresourcemanager.googleapis.com \
  cloudbilling.googleapis.com storage.googleapis.com iam.googleapis.com

step "Terraform state bucket gs://$STATE_BUCKET"
if ! gcloud storage buckets describe "gs://$STATE_BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$STATE_BUCKET" --project="$PROJECT_ID" \
    --location="$REGION" --uniform-bucket-level-access --public-access-prevention
fi
gcloud storage buckets update "gs://$STATE_BUCKET" --versioning >/dev/null
echo "  versioned, private"

step "Writing $TF_DIR/backend.hcl and terraform.tfvars"
cat > "$TF_DIR/backend.hcl" <<EOF
bucket = "$STATE_BUCKET"
prefix = "unmangle/prod"
EOF
if [[ ! -f "$TF_DIR/terraform.tfvars" ]]; then
  sed -e "s/unmangle-prod-xxxx/$PROJECT_ID/" \
      -e "s/XXXXXX-XXXXXX-XXXXXX/$BILLING_ACCOUNT/" \
      -e "s/us-central1/$REGION/" \
      "$TF_DIR/terraform.tfvars.example" > "$TF_DIR/terraform.tfvars"
  echo "  created terraform.tfvars (review support_email / mail_from)"
else
  echo "  terraform.tfvars already exists; left unchanged"
fi

gcloud config set project "$PROJECT_ID" >/dev/null
cat <<EOF

Bootstrap done. Next:
  cd infra/terraform
  terraform init -backend-config=backend.hcl
  terraform plan -out=tfplan && terraform apply tfplan
EOF
