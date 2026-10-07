from datetime import timedelta

import httplib2
import pytest
from cryptography.fernet import Fernet
from googleapiclient.errors import HttpError

from app import create_app, crypto, gmail
from app.access import now
from app.store import MemoryStore


def http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"{}")


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeGmail:
    """In-memory Gmail: messages dict id -> {from, subject, to, labels, date}."""

    def __init__(self):
        self.messages = {}
        self.history = []          # list of (history_id, [msg ids added])
        self.history_id = 100
        self.watching = False
        self.history_gone = False
        self.calls = []

    def add(self, mid, frm, subject="hi", to="me@gmail.com", labels=("INBOX",), add_history=True):
        self.messages[mid] = {"from": frm, "subject": subject, "to": to, "labels": list(labels),
                              "date": 1_700_000_000_000 + len(self.messages)}
        if add_history:
            self.history_id += 1
            self.history.append((self.history_id, [mid]))

    # --- API surface
    def users(self):
        return _Users(self)

    def new_batch_http_request(self, callback):
        return FakeBatch(callback)


class _Users:
    def __init__(self, g):
        self.g = g

    def messages(self):
        return _Messages(self.g)

    def history(self):
        return _History(self.g)

    def watch(self, userId, body):
        g = self.g

        def run():
            g.watching = True
            return {"historyId": str(g.history_id),
                    "expiration": str(int(now().timestamp() * 1000) + 7 * 86400000)}
        return Req(run)

    def stop(self, userId):
        g = self.g

        def run():
            g.watching = False
            return {}
        return Req(run)


class _Messages:
    def __init__(self, g):
        self.g = g

    def list(self, userId, q, maxResults=500, pageToken=None):
        def run():
            label = "SPAM" if "in:spam" in q else "INBOX"
            ids = [m for m, v in sorted(self.g.messages.items(), key=lambda kv: -kv[1]["date"])
                   if label in v["labels"]]
            return {"messages": [{"id": i} for i in ids]} if ids else {}
        return Req(run)

    def get(self, userId, id, format=None, metadataHeaders=None):
        def run():
            if id not in self.g.messages:
                raise http_error(404)
            m = self.g.messages[id]
            return {"id": id, "labelIds": list(m["labels"]), "internalDate": str(m["date"]),
                    "payload": {"headers": [{"name": "From", "value": m["from"]},
                                            {"name": "Subject", "value": m["subject"]},
                                            {"name": "To", "value": m["to"]}]}}
        return Req(run)

    def batchModify(self, userId, body):
        def run():
            self.g.calls.append(("batchModify", list(body["ids"])))
            for i in body["ids"]:
                labels = self.g.messages[i]["labels"]
                for l in body.get("removeLabelIds", []):
                    if l in labels:
                        labels.remove(l)
                labels.extend(l for l in body.get("addLabelIds", []) if l not in labels)
            return {}
        return Req(run)

    def modify(self, userId, id, body):
        return self.batchModify(userId, {"ids": [id], **body})


class _History:
    def __init__(self, g):
        self.g = g

    def list(self, userId, startHistoryId, historyTypes=None, labelId=None, pageToken=None):
        def run():
            if self.g.history_gone:
                raise http_error(404)
            recs = [{"id": str(h), "messagesAdded": [{"message": {"id": m}} for m in ids]}
                    for h, ids in self.g.history if h > int(startHistoryId)]
            return {"history": recs, "historyId": str(self.g.history_id)}
        return Req(run)


class FakeBatch:
    def __init__(self, callback):
        self.cb, self.reqs = callback, []

    def add(self, req, request_id):
        self.reqs.append((request_id, req))

    def execute(self):
        for rid, req in self.reqs:
            try:
                self.cb(rid, req.execute(), None)
            except HttpError as e:
                self.cb(rid, None, e)


@pytest.fixture
def fake_gmail(monkeypatch):
    g = FakeGmail()
    monkeypatch.setattr(gmail, "service_factory", lambda user: g)
    monkeypatch.setattr(gmail, "revoke", lambda user: None)
    monkeypatch.setattr(gmail, "BATCH_INTERVAL_SECONDS", 0)
    return g


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def app(store):
    app = create_app({
        "APP_ENV": "test", "IS_PROD": False, "STORE": "memory", "TESTING": True,
        "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test",
        "TOKEN_ENC_KEY": Fernet.generate_key().decode(),
        "GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret",
        "PUBSUB_TOPIC": "projects/p/topics/gmail-push",
        "STRIPE_WEBHOOK_SECRET": "whsec_test", "BASE_URL": "http://localhost",
        "OIDC_AUDIENCE": "http://localhost", "INVOKER_SA_EMAIL": "invoker@p.iam.gserviceaccount.com",
    }, store=store)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(store, uid="u1", *, connected=True, trial_days=14, **extra):
    data = {
        "email": f"{uid}@example.com", "name": "Jane Doe", "created_at": now(),
        "trial_ends_at": now() + timedelta(days=trial_days), "keywords": [], "allowlist": [],
        "use_default_rules": True, "digest_enabled": True, "moved_count": 0,
        "gmail_connected": connected, "onboarded": True,
    }
    if connected:
        data.update({"gmail_email": f"{uid}@gmail.com", "refresh_token_enc": crypto.encrypt("rt"),
                     "history_id": 100, "gmail_status": "ok"})
    data.update(extra)
    return store.create_user(uid, data)


@pytest.fixture
def login(client):
    def _login(uid="u1"):
        with client.session_transaction() as s:
            s["uid"] = uid
    return _login
