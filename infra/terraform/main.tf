data "google_project" "this" {
  project_id = var.project_id
}

locals {
  svc            = var.service_name
  project_number = data.google_project.this.number

  # Cloud Run's deterministic URL lets the service know its own address without a dependency cycle.
  run_url  = "https://${local.svc}-${local.project_number}.${var.region}.run.app"
  base_url = var.custom_domain_url != "" ? var.custom_domain_url : local.run_url

  topic_name = "gmail-push"
  queue_name = "backfill"

  apis = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudtasks.googleapis.com",
    "firestore.googleapis.com",
    "gmail.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
  ])
}

resource "google_project_service" "apis" {
  for_each           = local.apis
  service            = each.value
  disable_on_destroy = false
}

# Some APIs take a minute to become usable after enabling.
resource "time_sleep" "apis_ready" {
  depends_on      = [google_project_service.apis]
  create_duration = "45s"
}
