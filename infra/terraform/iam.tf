# Runtime identity for the Cloud Run service.
resource "google_service_account" "app" {
  account_id   = "${local.svc}-app"
  display_name = "${var.app_name} app runtime"
  depends_on   = [time_sleep.apis_ready]
}

# Identity that Pub/Sub push, Cloud Scheduler and Cloud Tasks sign OIDC tokens as.
# The app verifies tokens against this email (INVOKER_SA_EMAIL).
resource "google_service_account" "invoker" {
  account_id   = "${local.svc}-invoker"
  display_name = "${var.app_name} push/scheduler/tasks invoker"
  depends_on   = [time_sleep.apis_ready]
}

# Runs container builds (release.sh). Avoids depending on the default compute SA.
resource "google_service_account" "build" {
  account_id   = "${local.svc}-build"
  display_name = "${var.app_name} Cloud Build"
  depends_on   = [time_sleep.apis_ready]
}

resource "google_project_iam_member" "app" {
  for_each = toset([
    "roles/datastore.user",
    "roles/cloudtasks.enqueuer",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app.email}"
}

resource "google_project_iam_member" "build" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/storage.objectViewer", # read uploaded source from the _cloudbuild bucket
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.build.email}"
}

# The app creates Cloud Tasks whose OIDC token is minted for the invoker SA.
resource "google_service_account_iam_member" "app_acts_as_invoker" {
  service_account_id = google_service_account.invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.app.email}"
}
