"""All configuration comes from environment variables (Secret Manager in prod)."""
import os


def _env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable {name}")
    return val


class Config:
    APP_ENV = _env("APP_ENV", "dev")  # dev | prod | test
    APP_NAME = _env("APP_NAME", "Unmangle")
    BASE_URL = (_env("BASE_URL", "http://localhost:8080") or "").rstrip("/")
    SUPPORT_EMAIL = _env("SUPPORT_EMAIL", "support@example.com")

    SECRET_KEY = _env("SECRET_KEY", "dev-only-not-secret")
    TOKEN_ENC_KEY = _env("TOKEN_ENC_KEY")  # Fernet key; generated per env

    STORE = _env("STORE", "firestore")  # firestore | memory
    GCP_PROJECT = _env("GCP_PROJECT")

    # Google OAuth (one Web client for sign-in and Gmail connect)
    GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")

    # Gmail push + background work
    PUBSUB_TOPIC = _env("PUBSUB_TOPIC")  # projects/<p>/topics/gmail-push
    INVOKER_SA_EMAIL = _env("INVOKER_SA_EMAIL")  # SA that Pub/Sub, Scheduler, Tasks sign as
    OIDC_AUDIENCE = (_env("OIDC_AUDIENCE") or BASE_URL).rstrip("/")
    TASKS_LOCATION = _env("TASKS_LOCATION", "us-central1")
    TASKS_QUEUE = _env("TASKS_QUEUE", "backfill")

    # Stripe
    STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")
    PRICE_LOOKUP_MONTHLY = _env("PRICE_LOOKUP_MONTHLY", "unmangle_monthly")
    PRICE_LOOKUP_ANNUAL = _env("PRICE_LOOKUP_ANNUAL", "unmangle_annual")
    STRIPE_PORTAL_CONFIG = _env("STRIPE_PORTAL_CONFIG")  # bpc_...; from scripts/stripe_setup.py
    TRIAL_DAYS = int(_env("TRIAL_DAYS", "14"))

    # Outbound email for weekly digests (any SMTP provider: SendGrid, Postmark, SES...)
    SMTP_HOST = _env("SMTP_HOST")
    SMTP_PORT = int(_env("SMTP_PORT", "587"))
    SMTP_USER = _env("SMTP_USER")
    SMTP_PASSWORD = _env("SMTP_PASSWORD")
    MAIL_FROM = _env("MAIL_FROM", "Unmangle <digest@example.com>")

    ACTIVITY_RETENTION_DAYS = int(_env("ACTIVITY_RETENTION_DAYS", "90"))

    @property
    def is_prod(self):
        return self.APP_ENV == "prod"
