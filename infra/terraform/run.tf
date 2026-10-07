locals {
  plain_env = {
    APP_ENV              = "prod"
    STORE                = "firestore"
    GCP_PROJECT          = var.project_id
    BASE_URL             = local.base_url
    APP_NAME             = var.app_name
    SUPPORT_EMAIL        = var.support_email
    MAIL_FROM            = var.mail_from
    SMTP_HOST            = var.smtp_host
    SMTP_USER            = var.smtp_user
    PUBSUB_TOPIC         = google_pubsub_topic.gmail.id
    INVOKER_SA_EMAIL     = google_service_account.invoker.email
    TASKS_LOCATION       = var.region
    TASKS_QUEUE          = google_cloud_tasks_queue.backfill.name
    STRIPE_PORTAL_CONFIG = var.stripe_portal_config
  }
}

resource "google_cloud_run_v2_service" "app" {
  name                = local.svc
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  template {
    service_account                  = google_service_account.app.email
    timeout                          = "900s" # inbox cleanup tasks run up to ~13 min
    max_instance_request_concurrency = 40

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = var.initial_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.all_secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s[env.key].secret_id
              version = "latest"
            }
          }
        }
      }
      # Default TCP startup probe: works for both the placeholder image and the app.
    }
  }

  lifecycle {
    # release.sh rolls out new images with gcloud; Terraform owns everything else.
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.app,
    google_secret_manager_secret_version.placeholder,
    google_secret_manager_secret_version.flask_secret,
    google_secret_manager_secret_version.token_key,
    google_project_iam_member.app,
  ]
}

# Public web app: anyone can load pages. Machine endpoints verify OIDC tokens in the app.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.app.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "invoker" {
  name     = google_cloud_run_v2_service.app.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.invoker.email}"
}
