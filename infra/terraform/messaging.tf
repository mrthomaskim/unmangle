# Gmail publishes inbox changes here for every connected user (users.watch topicName).
resource "google_pubsub_topic" "gmail" {
  name       = local.topic_name
  depends_on = [time_sleep.apis_ready]
}

resource "google_pubsub_topic_iam_member" "gmail_publisher" {
  topic  = google_pubsub_topic.gmail.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
}

resource "google_pubsub_subscription" "gmail_push" {
  name                       = "${local.topic_name}-sub"
  topic                      = google_pubsub_topic.gmail.id
  ack_deadline_seconds       = 120
  message_retention_duration = "86400s"

  push_config {
    push_endpoint = "${local.base_url}/pubsub/gmail"
    oidc_token {
      service_account_email = google_service_account.invoker.email
      audience              = local.base_url
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  # Default is to delete a subscription after 31 idle days; never expire.
  expiration_policy {
    ttl = ""
  }
}

resource "google_cloud_tasks_queue" "backfill" {
  name     = local.queue_name
  location = var.region

  rate_limits {
    max_concurrent_dispatches = 5
  }
  retry_config {
    max_attempts = 3
  }
  depends_on = [time_sleep.apis_ready]
}

locals {
  schedules = {
    "renew-gmail-watches" = { cron = "13 6 * * *", path = "/tasks/renew-watches" } # daily
    "weekly-digest"       = { cron = "7 8 * * 1", path = "/tasks/digest" }         # Mondays
  }
}

resource "google_cloud_scheduler_job" "jobs" {
  for_each         = local.schedules
  name             = each.key
  region           = var.region
  schedule         = each.value.cron
  time_zone        = "America/Chicago"
  attempt_deadline = "900s"

  http_target {
    http_method = "POST"
    uri         = "${local.base_url}${each.value.path}"
    oidc_token {
      service_account_email = google_service_account.invoker.email
      audience              = local.base_url
    }
  }

  retry_config {
    retry_count = 2
  }
  depends_on = [google_cloud_run_v2_service.app]
}
