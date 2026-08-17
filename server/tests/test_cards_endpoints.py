import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.hive.post_builder import build_post
from app.hive.queue import PublishQueue
from app.hive.record import card_permlink
from app.main import app
from app.publish_routes import HiveState
from tests.test_hive_record import COMMUNITY, make_record
from tests.test_publish_queue import FakeClock, FakeHive


def as_bridge_post(record, author="thebinder", created="2026-08-16T20:30:00"):
    ops = build_post(record, community=COMMUNITY, account=author,
                     permlink=card_permlink(record))
    comment = ops[0][1]
    return {
        "author": author, "permlink": comment["permlink"], "created": created,
        "title": comment["title"], "body": comment["body"],
        "json_metadata": json.loads(comment["json_metadata"]),
    }


HUMAN_POST = {"author": "collector99", "permlink": "hello-binder",
              "created": "2026-08-16T21:00:00", "title": "gm",
              "body": "hello", "json_metadata": {"app": "peakd"}}
MALFORMED = {"author": "thebinder", "permlink": "card-broken",
             "created": "2026-08-16T21:05:00", "title": "broken",
             "body": "", "json_metadata": {"card": {"v": 1, "kind": "card"}}}


class FeedHive(FakeHive):
    def __init__(self, feed):
        super().__init__()
        self.feed = feed
        self.ranked_calls = 0

    async def get_ranked_posts(self, **kwargs):
        self.ranked_calls += 1
        self.last_kwargs = kwargs
        return self.feed


@pytest.fixture
def records():
    return [make_record(record_id="r-one"), make_record(record_id="r-two")]


@pytest.fixture
def hive(records):
    feed = [as_bridge_post(records[0]), HUMAN_POST, MALFORMED,
            as_bridge_post(records[1], author="someoneelse")]
    return FeedHive(feed)


@pytest.fixture
def client(tmp_path, hive):
    queue = PublishQueue(tmp_path, hive, COMMUNITY, clock=FakeClock())
    app.state.hive = HiveState(client=hive, queue=queue, community=COMMUNITY,
                               http=httpx.AsyncClient(transport=httpx.MockTransport(
                                   lambda request: httpx.Response(500))))
    yield TestClient(app)
    app.state.hive = None


def test_cards_feed_filters_to_valid_app_cards(client, records):
    body = client.get("/api/cards").json()
    permalinks = [c["permlink"] for c in body["cards"]]
    # Human post and malformed metadata are skipped; other authors are
    # excluded by default (the app account is the publisher of record).
    assert permalinks == [card_permlink(records[0])]
    card = body["cards"][0]
    assert card["author"] == "thebinder"
    assert card["card"]["record_id"] == "r-one"
    assert body["next"] is None  # 4 raw posts < limit 20 ⇒ last page


def test_cards_feed_cursor_from_last_raw_post(client, records):
    # A full page: cursor continues from the LAST RAW post (not the last
    # surviving card) so filtering never skips a page.
    body = client.get("/api/cards?limit=4").json()
    assert body["next"] == {"start_author": "someoneelse",
                            "start_permlink": card_permlink(records[1])}


def test_cards_feed_all_authors(client, records):
    body = client.get("/api/cards?all_authors=1").json()
    assert [c["card"]["record_id"] for c in body["cards"]] == ["r-one", "r-two"]


def test_cards_feed_passes_cursor_and_community(client, hive):
    client.get("/api/cards?limit=5&start_author=a&start_permlink=p")
    assert hive.last_kwargs["tag"] == COMMUNITY
    assert hive.last_kwargs["limit"] == 5
    assert hive.last_kwargs["start_author"] == "a"
    assert hive.last_kwargs["start_permlink"] == "p"


def test_cards_feed_is_cached(client, hive):
    client.get("/api/cards")
    client.get("/api/cards")
    assert hive.ranked_calls == 1
    client.get("/api/cards?limit=5")  # different cursor = different cache key
    assert hive.ranked_calls == 2


def test_cards_feed_end_of_pagination(client, hive):
    hive.feed = hive.feed[:2]  # fewer raw posts than limit → no next page
    assert client.get("/api/cards?limit=20").json()["next"] is None


def test_card_detail(client, records, hive):
    permlink = card_permlink(records[0])
    hive.posts[permlink] = as_bridge_post(records[0])
    body = client.get(f"/api/cards/{permlink}").json()
    assert body["card"]["record_id"] == "r-one"
    assert body["hive_url"] == f"https://peakd.com/@thebinder/{permlink}"


def test_card_detail_404_for_missing_or_non_card(client):
    assert client.get("/api/cards/nope").status_code == 404


class FakeSource:
    source_type = "active_listings"


def test_refresh_comps_enqueues_an_update(client, records, hive, monkeypatch):
    from app import scan as scan_module
    from app.schemas import CompListing

    fresh = [CompListing(title=f"fresh {i}", price=80.0 + i, graded=False)
             for i in range(3)]

    async def fake_search(query, category="sports"):
        assert "Luka Doncic" in query
        return fresh

    monkeypatch.setattr(scan_module, "search_comps", fake_search)
    monkeypatch.setattr(scan_module, "_get_pricing_source", lambda: FakeSource())

    permlink = card_permlink(records[0])
    hive.posts[permlink] = as_bridge_post(records[0])
    resp = client.post(f"/api/cards/{permlink}/refresh-comps")
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    job = app.state.hive.queue.get_job(job_id)
    assert job.kind == "update"
    assert job.permlink == permlink
    assert [s.title for s in job.record.comps.top_sales] == [c.title for c in fresh]
    assert job.record.comps.summary.raw_count == 3
    assert job.record.verdict is not None


def test_refresh_comps_404_for_unknown_card(client):
    assert client.post("/api/cards/nope/refresh-comps").status_code == 404
