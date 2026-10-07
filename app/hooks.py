"""Machine-to-machine endpoints: Gmail push (via Pub/Sub), Cloud Tasks, Cloud Scheduler.
All require a Google-signed OIDC token from the invoker service account."""
import base64
import json
from datetime import timedelta

from flask import Blueprint, current_app, render_template, request
from googleapiclient.errors import HttpError

from . import gmail, mailer, tasks
from .access import has_access, now
from .security import oidc_required

bp = Blueprint("hooks", __name__)


@bp.route("/pubsub/gmail", methods=["POST"])
@oidc_required
def gmail_push():
    envelope = request.get_json(silent=True) or {}
    try:
        data = json.loads(base64.b64decode(envelope["message"]["data"]))
        address, history_id = data["emailAddress"].lower(), int(data["historyId"])
    except (KeyError, ValueError, TypeError):
        return "", 204  # malformed; ack so it isn't retried forever

    store = current_app.store
    user = store.find_user("gmail_email", address)
    if not user or not user.get("refresh_token_enc") or not has_access(user):
        return "", 204
    try:
        gmail.process_notification(user, history_id)
    except gmail.ReauthRequired:
        gmail.handle_reauth(user)
    except HttpError as e:
        if e.resp.status in (429, 500, 503):
            return "", 503  # Pub/Sub will retry with backoff
        gmail.log("ERROR", "push failed", uid=user["uid"], status=e.resp.status, error=str(e)[:300])
    return "", 204


@bp.route("/tasks/backfill", methods=["POST"])
@oidc_required
def backfill_task():
    tasks.run_backfill(request.get_json())
    return "", 204


@bp.route("/tasks/renew-watches", methods=["POST"])
@oidc_required
def renew_watches():
    """Daily: re-arm watches (they expire after 7 days) and switch off expired trials."""
    renewed = stopped = 0
    for user in current_app.store.users_with_gmail():
        before = bool(user.get("watch_expiration"))
        gmail.sync_watch(user)
        if has_access(user):
            renewed += 1
        elif before:
            stopped += 1
    gmail.log("INFO", "watches synced", renewed=renewed, stopped=stopped)
    return {"renewed": renewed, "stopped": stopped}, 200


@bp.route("/tasks/digest", methods=["POST"])
@oidc_required
def weekly_digest():
    store, sent = current_app.store, 0
    cutoff = now() - timedelta(days=7)
    for user in store.users_with_gmail():
        if not user.get("digest_enabled", True) or not has_access(user):
            continue
        since = max(cutoff, user.get("last_digest_at") or cutoff)
        rows = store.list_activity(user["uid"], limit=500, since=since)
        if not rows:
            continue
        senders = {}
        for r in rows:
            name = r["from"].split("<")[0].strip().strip('"') or r["from"]
            senders[name] = senders.get(name, 0) + 1
        top = sorted(senders.items(), key=lambda kv: -kv[1])[:8]
        ctx = {"user": user, "count": len(rows), "top": top,
               "activity_url": current_app.config["BASE_URL"] + "/activity",
               "settings_url": current_app.config["BASE_URL"] + "/settings"}
        try:
            mailer.send(user["email"], f"{current_app.config['APP_NAME']}: {len(rows)} spam emails blocked this week",
                        render_template("email/digest.txt", **ctx), render_template("email/digest.html", **ctx))
            store.update_user(user["uid"], {"last_digest_at": now()})
            sent += 1
        except Exception as e:
            gmail.log("ERROR", "digest failed", uid=user["uid"], error=str(e)[:300])
    return {"sent": sent}, 200
