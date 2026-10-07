import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
import stripe

from app import billing
from app.access import has_access, now
from tests.conftest import make_user


@pytest.fixture
def no_sig(monkeypatch):
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda *a, **k: None)


def sub_event(eid, etype, uid, status, lookup="unmangle_monthly", sub_id="sub_1", **extra):
    return {"id": eid, "type": etype, "data": {"object": {
        "id": sub_id, "object": "subscription", "customer": "cus_1", "status": status,
        "metadata": {"uid": uid}, "cancel_at_period_end": False,
        "items": {"data": [{"price": {"lookup_key": lookup}, "current_period_end": 1_900_000_000}]},
        **extra}}}


def post_event(client, ev):
    return client.post("/stripe/webhook", data=json.dumps(ev), headers={"Stripe-Signature": "t"},
                       content_type="application/json")


def test_webhook_rejects_bad_signature(client):
    assert client.post("/stripe/webhook", data="{}", headers={"Stripe-Signature": "bad"}).status_code == 400


def test_subscription_lifecycle(client, store, fake_gmail, no_sig):
    make_user(store, trial_days=-1)
    assert post_event(client, sub_event("evt_1", "customer.subscription.created", "u1", "active",
                                        lookup="unmangle_annual")).status_code == 200
    u = store.get_user("u1")
    assert u["subscription_status"] == "active" and u["plan"] == "annual" and has_access(u)
    assert u["current_period_end"].year == 2030 and fake_gmail.watching

    # Replayed event is ignored.
    store.update_user("u1", {"subscription_status": "weird"})
    post_event(client, sub_event("evt_1", "customer.subscription.updated", "u1", "active"))
    assert store.get_user("u1")["subscription_status"] == "weird"

    post_event(client, sub_event("evt_2", "customer.subscription.deleted", "u1", "canceled"))
    u = store.get_user("u1")
    assert u["subscription_status"] == "canceled" and not has_access(u)
    assert not fake_gmail.watching and u["watch_expiration"] is None


def test_checkout_carries_over_remaining_trial(client, store, login, monkeypatch):
    captured = {}
    monkeypatch.setattr(billing, "price_id", lambda key: f"price_{key}")
    monkeypatch.setattr(stripe.Customer, "create", lambda **k: SimpleNamespace(id="cus_9"))
    monkeypatch.setattr(stripe.checkout.Session, "create",
                        lambda **k: captured.update(k) or SimpleNamespace(url="https://checkout.stripe.com/x"))
    make_user(store, trial_days=10)
    login()
    r = client.post("/billing/checkout", data={"plan": "annual"})
    assert r.status_code == 303 and r.location.startswith("https://checkout.stripe.com")
    assert captured["line_items"][0]["price"] == "price_unmangle_annual"
    assert captured["subscription_data"]["trial_end"] > now().timestamp() + 9 * 86400
    assert store.get_user("u1")["stripe_customer_id"] == "cus_9"

    # Less than 48h left: no carry-over (Stripe would reject it), charge starts now.
    store.update_user("u1", {"trial_ends_at": now() + timedelta(hours=20)})
    client.post("/billing/checkout", data={"plan": "monthly"})
    assert "trial_end" not in captured["subscription_data"]


def test_signin_creates_trial_once_per_address(app, store):
    from app.auth import _finish_signin
    claims = {"sub": "g-1", "email": "Thomas@Example.com", "name": "Thomas"}
    with app.test_request_context():
        _finish_signin(claims, None)
    u = store.get_user("g-1")
    assert u["email"] == "thomas@example.com"
    assert u["trial_ends_at"] - now() > timedelta(days=13)

    store.delete_user("g-1")
    with app.test_request_context():
        _finish_signin({**claims, "sub": "g-2"}, None)
    assert not has_access(store.get_user("g-2"))  # trial already used by this address


def test_pages_and_login_gate(client, store, login):
    assert client.get("/").status_code == 200
    assert "FID<span class=\"j\">.</span>ELI" in client.get("/").get_data(as_text=True)
    assert client.get("/privacy").status_code == 200
    r = client.get("/dashboard")
    assert r.status_code == 302 and "/login" in r.location

    make_user(store, connected=False)
    login()
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Connect your Gmail" in page and "14 days left" in page
    for path in ("/rules", "/activity", "/settings"):
        assert client.get(path).status_code == 200


def test_rules_normalize_and_validate(client, store, login):
    make_user(store)
    login()
    client.post("/rules", data={"action": "add_keyword", "value": "Metro via SafeOpt"})
    client.post("/rules", data={"action": "add_keyword", "value": "a.b"})
    client.post("/rules", data={"action": "add_allow", "value": "@gym.com"})
    u = store.get_user("u1")
    assert u["keywords"] == ["metroviasafeopt"] and u["allowlist"] == ["@gym.com"]
    client.post("/rules", data={"action": "toggle_defaults", "enabled": "0"})
    assert store.get_user("u1")["use_default_rules"] is False


def test_delete_account_requires_confirmation(client, store, fake_gmail, login):
    make_user(store)
    login()
    client.post("/settings/delete", data={"confirm": "wrong"})
    assert store.get_user("u1")
    client.post("/settings/delete", data={"confirm": "u1@example.com"})
    assert store.get_user("u1") is None


def test_prod_task_endpoints_require_oidc(app, client):
    app.config.update(IS_PROD=True, APP_ENV="prod")
    assert client.post("/tasks/renew-watches").status_code == 401
    assert client.post("/pubsub/gmail", json={}).status_code == 401


def test_csrf_on_forms_but_not_webhooks(app, store, login, no_sig, fake_gmail):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    make_user(store)
    with client.session_transaction() as s:
        s["uid"] = "u1"
    assert client.post("/rules", data={"action": "add_keyword", "value": "carshield"}).status_code == 400
    ev = sub_event("evt_c", "customer.subscription.updated", "u1", "active")
    assert post_event(client, ev).status_code == 200
    assert client.post("/pubsub/gmail", json={}).status_code == 204


def test_csp_allows_oauth_and_stripe_redirects(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    for host in ("https://accounts.google.com", "https://checkout.stripe.com", "https://billing.stripe.com"):
        assert host in csp
