"""Everything that talks to a user's Gmail."""
import json
import time
from datetime import timedelta

import requests
from flask import current_app
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import crypto
from .access import has_access, now
from .filtering import classify
from .suggestions import build_suggestions

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
RETRIES = 6
BATCH_SIZE = 50                 # Gmail allows up to 100 per batch; 50 avoids per-batch throttling
BATCH_INTERVAL_SECONDS = 1.5    # ~33 reads/sec/user, well under 15k quota units/min


class ReauthRequired(Exception):
    """The user's Gmail grant was revoked or expired; they must reconnect."""


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, **fields}, default=str), flush=True)


# --------------------------------------------------------------------------- service
def _default_factory(user):
    cfg = current_app.config
    creds = Credentials(
        None,
        refresh_token=crypto.decrypt(user["refresh_token_enc"]),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["GOOGLE_CLIENT_ID"],
        client_secret=cfg["GOOGLE_CLIENT_SECRET"],
        scopes=[GMAIL_SCOPE],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


service_factory = _default_factory  # tests replace this


def _store():
    return current_app.store


def _mark_reauth(user):
    _store().update_user(user["uid"], {"gmail_status": "reauth_required", "watch_expiration": None})
    log("WARNING", "gmail grant revoked", uid=user["uid"])


def _exec(req):
    try:
        return req.execute(num_retries=RETRIES)
    except RefreshError as e:
        raise ReauthRequired() from e


def gmail_for(user):
    if not user.get("refresh_token_enc"):
        raise ReauthRequired()
    return service_factory(user)


# --------------------------------------------------------------------------- reading
def _headers_from(msg):
    h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
    return {
        "id": msg["id"],
        "from": h.get("from", ""),
        "subject": h.get("subject", ""),
        "to": h.get("to", ""),
        "labels": msg.get("labelIds", []),
        "internal_date": int(msg.get("internalDate", 0)),
    }


def fetch_headers(svc, ids, pace=False):
    """Fetches From/To/Subject for many messages using batch requests. Missing (deleted) ids are skipped."""
    out, pending = {}, list(ids)
    for attempt in range(4):
        failed = []
        for i in range(0, len(pending), BATCH_SIZE):
            chunk = pending[i:i + BATCH_SIZE]
            started = time.monotonic()

            def cb(request_id, response, exception, _failed=failed):
                if exception is None:
                    out[request_id] = _headers_from(response)
                elif isinstance(exception, HttpError) and exception.resp.status in (403, 429, 500, 503):
                    _failed.append(request_id)  # rate limited or transient: retry below
                # 404 = message deleted meanwhile; ignore

            batch = svc.new_batch_http_request(callback=cb)
            for mid in chunk:
                batch.add(svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "To"]), request_id=mid)
            try:
                batch.execute()
            except RefreshError as e:
                raise ReauthRequired() from e
            if pace or failed:
                time.sleep(max(0.0, BATCH_INTERVAL_SECONDS - (time.monotonic() - started)))
        if not failed:
            break
        pending = failed
        time.sleep(2 ** attempt)
    return [out[i] for i in ids if i in out]


def list_ids(svc, query, limit=None):
    ids, page = [], None
    while True:
        resp = _exec(svc.users().messages().list(userId="me", q=query, maxResults=500, pageToken=page))
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page or (limit and len(ids) >= limit):
            return ids[:limit] if limit else ids


# --------------------------------------------------------------------------- filtering
def _rules(user):
    return {
        "keywords": user.get("keywords", []),
        "use_defaults": user.get("use_default_rules", True),
        "allowlist": user.get("allowlist", []),
    }


