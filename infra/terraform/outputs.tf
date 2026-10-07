output "base_url" {
  value = local.base_url
}

output "run_url" {
  value = google_cloud_run_v2_service.app.uri
}

output "oauth_redirect_uri" {
  description = "Add to the OAuth Web client's authorized redirect URIs."
  value       = "${local.base_url}/auth/callback"
}

output "stripe_webhook_url" {
  value = "${local.base_url}/stripe/webhook"
}

output "image_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}

output "build_service_account" {
  value = google_service_account.build.email
}

output "service_name" {
  value = google_cloud_run_v2_service.app.name
}

output "region" {
  value = var.region
}

output "secrets_to_set" {
  description = "Set each with: ./infra/set-secret.sh NAME"
  value       = sort(keys(local.external_secrets))
}
