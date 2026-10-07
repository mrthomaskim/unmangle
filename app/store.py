"""Data access. FirestoreStore in prod, MemoryStore for tests and local dev.

Layout
  users/{uid}                     account, billing, Gmail link, rules
  users/{uid}/activity/{msgId}    one row per message moved (expires_at drives a TTL policy)
  stripe_events/{eventId}         webhook idempotency
  trials/{sha256(email)}          one free trial per Google address, even after account deletion
"""
import copy
import hashlib
import threading
from datetime import timedelta

from .access import now


def email_hash(email: str) -> str:
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()


class MemoryStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.users, self.activity, self.events, self.trials = {}, {}, set(), {}

    # users
    def get_user(self, uid):
        u = self.users.get(uid)
        return copy.deepcopy(u) if u else None

    def create_user(self, uid, data):
        with self._lock:
            self.users[uid] = {"uid": uid, **copy.deepcopy(data)}
        return self.get_user(uid)

    def update_user(self, uid, fields):
        with self._lock:
            self.users[uid].update(copy.deepcopy(fields))

    def delete_user(self, uid):
        with self._lock:
            self.users.pop(uid, None)
            self.activity.pop(uid, None)

    def find_user(self, field, value):
        for u in self.users.values():
            if u.get(field) == value:
                return copy.deepcopy(u)
        return None

    def users_with_gmail(self):
        return [copy.deepcopy(u) for u in self.users.values() if u.get("refresh_token_enc")]

    def advance_history_id(self, uid, history_id):
        with self._lock:
            u = self.users[uid]
            if int(history_id) > int(u.get("history_id") or 0):
                u["history_id"] = int(history_id)

    def increment(self, uid, field, n):
        with self._lock:
            self.users[uid][field] = self.users[uid].get(field, 0) + n

    # activity
    def add_activity(self, uid, msg_id, row) -> bool:
        with self._lock:
            rows = self.activity.setdefault(uid, {})
            if msg_id in rows:
                return False
            rows[msg_id] = {"id": msg_id, **row}
            return True

    def get_activity(self, uid, msg_id):
        return copy.deepcopy(self.activity.get(uid, {}).get(msg_id))

    def update_activity(self, uid, msg_id, fields):
        with self._lock:
            self.activity[uid][msg_id].update(fields)

    def list_activity(self, uid, limit=100, since=None):
        rows = sorted(self.activity.get(uid, {}).values(), key=lambda r: r["ts"], reverse=True)
        if since:
            rows = [r for r in rows if r["ts"] >= since]
        return copy.deepcopy(rows[:limit])

    # idempotency + trials
    def event_seen(self, event_id):
        return event_id in self.events

    def mark_event(self, event_id):
        self.events.add(event_id)

    def trial_owner(self, email):
        return self.trials.get(email_hash(email))

    def claim_trial(self, email, uid):
        self.trials.setdefault(email_hash(email), uid)


class FirestoreStore:
    def __init__(self, project=None):
        from google.cloud import firestore
        self._fs = firestore
        self.db = firestore.Client(project=project)

    def _u(self, uid):
        return self.db.collection("users").document(uid)

    def get_user(self, uid):
        snap = self._u(uid).get()
        return {"uid": uid, **snap.to_dict()} if snap.exists else None

    def create_user(self, uid, data):
        self._u(uid).set({k: v for k, v in data.items() if k != "uid"})
        return self.get_user(uid)

    def update_user(self, uid, fields):
        self._u(uid).update(fields)

    def delete_user(self, uid):
        col = self._u(uid).collection("activity")
        while True:
            docs = list(col.limit(400).stream())
            if not docs:
                break
            batch = self.db.batch()
            for d in docs:
                batch.delete(d.reference)
            batch.commit()
        self._u(uid).delete()

    def find_user(self, field, value):
        from google.cloud.firestore_v1.base_query import FieldFilter
        for snap in self.db.collection("users").where(filter=FieldFilter(field, "==", value)).limit(1).stream():
            return {"uid": snap.id, **snap.to_dict()}
        return None

    def users_with_gmail(self):
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = self.db.collection("users").where(filter=FieldFilter("gmail_connected", "==", True))
        return [{"uid": s.id, **s.to_dict()} for s in q.stream()]

    def advance_history_id(self, uid, history_id):
        ref = self._u(uid)
        transaction = self.db.transaction()

        @self._fs.transactional
        def _txn(t):
            snap = ref.get(transaction=t)
            if int(history_id) > int((snap.to_dict() or {}).get("history_id") or 0):
                t.update(ref, {"history_id": int(history_id)})

        _txn(transaction)

    def increment(self, uid, field, n):
        self._u(uid).update({field: self._fs.Increment(n)})

    def add_activity(self, uid, msg_id, row) -> bool:
        from google.api_core.exceptions import AlreadyExists
        try:
            self._u(uid).collection("activity").document(msg_id).create(row)
            return True
        except AlreadyExists:
            return False

    def get_activity(self, uid, msg_id):
        snap = self._u(uid).collection("activity").document(msg_id).get()
        return {"id": msg_id, **snap.to_dict()} if snap.exists else None

    def update_activity(self, uid, msg_id, fields):
        self._u(uid).collection("activity").document(msg_id).update(fields)

    def list_activity(self, uid, limit=100, since=None):
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = self._u(uid).collection("activity")
        if since:
            q = q.where(filter=FieldFilter("ts", ">=", since))
        q = q.order_by("ts", direction=self._fs.Query.DESCENDING).limit(limit)
        return [{"id": s.id, **s.to_dict()} for s in q.stream()]

    def event_seen(self, event_id):
        return self.db.collection("stripe_events").document(event_id).get().exists

    def mark_event(self, event_id):
        self.db.collection("stripe_events").document(event_id).set(
            {"at": now(), "expires_at": now() + timedelta(days=30)})

    def trial_owner(self, email):
        snap = self.db.collection("trials").document(email_hash(email)).get()
        return snap.to_dict().get("uid") if snap.exists else None

    def claim_trial(self, email, uid):
        from google.api_core.exceptions import AlreadyExists
        try:
            self.db.collection("trials").document(email_hash(email)).create({"uid": uid, "at": now()})
        except AlreadyExists:
            pass


def make_store(cfg):
    if cfg.STORE == "memory":
        return MemoryStore()
    return FirestoreStore(cfg.GCP_PROJECT)
