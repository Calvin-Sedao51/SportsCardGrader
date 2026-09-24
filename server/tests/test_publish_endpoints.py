import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import Hs256Verifier, issue_app_token
from app.auth_routes import AuthState
from app.hive.queue import PublishQueue
from app.main import app
from app.publish_routes import DEFAULT_PUBLISH_RATE_BUDGET, HiveState, PublishRateLimiter
from app.users import UserStore
from tests.test_hive_record import COMMUNITY, make_record
from tests.test_publish_queue import FakeClock, FakeHive

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 32

# Publish now requires sign-in; this backs a default signed-in caller so the
# existing publish tests keep exercising the happy path without each one
# wiring up auth by hand.
AUTH_SECRET = "test-jwt-secret"
DEFAULT_USER_ID = "test-user-id"
DEFAULT_GOOGLE_SUB = "test-google-sub"


def auth_headers(user_id=DEFAULT_USER_ID, google_sub=DEFAULT_GOOGLE_SUB,
                 email="calvin@example.com"):
    token = issue_app_token(secret=AUTH_SECRET, user_id=user_id, google_sub=google_sub,
                            email=email)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client_unconfigured():
    app.state.hive = None
    return TestClient(app)


@pytest.fixture
def hive():
    return FakeHive()


@pytest.fixture
def client(tmp_path, hive):
    async def instant_sleep(_):
        pass

    async def image_host(request):
        if request.url.host == "images.hive.blog":
            return httpx.Response(200, json={"url": f"https://images.hive.blog/DQm/{request.url.path.split('/')[-1][:6]}.jpg"})
        raise AssertionError(f"unexpected host {request.url.host}")

    queue = PublishQueue(tmp_path, hive, COMMUNITY, clock=FakeClock(),
                         sleeper=instant_sleep)
    app.state.hive = HiveState(
        client=hive, queue=queue, community=COMMUNITY,
        http=httpx.AsyncClient(transport=httpx.MockTransport(image_host)),
        # Any valid WIF works for signing in tests.
        posting_key="5KQwrPbwdL6PhXujxW37FSSQZ1JiwsST4cqQzDeyXtP79zkvFD3",
        fallback_token="", dry_run=False,
    )
    app.state.auth = AuthState(verifier=Hs256Verifier(AUTH_SECRET), client_id="test-client",
                               secret=AUTH_SECRET, users=UserStore(tmp_path / "users.json"))
    yield TestClient(app)
    app.state.hive = None
    app.state.auth = None


def draft_json(record_id="4fef9db2-9f3a-4c5e-8f6d-0123456789ab") -> str:
    record = make_record(record_id=record_id)
    data = json.loads(record.model_dump_json(exclude={"images"}))
    return json.dumps(data)


def post_publish(client, record=None, record_id="4fef9db2-9f3a-4c5e-8f6d-0123456789ab",
                 front=JPEG, headers=None):
    return client.post("/api/publish", data={"record": record or draft_json(record_id)},
                       files={"front": ("front.jpg", io.BytesIO(front), "image/jpeg")},
                       headers=auth_headers() if headers is None else headers)


def test_publish_503_when_unconfigured(client_unconfigured):
    resp = post_publish(client_unconfigured)
    assert resp.status_code == 503


def test_publish_happy_path(client):
    resp = post_publish(client)
    assert resp.status_code == 202
    body = resp.json()
    assert body["permlink"].startswith("card-luka-doncic-2018-")
    assert body["status"] == "queued"
    assert body["position"] == 1
    assert body["eta_seconds"] >= 0


def test_publish_idempotent_on_record_id(client):
    first = post_publish(client).json()
    resp = post_publish(client)
    assert resp.status_code == 200
    assert resp.json()["job_id"] == first["job_id"]


def test_publish_rejects_bad_record_json(client):
    resp = post_publish(client, record="{not json")
    assert resp.status_code == 422


def test_publish_rejects_bad_image(client):
    resp = post_publish(client, front=b"GIF89a not an allowed type")
    assert resp.status_code == 422


def test_job_status_roundtrip(client):
    job_id = post_publish(client).json()["job_id"]
    resp = client.get(f"/api/publish/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert client.get("/api/publish/nope").status_code == 404


def test_dry_run_skips_image_upload(client):
    # A dry-run rehearsal must not push permanent bytes to the real CDN.
    state = app.state.hive
    state.dry_run = True
    state.http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("network used in dry run"))))
    resp = post_publish(client)
    assert resp.status_code == 202
    job = state.queue.get_job(resp.json()["job_id"])
    assert job.record.images.front.startswith("dry-run://")


def test_hive_status_unconfigured(client_unconfigured):
    body = client_unconfigured.get("/api/hive/status").json()
    assert body == {"configured": False}


def test_hive_status_configured(client):
    body = client.get("/api/hive/status").json()
    assert body["configured"] is True
    assert body["community"] == COMMUNITY
    assert body["rc_percent"] == 80.0
    assert body["queue_depth"] == 0


def test_publish_requires_sign_in(client):
    resp = post_publish(client, headers={})
    assert resp.status_code == 401
    assert "Sign in to publish" in resp.json()["detail"]


def test_publish_rejects_bad_token(client):
    resp = post_publish(client, headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_publish_signed_in_returns_202(client):
    resp = post_publish(client, headers=auth_headers())
    assert resp.status_code == 202


def test_publish_rate_limit_50th_allowed_51st_blocked(client):
    state = app.state.hive
    assert state.rate_limiter.max_per_window == DEFAULT_PUBLISH_RATE_BUDGET
    for i in range(DEFAULT_PUBLISH_RATE_BUDGET):
        resp = post_publish(client, record_id=f"4fef9db2-9f3a-4c5e-8f6d-{i:012x}")
        assert resp.status_code == 202, f"job {i} should be within budget"
    resp = post_publish(client, record_id="4fef9db2-9f3a-4c5e-8f6d-ffffffffffff")
    assert resp.status_code == 429
    assert "hourly publish limit" in resp.json()["detail"]


def test_publish_idempotent_resubmit_does_not_spend_budget(client):
    # A resubmit of an already-queued record is not a new chain post, so
    # repeated resubmits must never themselves trip a tight budget.
    app.state.hive.rate_limiter = PublishRateLimiter(max_per_window=1)
    first = post_publish(client)
    assert first.status_code == 202
    for _ in range(5):
        resubmit = post_publish(client)
        assert resubmit.status_code == 200


def test_publish_rate_limit_is_per_user(client):
    app.state.hive.rate_limiter = PublishRateLimiter(max_per_window=1)
    first = post_publish(client, record_id="4fef9db2-9f3a-4c5e-8f6d-bbbbbbbbbbbb")
    assert first.status_code == 202
    second = post_publish(client, record_id="4fef9db2-9f3a-4c5e-8f6d-cccccccccccc")
    assert second.status_code == 429

    other_user = auth_headers(user_id="other-user-id", google_sub="other-google-sub")
    third = post_publish(client, record_id="4fef9db2-9f3a-4c5e-8f6d-dddddddddddd",
                         headers=other_user)
    assert third.status_code == 202
