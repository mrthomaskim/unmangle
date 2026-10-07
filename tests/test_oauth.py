from urllib.parse import parse_qs, urlparse

from tests.conftest import make_user


def _q(location):
    return {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


def test_login_requests_only_profile_scopes_with_pkce(client):
    r = client.get("/login")
    assert r.status_code == 302 and r.location.startswith("https://accounts.google.com/")
    q = _q(r.location)
    assert "gmail" not in q["scope"] and "openid" in q["scope"]
    assert q["code_challenge_method"] == "S256" and q["redirect_uri"] == "http://localhost/auth/callback"


def test_connect_gmail_requests_offline_gmail_scope(client, store, login):
    make_user(store, connected=False)
    login()
    q = _q(client.post("/connect-gmail").location)
    assert "https://www.googleapis.com/auth/gmail.modify" in q["scope"]
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert q["include_granted_scopes"] == "true" and q["login_hint"] == "u1@example.com"


def test_callback_rejects_state_mismatch(client):
    client.get("/login")
    r = client.get("/auth/callback?state=forged&code=x")
    assert r.status_code == 302 and urlparse(r.location).path == "/"


def test_callback_handles_denied_consent(client, store, login):
    make_user(store, connected=False)
    login()
    state = _q(client.post("/connect-gmail").location)["state"]
    r = client.get(f"/auth/callback?state={state}&error=access_denied")
    assert urlparse(r.location).path == "/dashboard"
    assert store.get_user("u1")["gmail_connected"] is False


def test_finish_gmail_stores_encrypted_token_and_starts_watch(app, store, fake_gmail):
    from types import SimpleNamespace

    from flask import session

    from app import auth, crypto
    make_user(store, connected=False, onboarded=False)
    creds = SimpleNamespace(granted_scopes=["openid", "https://www.googleapis.com/auth/gmail.modify"],
                            scopes=None, refresh_token="1//refresh")
    with app.test_request_context():
        session["uid"] = "u1"
        r = auth._finish_gmail({"email": "Thomas@Gmail.com"}, creds)
    u = store.get_user("u1")
    assert r.location.endswith("/onboarding")
    assert u["gmail_email"] == "thomas@gmail.com" and u["gmail_connected"]
    assert u["refresh_token_enc"] != "1//refresh" and crypto.decrypt(u["refresh_token_enc"]) == "1//refresh"
    assert fake_gmail.watching and u["history_id"] == fake_gmail.history_id


def test_finish_gmail_rejects_missing_scope(app, store, fake_gmail):
    from types import SimpleNamespace

    from flask import session

    from app import auth
    make_user(store, connected=False)
    creds = SimpleNamespace(granted_scopes=["openid"], scopes=None, refresh_token="x")
    with app.test_request_context():
        session["uid"] = "u1"
        auth._finish_gmail({"email": "t@gmail.com"}, creds)
    assert not store.get_user("u1")["gmail_connected"]
