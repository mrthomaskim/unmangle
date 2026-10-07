"""Google-only sign-in, then a separate, incremental consent step for Gmail access."""
from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, request, session, url_for
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from . import crypto, gmail
from .access import has_access, now
from .security import current_user, login_required

bp = Blueprint("auth", __name__)

SIGNIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
GMAIL_SCOPES = SIGNIN_SCOPES + [gmail.GMAIL_SCOPE]


def _flow(scopes, state=None, code_verifier=None):
    cfg = current_app.config
    client = {"web": {
        "client_id": cfg["GOOGLE_CLIENT_ID"],
        "client_secret": cfg["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}
    flow = Flow.from_client_config(client, scopes=scopes, state=state, code_verifier=code_verifier,
                                   autogenerate_code_verifier=code_verifier is None)
    flow.redirect_uri = cfg["BASE_URL"] + url_for("auth.callback")
    return flow


def _start(kind, scopes, **params):
    flow = _flow(scopes)
    url, state = flow.authorization_url(**params)
    session["oauth"] = {"kind": kind, "state": state, "verifier": flow.code_verifier,
                        "next": session.get("oauth_next")}
    return redirect(url)


def _safe_next(target):
    return target if target and urlparse(target).netloc == "" and target.startswith("/") else None


@bp.route("/login")
def login():
    if current_user():
        return redirect(url_for("views.dashboard"))
    session["oauth_next"] = _safe_next(request.args.get("next"))
    return _start("signin", SIGNIN_SCOPES, prompt="select_account")


@bp.route("/connect-gmail", methods=["POST"])
@login_required
def connect_gmail():
    user = current_user()
    return _start("gmail", GMAIL_SCOPES, access_type="offline", prompt="consent",
                  include_granted_scopes="true", login_hint=user.get("gmail_email") or user["email"])


@bp.route("/auth/callback")
def callback():
    pending = session.pop("oauth", None)
    if not pending or request.args.get("state") != pending["state"]:
        flash("That sign-in link expired. Try again.", "error")
        return redirect(url_for("views.index"))
    if request.args.get("error"):
        flash("Google sign-in was cancelled." if pending["kind"] == "signin"
              else "Gmail wasn't connected because access wasn't granted.", "error")
        return redirect(url_for("views.dashboard" if current_user() else "views.index"))

    scopes = SIGNIN_SCOPES if pending["kind"] == "signin" else GMAIL_SCOPES
    flow = _flow(scopes, state=pending["state"], code_verifier=pending["verifier"])
    flow.fetch_token(code=request.args["code"])
    creds = flow.credentials
    claims = id_token.verify_oauth2_token(
        creds.id_token, google_requests.Request(), current_app.config["GOOGLE_CLIENT_ID"])
    if not claims.get("email_verified"):
        flash("Your Google account email isn't verified.", "error")
        return redirect(url_for("views.index"))

    if pending["kind"] == "signin":
        return _finish_signin(claims, pending.get("next"))
    return _finish_gmail(claims, creds)


def _finish_signin(claims, next_url):
    store, uid, email = current_app.store, claims["sub"], claims["email"].lower()
    user = store.get_user(uid)
    if not user:
        owner = store.trial_owner(email)
        trial_end = now() + timedelta(days=current_app.config["TRIAL_DAYS"])
        if owner and owner != uid:
            trial_end = now()  # this address already used its trial on a deleted account
        store.create_user(uid, {
            "email": email, "name": claims.get("name", ""), "created_at": now(),
            "trial_ends_at": trial_end, "keywords": [], "allowlist": [],
            "use_default_rules": True, "digest_enabled": True, "moved_count": 0,
            "gmail_connected": False, "onboarded": False,
        })
        store.claim_trial(email, uid)
    session.clear()
    session.permanent = True
    session["uid"] = uid
    return redirect(next_url or url_for("views.dashboard"))


def _finish_gmail(claims, creds):
    user = current_user()
    if not user:
        return redirect(url_for("auth.login"))
    store = current_app.store
    granted = set(creds.granted_scopes or creds.scopes or [])
    if gmail.GMAIL_SCOPE not in granted:
        flash("Gmail wasn't connected. On Google's screen, tick the box that lets "
              f"{current_app.config['APP_NAME']} manage your mail, then try again.", "error")
        return redirect(url_for("views.dashboard"))
    if not creds.refresh_token:
        flash("Google didn't return offline access. Remove the app at myaccount.google.com/connections "
              "and connect again.", "error")
        return redirect(url_for("views.dashboard"))

    gmail_email = claims["email"].lower()
    other = store.find_user("gmail_email", gmail_email)
    if other and other["uid"] != user["uid"]:
        flash(f"{gmail_email} is already connected to another {current_app.config['APP_NAME']} account.", "error")
        return redirect(url_for("views.dashboard"))

    # A second Gmail address doesn't earn a second trial.
    owner = store.trial_owner(gmail_email)
    fields = {}
    if owner and owner != user["uid"] and not user.get("subscription_status"):
        fields["trial_ends_at"] = min(user["trial_ends_at"], now())
    store.claim_trial(gmail_email, user["uid"])

    replacing = user.get("refresh_token_enc") and user.get("gmail_email") != gmail_email
    if replacing:
        gmail.stop_watch(user)
        gmail.revoke(user)
    fields.update({
        "gmail_email": gmail_email, "gmail_connected": True, "gmail_status": "ok",
        "refresh_token_enc": crypto.encrypt(creds.refresh_token),
        "history_id": None if replacing or not user.get("history_id") else user["history_id"],
    })
    store.update_user(user["uid"], fields)
    user = store.get_user(user["uid"])
    if has_access(user):
        gmail.sync_watch(user)
    if not user.get("onboarded"):
        return redirect(url_for("views.onboarding"))
    flash(f"Connected {gmail_email}.", "ok")
    return redirect(url_for("views.dashboard"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("views.index"))
