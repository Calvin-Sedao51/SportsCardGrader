"""Attribution round-trip: user identity into a post and back out of the feed.

Never touches the real Hive bridge: posts are built with build_post and read
back through the same shape the bridge returns (FakeHive / as_bridge_post).
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import Hs256Verifier, encode_hs256
from app.auth_routes import AuthState
from app.hive.post_builder import build_post
from app.hive.queue import PublishQueue
from app.hive.record import Attribution, attribution_from_post, card_permlink
from app.main import app
from app.publish_routes import HiveState
from app.users import UserStore, hive_display_key, user_id_for
from tests.test_auth import CLIENT_ID, NOW, google_claims
from tests.test_cards_endpoints import FeedHive, HUMAN_POST, as_bridge_post
from tests.test_hive_record import COMMUNITY, make_record
from tests.test_publish_endpoints import JPEG, draft_json
from tests.test_publish_queue import FakeClock, FakeHive, make_queue

GOOGLE_FAKE_SECRET = "fake-google-signing-secret"
APP_SECRET = "app-jwt-secret"
SUB = google_claims()["sub"]
KEY = hive_display_key(SUB)
UID = user_id_for(SUB)


def attributed(**overrides):
    return make_record(attribution=Attribution(
        client_id="c1", display_name="Calvin", user_id=UID, hive_display_key=KEY), **overrides)


# -- record model + post builder ---------------------------------------------

def test_attribution_fields_are_optional_for_anonymous_scans():
    anon = Attribution(client_id="c1")
    assert anon.user_id is None and anon.hive_display_key is None and anon.display_name is None


def test_post_builder_emits_machine_readable_attribution_block():
    ops = build_post(attributed(), community=COMMUNITY, account="thebinder", permlink="p")
    meta = json.loads(ops[0][1]["json_metadata"])
    assert meta["attribution"] == {"user_id": UID, "hive_display_key": KEY,
                                   "display_name": "Calvin"}
    assert "| Collector | Calvin |" in ops[0][1]["body"]
    assert len(json.dumps(meta)) < 8192


def test_post_builder_anonymous_block_has_no_identity():
    ops = build_post(make_record(), community=COMMUNITY, account="thebinder", permlink="p")
    meta = json.loads(ops[0][1]["json_metadata"])
    assert meta["attribution"] == {"user_id": None, "hive_display_key": None,
                                   "display_name": None}
    assert "Collector" not in ops[0][1]["body"]


def test_attribution_never_carries_email_or_google_sub():
    ops = build_post(attributed(), community=COMMUNITY, account="thebinder", permlink="p")
    blob = ops[0][1]["json_metadata"] + ops[0][1]["body"]
    assert SUB not in blob and "calvin@example.com" not in blob


# -- record parser ------------------------------------------------------------

def test_attribution_round_trips_through_a_bridge_post():
    record = attributed()
    parsed = attribution_from_post(as_bridge_post(record))
    assert parsed == record.attribution


def test_attribution_round_trips_without_identity():
    record = make_record()
    parsed = attribution_from_post(as_bridge_post(record))
    assert parsed == Attribution(client_id="c1")
    assert parsed.user_id is None


def test_attribution_falls_back_to_card_block_for_older_posts():
    # Posts published before the top-level block existed only have card.attribution.
    post = as_bridge_post(attributed())
    del post["json_metadata"]["attribution"]
    assert attribution_from_post(post).hive_display_key == KEY


def test_attribution_is_none_for_non_card_posts():
    assert attribution_from_post(HUMAN_POST) is None
    assert attribution_from_post({"json_metadata": "not a dict"}) is None
    assert attribution_from_post({}) is None


# -- publish queue persists the user id ---------------------------------------

async def test_queue_persists_user_id_per_job(tmp_path):
    hive, clock = FakeHive(), FakeClock()
    q1 = make_queue(tmp_path, hive, clock)
    job = q1.enqueue(attributed(), user_id=UID)
    assert job.user_id == UID
    assert json.loads((tmp_path / f"{job.job_id}.json").read_text())["user_id"] == UID
    q2 = make_queue(tmp_path, hive, clock)  # restart
    assert q2.get_job(job.job_id).user_id == UID
    assert q2.get_job(job.job_id).record.attribution.hive_display_key == KEY
    anonymous = q1.enqueue(make_record(record_id="anon"))
    assert anonymous.user_id is None


# -- endpoints: the server, not the client, decides attribution ---------------

@pytest.fixture
def auth_state(tmp_path):
    state = AuthState(verifier=Hs256Verifier(GOOGLE_FAKE_SECRET), client_id=CLIENT_ID,
                      secret=APP_SECRET, users=UserStore(tmp_path / "users.json"),
                      shared_hive_account="thebinder", clock=lambda: NOW)
    app.state.auth = state
    yield state
    app.state.auth = None


@pytest.fixture
def hive():
    mine = attributed(record_id="mine")
    other = make_record(record_id="theirs", attribution=Attribution(
        client_id="c2", display_name="Someone", user_id="other-user",
        hive_display_key="binder-00000000"))
    return FeedHive([as_bridge_post(mine), HUMAN_POST, as_bridge_post(other),
                     as_bridge_post(make_record(record_id="anon"))])


@pytest.fixture
def client(tmp_path, hive, auth_state):
    async def image_host(request):
        return httpx.Response(200, json={"url": "https://images.hive.blog/DQm/x.jpg"})

    queue = PublishQueue(tmp_path / "q", hive, COMMUNITY, clock=FakeClock())
    app.state.hive = HiveState(client=hive, queue=queue, community=COMMUNITY,
                               http=httpx.AsyncClient(transport=httpx.MockTransport(image_host)),
                               posting_key="5KQwrPbwdL6PhXujxW37FSSQZ1JiwsST4cqQzDeyXtP79zkvFD3")
    yield TestClient(app)
    app.state.hive = None


def bearer(client):
    token = client.post("/api/auth/google",
                        json={"credential": encode_hs256(google_claims(), GOOGLE_FAKE_SECRET)})
    return {"Authorization": f"Bearer {token.json()['token']}"}


def post_publish(client, headers=None, record=None):
    import io
    return client.post("/api/publish", data={"record": record or draft_json("new-rec")},
                       files={"front": ("front.jpg", io.BytesIO(JPEG), "image/jpeg")},
                       headers=headers or {})


def test_signed_in_publish_is_stamped_from_the_user_store(client):
    resp = post_publish(client, headers=bearer(client))
    assert resp.status_code == 202
    job = app.state.hive.queue.get_job("new-rec")
    assert job.user_id == UID
    assert job.record.attribution.user_id == UID
    assert job.record.attribution.hive_display_key == KEY
    assert job.record.attribution.display_name == "Calvin"
    assert job.record.attribution.client_id == "c1"  # install id still kept


def test_anonymous_publish_is_rejected_even_with_forged_attribution(client):
    # No token: publish is rejected outright, regardless of whatever identity
    # the client typed into the draft — a forged block must never win.
    draft = json.loads(draft_json("new-rec"))
    draft["attribution"] = {"client_id": "c1", "display_name": "Impostor",
                            "user_id": UID, "hive_display_key": KEY}
    resp = post_publish(client, record=json.dumps(draft))
    assert resp.status_code == 401
    assert app.state.hive.queue.get_job("new-rec") is None


def test_bad_token_publish_is_401_not_silently_anonymous(client):
    resp = post_publish(client, headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401
    assert app.state.hive.queue.get_job("new-rec") is None


def test_feed_entries_carry_attribution_and_owner_filter(client):
    body = client.get("/api/cards").json()
    by_id = {c["card"]["record_id"]: c for c in body["cards"]}
    assert by_id["mine"]["attribution"]["hive_display_key"] == KEY
    assert by_id["anon"]["attribution"]["hive_display_key"] is None
    mine = client.get(f"/api/cards?owner={KEY}").json()
    assert [c["card"]["record_id"] for c in mine["cards"]] == ["mine"]
    assert client.get("/api/cards?owner=binder-nobody").json()["cards"] == []
