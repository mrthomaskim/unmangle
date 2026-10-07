import base64
import json
from datetime import timedelta

from app.access import now
from tests.conftest import make_user


def push(client, email, history_id):
    data = base64.b64encode(json.dumps({"emailAddress": email, "historyId": history_id}).encode()).decode()
    return client.post("/pubsub/gmail", json={"message": {"data": data}, "subscription": "s"})


def test_push_moves_mangled_spam_once(client, store, fake_gmail):
    make_user(store)
    fake_gmail.add("m1", '"FID.ELI.TY LI.FE" <x@kitayamatriangle.com>', "WELCOME TO YOUR OFFER")
    fake_gmail.add("m2", "Mom <mom@family.com>", "Dinner?")

    assert push(client, "u1@gmail.com", fake_gmail.history_id).status_code == 204
    assert fake_gmail.messages["m1"]["labels"] == ["SPAM"]
    assert fake_gmail.messages["m2"]["labels"] == ["INBOX"]
    user = store.get_user("u1")
    assert user["moved_count"] == 1 and user["history_id"] == fake_gmail.history_id
    row = store.get_activity("u1", "m1")
    assert row["reason"] == "Built-in: fidelitylife" and row["expires_at"] > now()

    # Duplicate notification: no double counting.
    store.update_user("u1", {"history_id": 100})
    push(client, "u1@gmail.com", fake_gmail.history_id)
    assert store.get_user("u1")["moved_count"] == 1


def test_push_ignored_when_trial_expired(client, store, fake_gmail):
    make_user(store, trial_days=-1)
    fake_gmail.add("m1", '"C.ARSHIELD" <x@y.com>')
    push(client, "u1@gmail.com", fake_gmail.history_id)
    assert fake_gmail.messages["m1"]["labels"] == ["INBOX"]


def test_push_falls_back_when_history_expired(client, store, fake_gmail):
    make_user(store)
    fake_gmail.add("m1", '"E^NDURANCE" <x@y.com>')
    fake_gmail.history_gone = True
    push(client, "u1@gmail.com", fake_gmail.history_id)
    assert fake_gmail.messages["m1"]["labels"] == ["SPAM"]


def test_push_unknown_or_malformed_is_acked(client, fake_gmail):
    assert push(client, "nobody@gmail.com", 5).status_code == 204
    assert client.post("/pubsub/gmail", json={"message": {"data": "!!"}}).status_code == 204


def test_backfill_runs_inline_in_dev(client, store, fake_gmail, login):
    make_user(store)
    for i in range(120):
        fake_gmail.add(f"ok{i}", f"Friend {i} <f{i}@x.com>", add_history=False)
    fake_gmail.add("s1", '"A.UT.O.PLAN.A.DV.ISO.RS" <a@b.com>', add_history=False)
    login()
    r = client.post("/backfill", data={"days": "60"})
    assert r.status_code == 302
    u = store.get_user("u1")
    assert u["backfill_status"] == "done" and u["backfill_result"] == {"checked": 121, "moved": 1}
    assert fake_gmail.messages["s1"]["labels"] == ["SPAM"]


def test_onboarding_suggests_from_spam_and_saves(client, store, fake_gmail, login):
    make_user(store, onboarded=False)
    for i, name in enumerate(["FID.ELI.TY LI.FE", "FiDeLity.LiFe.TeaM", "Metro via SafeOpt", "Metro via SafeOpt"]):
        fake_gmail.add(f"sp{i}", f'"{name}" <a{i}@x.com>', labels=("SPAM",), add_history=False)
    login()
    page = client.get("/onboarding").get_data(as_text=True)
    assert "metroviasafeopt" in page
    assert "already caught by your current rules" in page  # fidelitylife is a default
    client.post("/onboarding", data={"keyword": ["metroviasafeopt"]})
    u = store.get_user("u1")
    assert u["keywords"] == ["metroviasafeopt"] and u["onboarded"]


def test_not_spam_restores_and_allowlists(client, store, fake_gmail, login):
    make_user(store)
    fake_gmail.add("m1", '"Endurance Fitness" <coach@gym.com>', "Class times")
    from tests.test_gmail_flow import push as _p
    _p(client, "u1@gmail.com", fake_gmail.history_id)
    assert fake_gmail.messages["m1"]["labels"] == ["SPAM"]
    login()
    client.post("/activity/m1/restore")
    assert "INBOX" in fake_gmail.messages["m1"]["labels"]
    assert store.get_user("u1")["allowlist"] == ["coach@gym.com"]
    assert store.get_activity("u1", "m1")["restored"]


def test_renew_watches_stops_expired_and_renews_active(client, store, fake_gmail):
    make_user(store, "active")
    make_user(store, "expired", trial_days=-1, watch_expiration=123)
    r = client.post("/tasks/renew-watches")
    assert r.get_json() == {"renewed": 1, "stopped": 1}
    assert store.get_user("active")["watch_expiration"]
    assert store.get_user("expired")["watch_expiration"] is None


def test_digest_sends_and_stamps(client, store, fake_gmail):
    make_user(store)
    store.add_activity("u1", "m1", {"ts": now() - timedelta(days=1), "from": '"C.ARSHIELD" <a@b.com>',
                                    "subject": "q", "reason": "r", "restored": False})
    make_user(store, "quiet")
    r = client.post("/tasks/digest")
    assert r.get_json() == {"sent": 1}
    assert store.get_user("u1")["last_digest_at"]
