#!/usr/bin/env bash
# Store a secret value without it touching Terraform state or shell history.
#   ./infra/set-secret.sh STRIPE_SECRET_KEY          (prompts, input hidden)
#   ./infra/set-secret.sh GOOGLE_CLIENT_ID < file     (reads stdin)
# Then run ./infra/release.sh so the running service picks it up.
set -euo pipefail

declare -A NAMES=(
  [GOOGLE_CLIENT_ID]=google-client-id
  [GOOGLE_CLIENT_SECRET]=google-client-secret
  [STRIPE_SECRET_KEY]=stripe-secret-key
  [STRIPE_WEBHOOK_SECRET]=stripe-webhook-secret
  [SMTP_PASSWORD]=smtp-password
)
VAR="${1:-}"
if [[ -z "$VAR" || -z "${NAMES[$VAR]:-}" ]]; then
  echo "Usage: $0 {$(IFS='|'; echo "${!NAMES[*]}")}" >&2
  exit 1
fi
SERVICE="${SERVICE:-unmangle}"
SECRET="$SERVICE-${NAMES[$VAR]}"

if [[ -t 0 ]]; then
  read -r -s -p "Value for $VAR: " VALUE; echo
else
  VALUE="$(cat)"
fi
[[ -n "$VALUE" ]] || { echo "Empty value; nothing stored." >&2; exit 1; }

case "$VAR" in
  STRIPE_SECRET_KEY)     [[ "$VALUE" =~ ^(sk|rk)_(test|live)_ ]] || { echo "Expected sk_test_/sk_live_..." >&2; exit 1; } ;;
  STRIPE_WEBHOOK_SECRET) [[ "$VALUE" == whsec_* ]] || { echo "Expected whsec_..." >&2; exit 1; } ;;
  GOOGLE_CLIENT_ID)      [[ "$VALUE" == *.apps.googleusercontent.com ]] || { echo "Expected ...apps.googleusercontent.com" >&2; exit 1; } ;;
esac

printf '%s' "$VALUE" | gcloud secrets versions add "$SECRET" --data-file=- >/dev/null
echo "Stored new version of $SECRET. Run ./infra/release.sh to roll it out."