def filter_messages(user, svc, headers):
    """Classifies messages, moves spam out of the inbox, records activity. Returns moved count."""
    rules = _rules(user)
    hits = []
    for h in headers:
        if "INBOX" not in h["labels"]:
            continue
        reason = classify(h["from"], h["subject"], h["to"], **rules)
        if reason:
            hits.append((h, reason))
    if not hits:
        return 0

    ids = [h["id"] for h, _ in hits]
    for i in range(0, len(ids), 1000):
        _exec(svc.users().messages().batchModify(userId="me", body={
            "ids": ids[i:i + 1000], "addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}))

    store, ts = _store(), now()
    expires = ts + timedelta(days=current_app.config["ACTIVITY_RETENTION_DAYS"])
    new = 0
    for h, reason in hits:
        if store.add_activity(user["uid"], h["id"], {
            "ts": ts, "expires_at": expires, "from": h["from"][:300],
            "subject": h["subject"][:300], "reason": reason, "restored": False,
        }):
            new += 1
            log("INFO", "moved to spam", uid=user["uid"], reason=reason)
    if new:
        store.increment(user["uid"], "moved_count", new)
    return new


def process_notification(user, pushed_history_id):
    """Handles one Gmail push: reads inbox additions since the last seen historyId."""
    store = _store()
    svc = gmail_for(user)
    start = user.get("history_id")
    if not start:
        store.advance_history_id(user["uid"], pushed_history_id)
        return 0

    ids, page, latest = [], None, int(pushed_history_id)
    try:
        while True:
            resp = _exec(svc.users().history().list(
                userId="me", startHistoryId=str(start), historyTypes=["messageAdded"],
                labelId="INBOX", pageToken=page))
            latest = max(latest, int(resp.get("historyId", 0)))
            for rec in resp.get("history", []):
                ids += [m["message"]["id"] for m in rec.get("messagesAdded", [])]
            page = resp.get("nextPageToken")
            if not page:
                break
    except HttpError as e:
        if e.resp.status != 404:
            raise
        # historyId too old (Gmail keeps about a week): fall back to a recent window.
        ids = list_ids(svc, "in:inbox newer_than:2d", limit=500)

    ids = list(dict.fromkeys(ids))
    moved = filter_messages(user, svc, fetch_headers(svc, ids)) if ids else 0
    store.advance_history_id(user["uid"], latest)
    return moved


def backfill(user, days, before=None, time_budget=780):
    """Scans the inbox newest-first. Returns (checked, moved, next_before or None when finished)."""
    svc = gmail_for(user)
    query = f"in:inbox newer_than:{int(days)}d" + (f" before:{int(before)}" if before else "")
    ids = list_ids(svc, query)
    started, checked, moved, oldest = time.monotonic(), 0, 0, None
    for i in range(0, len(ids), 500):
        if time.monotonic() - started > time_budget:
            return checked, moved, oldest
        headers = fetch_headers(svc, ids[i:i + 500], pace=len(ids) > BATCH_SIZE)
        moved += filter_messages(user, svc, headers)
        checked += len(headers)
        if headers:
            oldest = min(h["internal_date"] for h in headers) // 1000
    return checked, moved, None


def suggest_from_spam(user, max_messages=300):
    svc = gmail_for(user)
    ids = list_ids(svc, "in:spam newer_than:60d", limit=max_messages)
    headers = fetch_headers(svc, ids)  # 300 reads = 1,500 units: no pacing needed, keeps the page fast
    return build_suggestions(headers, **_rules(user)), len(headers)


def restore(user, msg_id):
    svc = gmail_for(user)
    _exec(svc.users().messages().modify(userId="me", id=msg_id, body={
        "addLabelIds": ["INBOX"], "removeLabelIds": ["SPAM"]}))


# --------------------------------------------------------------------------- watch
def start_watch(user):
    svc = gmail_for(user)
    resp = _exec(svc.users().watch(userId="me", body={
        "topicName": current_app.config["PUBSUB_TOPIC"],
        "labelIds": ["INBOX"], "labelFilterBehavior": "include"}))
    fields = {"watch_expiration": int(resp["expiration"]), "gmail_status": "ok"}
    _store().update_user(user["uid"], fields)
    if not user.get("history_id"):
        _store().advance_history_id(user["uid"], resp["historyId"])
    return resp


def stop_watch(user):
    try:
        _exec(gmail_for(user).users().stop(userId="me"))
    except (ReauthRequired, HttpError):
        pass
    _store().update_user(user["uid"], {"watch_expiration": None})


def sync_watch(user):
    """Ensures the watch matches the user's access: on while paid/trial, off otherwise."""
    if not user.get("refresh_token_enc") or user.get("gmail_status") == "reauth_required":
        return
    if not current_app.config.get("PUBSUB_TOPIC"):
        log("INFO", "PUBSUB_TOPIC not set; skipping Gmail watch (local dev)", uid=user["uid"])
        return
    try:
        if has_access(user):
            start_watch(user)
        elif user.get("watch_expiration"):
            stop_watch(user)
    except ReauthRequired:
        _mark_reauth(user)
    except HttpError as e:
        log("ERROR", "watch sync failed", uid=user["uid"], status=e.resp.status, error=str(e)[:300])


def revoke(user):
    try:
        token = crypto.decrypt(user["refresh_token_enc"])
        requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=10)
    except Exception as e:  # best effort; the user can also revoke in their Google account
        log("WARNING", "revoke failed", uid=user["uid"], error=str(e))


def handle_reauth(user):
    _mark_reauth(user)
