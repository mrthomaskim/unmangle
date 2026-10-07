"""Stripe: Checkout to subscribe, Customer Portal to manage, webhooks as the source of truth.

Free trial runs inside the app (no card, no Stripe objects). When a trialing user subscribes,
the remaining trial days carry over to Stripe so they aren't charged early.
"""
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import stripe
from flask import Blueprint, abort, current_app, flash, redirect, request, url_for

from . import gmail
from .access import has_access, is_subscribed, now
from .security import current_user, login_required

bp = Blueprint("billing", __name__)

# Stripe Checkout requires trial_end to be at least 48h away.
MIN_CARRYOVER = timedelta(hours=49)


@lru_cache(maxsize=4)
def price_id(lookup_key):
    prices = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
    if not prices.data:
        raise RuntimeError(f"No active Stripe price with lookup_key={lookup_key}. Run scripts/stripe_setup.py.")
    return prices.data[0].id


def _lookup(plan):
    cfg = current_app.config
    return {"monthly": cfg["PRICE_LOOKUP_MONTHLY"], "annual": cfg["PRICE_LOOKUP_ANNUAL"]}.get(plan)


def _ensure_customer(user):
    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]
    customer = stripe.Customer.create(email=user["email"], name=user.get("name") or None,
                                      metadata={"uid": user["uid"]})
    current_app.store.update_user(user["uid"], {"stripe_customer_id": customer.id})
    return customer.id


@bp.route("/billing/checkout", methods=["POST"])
@login_required
def checkout():
    user = current_user()
    if is_subscribed(user):
        return portal()
    key = _lookup(request.form.get("plan", "monthly"))
    if not key:
        abort(400)
    base = current_app.config["BASE_URL"]
    sub_data = {"metadata": {"uid": user["uid"]}}
    trial_end = user.get("trial_ends_at")
    if trial_end and trial_end - now() > MIN_CARRYOVER:
        sub_data["trial_end"] = int(trial_end.timestamp())
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=_ensure_customer(user),
        client_reference_id=user["uid"],
        line_items=[{"price": price_id(key), "quantity": 1}],
        subscription_data=sub_data,
        allow_promotion_codes=True,
        success_url=base + url_for("billing.success") + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=base + url_for("views.dashboard"),
    )
    return redirect(session.url, code=303)


@bp.route("/billing/success")
@login_required
def success():
    # Webhooks are authoritative, but sync now so the dashboard is right on first load.
    sid = request.args.get("session_id")
    user = current_user()
    if sid:
        cs = stripe.checkout.Session.retrieve(sid)
        if cs.client_reference_id == user["uid"] and cs.subscription:
            sync_subscription(stripe.Subscription.retrieve(cs.subscription).to_dict())
    flash("You're subscribed. Thanks for supporting the project.", "ok")
    return redirect(url_for("views.dashboard"))


@bp.route("/billing/portal", methods=["POST"])
@login_required
def portal():
    user = current_user()
    if not user.get("stripe_customer_id"):
        return redirect(url_for("views.dashboard"))
    params = {"customer": user["stripe_customer_id"],
              "return_url": current_app.config["BASE_URL"] + url_for("views.dashboard")}
    if current_app.config.get("STRIPE_PORTAL_CONFIG"):
        params["configuration"] = current_app.config["STRIPE_PORTAL_CONFIG"]
    ps = stripe.billing_portal.Session.create(**params)
    return redirect(ps.url, code=303)


# --------------------------------------------------------------------------- webhook
@bp.route("/stripe/webhook", methods=["POST"])
def webhook():
    payload = request.get_data()
    try:
        stripe.Webhook.construct_event(payload, request.headers.get("Stripe-Signature"),
                                       current_app.config["STRIPE_WEBHOOK_SECRET"])
    except (ValueError, stripe.SignatureVerificationError):
        abort(400)
    event = json.loads(payload)  # plain dicts from here on
    store = current_app.store
    if store.event_seen(event["id"]):
        return "", 200

    obj, etype = event["data"]["object"], event["type"]
    if etype == "checkout.session.completed" and obj.get("mode") == "subscription":
        uid = obj.get("client_reference_id")
        if uid and store.get_user(uid):
            store.update_user(uid, {"stripe_customer_id": obj["customer"],
                                    "subscription_id": obj["subscription"]})
            sync_subscription(stripe.Subscription.retrieve(obj["subscription"]).to_dict())
    elif etype in ("customer.subscription.created", "customer.subscription.updated",
                   "customer.subscription.deleted", "customer.subscription.paused",
                   "customer.subscription.resumed"):
        sync_subscription(obj)
    elif etype == "invoice.payment_failed":
        gmail.log("WARNING", "payment failed", customer=obj.get("customer"))

    store.mark_event(event["id"])  # only after success, so Stripe retries on errors
    return "", 200


def _ts(v):
    return datetime.fromtimestamp(v, timezone.utc) if v else None


def sync_subscription(sub):
    store = current_app.store
    uid = (sub.get("metadata") or {}).get("uid")
    user = store.get_user(uid) if uid else store.find_user("stripe_customer_id", sub.get("customer"))
    if not user:
        gmail.log("WARNING", "subscription for unknown user", sub=sub.get("id"))
        return
    # Ignore stale events about an older, replaced subscription.
    if user.get("subscription_id") and user["subscription_id"] != sub["id"] \
            and user.get("subscription_status") in ("active", "trialing", "past_due") \
            and sub.get("status") not in ("active", "trialing", "past_due"):
        return

    item = (sub.get("items") or {}).get("data", [{}])[0]
    price = item.get("price") or {}
    lookup = price.get("lookup_key")
    plan = "annual" if lookup == current_app.config["PRICE_LOOKUP_ANNUAL"] else "monthly"
    # Newer API versions keep billing periods on the item, older ones on the subscription.
    period_end = item.get("current_period_end") or sub.get("current_period_end")
    store.update_user(user["uid"], {
        "stripe_customer_id": sub.get("customer"),
        "subscription_id": sub["id"],
        "subscription_status": sub.get("status"),
        "plan": plan,
        "current_period_end": _ts(period_end),
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end") or sub.get("cancel_at")),
    })
    gmail.log("INFO", "subscription synced", uid=user["uid"], status=sub.get("status"))
    after = store.get_user(user["uid"])
    if has_access(after) != bool(user.get("watch_expiration")):
        gmail.sync_watch(after)
