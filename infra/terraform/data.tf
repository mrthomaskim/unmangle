resource "google_firestore_database" "default" {
  name                    = "(default)"
  location_id             = var.firestore_location
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = var.deletion_protection ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  deletion_policy         = var.deletion_protection ? "ABANDON" : "DELETE"
  depends_on              = [time_sleep.apis_ready]
}

# Auto-delete documents once expires_at passes (activity rows after 90 days, webhook ids after 30).
resource "google_firestore_field" "ttl" {
  for_each   = toset(["activity", "stripe_events"])
  database   = google_firestore_database.default.name
  collection = each.value
  field      = "expires_at"
  ttl_config {}
}

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = local.svc
  format        = "DOCKER"
  description   = "${var.app_name} container images"

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }
  depends_on = [time_sleep.apis_ready]
}

resource "google_artifact_registry_repository_iam_member" "build_push" {
  location   = google_artifact_registry_repository.app.location
  repository = google_artifact_registry_repository.app.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build.email}"
}
