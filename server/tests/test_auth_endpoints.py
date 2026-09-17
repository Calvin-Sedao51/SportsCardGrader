"""POST /api/auth/google and /api/auth/hive with an offline Google double."""
import pytest
from fastapi.testclient import TestClient

from app.auth import Hs256Verifier, encode_hs256, read_app_token
from app.auth_routes import AuthState
from app.config import Settings
from app.hive.identity import HivePresence, hive_presence
from app.main import app
from app.users import UserStore, hive_display_key
from tests.test_auth import CLIENT_ID, NOW, google_claims

GOOGLE_FAKE_SECRET = "fake-google-signing-secret"
APP_SECRET = "app-jwt-secret"


def google_token(**overrides) -> str:
    return encode_hs256(google_claims(**overrides), GOOGLE_FAKE_SECRET)


@pytest.fixture
def client_unconfigured():
    app.state.auth = None
    return TestClient(app)


@pytest.fixture
def auth_state(tmp_path):
    return AuthState(verifier=Hs256Verifier(GOOGLE_FAKE_SECRET), client_id=CLIENT_ID,
                     secret=APP_SECRET, users=UserStore(tmp_path / "users.json"),
                     shared_hive_account="thebinder", clock=lambda: NOW)


@pytest.fixture
def client(auth_state):
    app.state.auth = auth_state
    yield TestClient(app)
    app.state.auth = None


def login(client, token=None):
    return client.post("/api/auth/google", json={"credential": token or google_token()})


def test_settings_auth_configured_needs_both_vars():
    assert Settings(auth_google_client_id="", auth_jwt_secret="").auth_configured is False
    assert Settings(auth_google_client_id=CLIENT_ID, auth_jwt_secret="").auth_configured is False
    assert Settings(auth_google_client_id=CLIENT_ID, auth_jwt_secret="s").auth_configured is True
    assert Settings().hive_account_mode == "shared"


def test_auth_503_when_unconfigured(client_unconfigured):
    assert login(client_unconfigured).status_code == 503
    assert client_unconfigured.post("/api/auth/hive").status_code == 503


def test_login_bad_aud_is_401(client):
    resp = login(client, google_token(aud="someone-else"))
    assert resp.status_code == 401


def test_login_missing_sub_is_401(client):
    assert login(client, google_token(sub=None)).status_code == 401


def test_login_bad_signature_and_garbage_are_401(client):
    forged = encode_hs256(google_claims(), "not-googles-secret")
    assert login(client, forged).status_code == 401
    assert login(client, "garbage").status_code == 401
    assert client.post("/api/auth/google", json={}).status_code == 422


def test_login_happy_path_returns_app_token_and_user(client, auth_state):
    resp = login(client)
    assert resp.status_code == 200
    body = resp.json()
    claims = read_app_token(body["token"], secret=APP_SECRET, now=NOW)
    assert claims.sub == google_claims()["sub"]
    assert claims.email == "calvin@example.com"
    assert body["user"]["id"] == claims.user_id
    assert body["user"]["display_name"] == "Calvin"
    assert body["user"]["hive_display_key"] == hive_display_key(google_claims()["sub"])
    assert body["created"] is True
    assert "google_sub" not in body["user"]  # raw sub never leaves the server


def test_login_is_idempotent_one_user(client, auth_state):
    first = login(client).json()
    second = login(client).json()
    assert first["user"]["id"] == second["user"]["id"]
    assert second["created"] is False
    assert auth_state.users.count() == 1


def test_hive_provision_requires_bearer(client):
    assert client.post("/api/auth/hive").status_code == 401
    assert client.post("/api/auth/hive", headers={"Authorization": "Bearer nope"}).status_code == 401
    expired = encode_hs256({"iss": "cardscanner", "sub": "g", "uid": "u", "exp": int(NOW) - 1},
                           APP_SECRET)
    assert client.post("/api/auth/hive",
                       headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_hive_provision_is_idempotent_and_returns_presence(client, auth_state):
    token = login(client).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    first = client.post("/api/auth/hive", headers=headers)
    second = client.post("/api/auth/hive", headers=headers)
    assert first.status_code == 200 and first.json() == second.json()
    body = first.json()
    assert body["display_name"] == "Calvin"
    assert body["hive_display_key"].startswith("binder-")
    assert body["hive"] == {"mode": "shared", "posting_account": "thebinder",
                            "display_key": body["hive_display_key"]}
    assert auth_state.users.count() == 1


def test_hive_provision_recreates_user_if_store_was_wiped(client, auth_state, tmp_path):
    # A valid app token outlives the file store: provisioning must self-heal.
    token = login(client).json()["token"]
    auth_state.users = UserStore(tmp_path / "fresh.json")
    resp = client.post("/api/auth/hive", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert auth_state.users.count() == 1


def test_per_user_hive_mode_is_a_dormant_shim():
    from app.users import User
    user = User(id="u", google_sub="g", email=None, display_name="Calvin",
                hive_display_key="binder-abcdef12", created_at="2026-09-16T00:00:00Z")
    assert hive_presence(user, mode="shared", shared_account="thebinder") == HivePresence(
        mode="shared", posting_account="thebinder", display_key="binder-abcdef12")
    with pytest.raises(NotImplementedError):
        hive_presence(user, mode="per_user", shared_account="thebinder")
    with pytest.raises(ValueError):
        hive_presence(user, mode="bogus", shared_account="thebinder")


def test_lifespan_wires_auth_state_from_env(monkeypatch, tmp_path):
    from app.config import get_settings
    from app.auth import TokeninfoVerifier
    monkeypatch.setenv("AUTH_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AUTH_JWT_SECRET", APP_SECRET)
    monkeypatch.setenv("AUTH_USERS_FILE", str(tmp_path / "users.json"))
    get_settings.cache_clear()
    with TestClient(app):
        state = app.state.auth
        assert isinstance(state, AuthState)
        assert isinstance(state.verifier, TokeninfoVerifier)
        assert state.client_id == CLIENT_ID and state.hive_account_mode == "shared"
        assert "app-jwt-secret" not in repr(state)  # secret never in logs/repr
    app.state.auth = None


def test_lifespan_refuses_unknown_hive_account_mode(monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("HIVE_ACCOUNT_MODE", "per_user")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
