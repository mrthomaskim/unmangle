from datetime import timedelta

import stripe
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from . import gmail, tasks
from .access import has_access, now, plan_state, trial_days_left
from .filtering import (DEFAULT_KEYWORDS, MAX_ALLOWLIST, MAX_KEYWORDS, clean_allow_entry,
                        clean_keyword, sender_address)
from .security import current_user, login_required

bp = Blueprint("views", __name__)


def _ctx(user):
    return {
        "user": user,
        "state": plan_state(user),
        "days_left": trial_days_left(user),
        "access": has_access(user),
        "reauth": user.get("gmail_status") == "reauth_required",
    }


@bp.route("/")
def index():
    if current_user():
        return redirect(url_for("views.dashboard"))
    return render_template("index.html")


@bp.route("/healthz")
def healthz():
    return "ok"


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/terms")
def terms():
    return render_template("terms.html")


# --------------------------------------------------------------------------- dashboard
@bp.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    week = current_app.store.list_activity(user["uid"], limit=500, since=now() - timedelta(days=7))
    recent = current_app.store.list_activity(user["uid"], limit=5)
    return render_template("dashboard.html", week_count=len(week), recent=recent, **_ctx(user))


# --------------------------------------------------------------------------- onboarding
@bp.route("/onboarding")
@login_required
def onboarding():
    user = current_user()
    if not user.get("gmail_connected"):
        return redirect(url_for("views.dashboard"))
    suggestions, scanned, error = [], 0, None
    try:
        suggestions, scanned = gmail.suggest_from_spam(user)
    except gmail.ReauthRequired:
        gmail.handle_reauth(user)
        return redirect(url_for("views.dashboard"))
    except Exception as e:  # don't block onboarding on a Gmail hiccup
        gmail.log("ERROR", "suggestion scan failed", uid=user["uid"], error=str(e)[:300])
        error = "We couldn't read your Spam folder just now. You can run this again from Rules."
    return render_template("onboarding.html", suggestions=suggestions, scanned=scanned,
                           error=error, from_rules=request.args.get("from") == "rules", **_ctx(user))


@bp.route("/onboarding", methods=["POST"])
@login_required
def onboarding_save():
    user = current_user()
    chosen = request.form.getlist("keyword")
    added = _add_keywords(user, chosen)
    current_app.store.update_user(user["uid"], {"onboarded": True})
    if request.form.get("then") == "rules":
        flash(f"Added {added} keyword{'s' if added != 1 else ''}.", "ok")
        return redirect(url_for("views.rules"))
    return redirect(url_for("views.onboarding_clean"))


@bp.route("/onboarding/clean")
@login_required
def onboarding_clean():
    return render_template("onboarding_clean.html", **_ctx(current_user()))


def _add_keywords(user, raw_list):
    keywords = list(user.get("keywords", []))
    added = 0
    for raw in raw_list:
        try:
            k = clean_keyword(raw)
        except ValueError:
            continue
        if k not in keywords and len(keywords) < MAX_KEYWORDS:
            keywords.append(k)
            added += 1
    current_app.store.update_user(user["uid"], {"keywords": keywords})
    return added


# --------------------------------------------------------------------------- rules
@bp.route("/rules")
@login_required
def rules():
    return render_template("rules.html", defaults=DEFAULT_KEYWORDS, **_ctx(current_user()))


@bp.route("/rules", methods=["POST"])
@login_required
def rules_update():
    user, store = current_user(), current_app.store
    action = request.form.get("action")
    try:
        if action == "add_keyword":
            k = clean_keyword(request.form.get("value", ""))
            kws = user.get("keywords", [])
            if k in kws:
                flash(f"“{k}” is already on your list.", "error")
            elif len(kws) >= MAX_KEYWORDS:
                flash(f"You can have up to {MAX_KEYWORDS} keywords.", "error")
            else:
                store.update_user(user["uid"], {"keywords": kws + [k]})
                flash(f"Added “{k}”. Mail whose sender or subject contains it will go to Spam.", "ok")
        elif action == "remove_keyword":
            store.update_user(user["uid"], {"keywords": [k for k in user.get("keywords", [])
                                                         if k != request.form.get("value")]})
        elif action == "add_allow":
            a = clean_allow_entry(request.form.get("value", ""))
            allow = user.get("allowlist", [])
            if a not in allow and len(allow) < MAX_ALLOWLIST:
                store.update_user(user["uid"], {"allowlist": allow + [a]})
            flash(f"Mail from {a} will always stay in your inbox.", "ok")
        elif action == "remove_allow":
            store.update_user(user["uid"], {"allowlist": [a for a in user.get("allowlist", [])
                                                          if a != request.form.get("value")]})
        elif action == "toggle_defaults":
            store.update_user(user["uid"], {"use_default_rules": request.form.get("enabled") == "1"})
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("views.rules"))


