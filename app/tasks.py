"""Queues long jobs (inbox cleanup) on Cloud Tasks so web requests stay fast."""
import json

from flask import current_app

from . import gmail


def enqueue_backfill(uid, days, before=None):
    cfg = current_app.config
    payload = {"uid": uid, "days": days, "before": before}
    if not cfg["IS_PROD"]:
        return run_backfill(payload)  # local dev: run inline

    from google.cloud import tasks_v2
    from google.protobuf import duration_pb2

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(cfg["GCP_PROJECT"], cfg["TASKS_LOCATION"], cfg["TASKS_QUEUE"])
    client.create_task(parent=parent, task={
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": cfg["BASE_URL"] + "/tasks/backfill",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode(),
            "oidc_token": {"service_account_email": cfg["INVOKER_SA_EMAIL"],
                           "audience": cfg["OIDC_AUDIENCE"]},
        },
        "dispatch_deadline": duration_pb2.Duration(seconds=900),
    })


def run_backfill(payload):
    store = current_app.store
    user = store.get_user(payload["uid"])
    if not user:
        return
    try:
        checked, moved, next_before = gmail.backfill(user, payload["days"], payload.get("before"))
    except gmail.ReauthRequired:
        gmail.handle_reauth(user)
        store.update_user(user["uid"], {"backfill_status": "failed"})
        return
    prev = (user.get("backfill_result") or {}) if payload.get("before") else {}
    result = {"checked": prev.get("checked", 0) + checked, "moved": prev.get("moved", 0) + moved}
    store.update_user(user["uid"], {"backfill_result": result,
                                    "backfill_status": "running" if next_before else "done"})
    if next_before:
        enqueue_backfill(user["uid"], payload["days"], next_before)
