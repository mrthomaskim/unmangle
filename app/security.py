from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token


def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = current_app.store.get_user(uid) if uid else None
        if uid and not g.user:
            session.clear()
    return g.user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def oidc_required(view):
    """For Pub/Sub push, Cloud Scheduler and Cloud Tasks: verify the Google-signed OIDC token."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        cfg = current_app.config
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            if not cfg["IS_PROD"] and cfg["APP_ENV"] in ("dev", "test"):
                return view(*args, **kwargs)  # local curl / tests
            abort(401)
        try:
            claims = id_token.verify_oauth2_token(
                auth[7:], google_requests.Request(), audience=cfg["OIDC_AUDIENCE"])
        except ValueError:
            abort(401)
        if not claims.get("email_verified") or claims.get("email") != cfg["INVOKER_SA_EMAIL"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapper
