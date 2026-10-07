# Two secrets are generated here. The rest are created with a placeholder value; set real
# values with ../set-secret.sh so they never pass through Terraform state.

locals {
  external_secrets = {
    GOOGLE_CLIENT_ID      = "google-client-id"
    GOOGLE_CLIENT_SECRET  = "google-client-secret"
    STRIPE_SECRET_KEY     = "stripe-secret-key"
    STRIPE_WEBHOOK_SECRET = "stripe-webhook-secret"
    SMTP_PASSWORD         = "smtp-password"
  }
  generated_secrets = {
    SECRET_KEY    = "secret-key"
    TOKEN_ENC_KEY = "token-key"
  }
  all_secrets = merge(local.external_secrets, local.generated_secrets)
}

resource "google_secret_manager_secret" "s" {
  for_each  = local.all_secrets
  secret_id = "${local.svc}-${each.value}"
  replication {
    auto {}
  }
  depends_on = [time_sleep.apis_ready]
}

resource "google_secret_manager_secret_version" "placeholder" {
  for_each    = local.external_secrets
  secret      = google_secret_manager_secret.s[each.key].id
  secret_data = "unset"
  lifecycle {
    ignore_changes = [secret_data, enabled]
  }
}

resource "random_password" "flask_secret" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret_version" "flask_secret" {
  secret      = google_secret_manager_secret.s["SECRET_KEY"].id
  secret_data = random_password.flask_secret.result
}

# Fernet key = URL-safe base64 of 32 random bytes.
# NEVER replace this: every stored Gmail token is encrypted with it.
resource "random_bytes" "fernet" {
  length = 32
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret_version" "token_key" {
  secret      = google_secret_manager_secret.s["TOKEN_ENC_KEY"].id
  secret_data = replace(replace(random_bytes.fernet.base64, "+", "-"), "/", "_")
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret_iam_member" "app" {
  for_each  = local.all_secrets
  secret_id = google_secret_manager_secret.s[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}
