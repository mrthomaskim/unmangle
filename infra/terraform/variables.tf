variable "project_id" {
  description = "GCP project ID (created by bootstrap.sh)."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Tasks, Scheduler and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "firestore_location" {
  description = "Firestore location. nam5 is US multi-region; cannot be changed after creation."
  type        = string
  default     = "nam5"
}

variable "service_name" {
  description = "Cloud Run service name; also prefixes service accounts and secrets."
  type        = string
  default     = "unmangle"
}

variable "app_name" {
  type    = string
  default = "Unmangle"
}

variable "custom_domain_url" {
  description = "Public URL if you map a custom domain, e.g. https://unmangle.app. Empty = the run.app URL."
  type        = string
  default     = ""
  validation {
    condition     = var.custom_domain_url == "" || can(regex("^https://[^/]+$", var.custom_domain_url))
    error_message = "Use https://host with no trailing slash or path."
  }
}

variable "support_email" {
  type    = string
  default = "support@example.com"
}

variable "mail_from" {
  description = "From header for weekly digests."
  type        = string
  default     = "Unmangle <digest@example.com>"
}

variable "smtp_host" {
  description = "SMTP server for digests. Empty = digests are logged, not sent."
  type        = string
  default     = ""
}

variable "smtp_user" {
  type    = string
  default = ""
}

variable "stripe_portal_config" {
  description = "bpc_... from scripts/stripe_setup.py. Empty = Stripe's default portal configuration."
  type        = string
  default     = ""
}

variable "initial_image" {
  description = "Image used only for the very first deploy. release.sh replaces it; Terraform then ignores image changes."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "max_instances" {
  type    = number
  default = 10
}

variable "deletion_protection" {
  description = "Protect the Cloud Run service and Firestore database from terraform destroy."
  type        = bool
  default     = true
}

variable "billing_account" {
  description = "Billing account ID (XXXXXX-XXXXXX-XXXXXX) for the budget alert. Empty = no budget."
  type        = string
  default     = ""
}

variable "budget_usd" {
  description = "Monthly budget; alerts at 50/90/100% go to billing admins."
  type        = number
  default     = 10
}