# --------------------------------------------------------------------------- activity
@bp.route("/activity")
@login_required
def activity():
    user = current_user()
    rows = current_app.store.list_activity(user["uid"], limit=200)
    return render_template("activity.html", rows=rows,
                           retention=current_app.config["ACTIVITY_RETENTION_DAYS"], **_ctx(user))


@bp.route("/activity/<msg_id>/restore", methods=["POST"])
@login_required
def restore(msg_id):
    user, store = current_user(), current_app.store
    row = store.get_activity(user["uid"], msg_id)
    if not row:
        flash("That message is no longer in your activity list.", "error")
        return redirect(url_for("views.activity"))
    try:
        gmail.restore(user, msg_id)
    except gmail.ReauthRequired:
        gmail.handle_reauth(user)
        flash("Reconnect Gmail to move messages back.", "error")
        return redirect(url_for("views.dashboard"))
    except Exception:
        flash("Gmail couldn't move that message. It may have been deleted.", "error")
        return redirect(url_for("views.activity"))
    store.update_activity(user["uid"], msg_id, {"restored": True})
    addr = sender_address(row["from"])
    if addr and addr not in user.get("allowlist", []):
        store.update_user(user["uid"], {"allowlist": user.get("allowlist", []) + [addr]})
        flash(f"Moved back to your inbox. Mail from {addr} will stay in your inbox from now on.", "ok")
    else:
        flash("Moved back to your inbox.", "ok")
    return redirect(url_for("views.activity"))


# --------------------------------------------------------------------------- cleanup
@bp.route("/backfill", methods=["POST"])
@login_required
def backfill():
    user = current_user()
    if not has_access(user) or not user.get("gmail_connected"):
        return redirect(url_for("views.dashboard"))
    if user.get("backfill_status") == "running":
        flash("A cleanup is already running.", "error")
        return redirect(url_for("views.dashboard"))
    days = 60 if request.form.get("days") != "30" else 30
    current_app.store.update_user(user["uid"], {"backfill_status": "running", "backfill_result": None,
                                                "onboarded": True})
    tasks.enqueue_backfill(user["uid"], days)
    flash(f"Cleaning up the last {days} days of your inbox. This can take a few minutes.", "ok")
    return redirect(url_for("views.dashboard"))


# --------------------------------------------------------------------------- settings
@bp.route("/settings")
@login_required
def settings():
    return render_template("settings.html", **_ctx(current_user()))


@bp.route("/settings", methods=["POST"])
@login_required
def settings_update():
    user = current_user()
    current_app.store.update_user(user["uid"], {"digest_enabled": request.form.get("digest") == "1"})
    flash("Settings saved.", "ok")
    return redirect(url_for("views.settings"))


@bp.route("/settings/disconnect", methods=["POST"])
@login_required
def disconnect():
    user = current_user()
    if user.get("refresh_token_enc"):
        gmail.stop_watch(user)
        gmail.revoke(user)
    current_app.store.update_user(user["uid"], {
        "gmail_connected": False, "refresh_token_enc": None, "gmail_email": None,
        "history_id": None, "watch_expiration": None, "gmail_status": None})
    flash("Gmail disconnected and access revoked. Filtering is off.", "ok")
    return redirect(url_for("views.settings"))


@bp.route("/settings/delete", methods=["POST"])
@login_required
def delete_account():
    user = current_user()
    if request.form.get("confirm", "").strip().lower() != user["email"]:
        flash("Type your email address exactly to confirm.", "error")
        return redirect(url_for("views.settings"))
    if user.get("refresh_token_enc"):
        gmail.stop_watch(user)
        gmail.revoke(user)
    if user.get("subscription_id") and user.get("subscription_status") not in (None, "canceled"):
        try:
            stripe.Subscription.cancel(user["subscription_id"])
        except stripe.StripeError as e:
            gmail.log("ERROR", "cancel on delete failed", uid=user["uid"], error=str(e))
    current_app.store.delete_user(user["uid"])
    session.clear()
    flash("Your account and data were deleted.", "ok")
    return redirect(url_for("views.index"))
