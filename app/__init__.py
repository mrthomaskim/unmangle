import os
from datetime import timedelta

import stripe
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from . import crypto
from .config import Config
from .store import make_store

csrf = CSRFProtect()


def create_app(overrides=None, store=None):
    # Google may return extra previously-granted scopes; that is expected, not an error.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

    app = Flask(__name__)
    cfg = Config()
    app.config.from_object(cfg)
    app.config["IS_PROD"] = cfg.is_prod
    if overrides:
        app.config.update(overrides)
    if not app.config["IS_PROD"]:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # http://localhost callbacks

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app.config["IS_PROD"],
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        WTF_CSRF_TIME_LIMIT=None,
    )
    if app.config["IS_PROD"] and app.config["SECRET_KEY"] == "dev-only-not-secret":
        raise RuntimeError("SECRET_KEY must be set in production")

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    crypto.init(app.config["TOKEN_ENC_KEY"])
    stripe.api_key = app.config["STRIPE_SECRET_KEY"]
    app.store = store or make_store(cfg if not overrides else _Cfg(app.config))

    csrf.init_app(app)

    from .auth import bp as auth_bp
    from .billing import bp as billing_bp
    from .hooks import bp as hooks_bp
    from .views import bp as views_bp

    for bp in (auth_bp, billing_bp, hooks_bp, views_bp):
        app.register_blueprint(bp)
    csrf.exempt(hooks_bp)
    csrf.exempt("app.billing.webhook")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; "
            # Chrome applies form-action to redirects after a POST, so list every redirect target.
            "form-action 'self' https://accounts.google.com https://checkout.stripe.com "
            "https://billing.stripe.com; "
            "frame-ancestors 'none'")
        if app.config["IS_PROD"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.context_processor
    def inject_globals():
        from .access import plan_state
        from .security import current_user
        user = current_user()
        return {
            "app_name": app.config["APP_NAME"],
            "support_email": app.config["SUPPORT_EMAIL"],
            "user": user,
            "state": plan_state(user) if user else None,
            "reauth": bool(user and user.get("gmail_status") == "reauth_required"),
        }

    return app


class _Cfg:
    def __init__(self, d):
        self.STORE, self.GCP_PROJECT = d["STORE"], d["GCP_PROJECT"]
