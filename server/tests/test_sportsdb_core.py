"""Core sportsdb: storage, sync orchestration, polite fetcher. No network."""
import asyncio

import httpx
import pytest

from app.sportsdb.db import SportsDb
from app.sportsdb.http import Blocked, Fetcher
from app.sportsdb.models import Card, Release, guess_sport, guess_year
from app.sportsdb.sync import sync_source


def release(**kw):
    base = dict(manufacturer="upperdeck", source_id="ud-1", name="2025-26 Upper Deck Series 1 Hockey",
                sport="hockey", year=2025, release_date="2026-10-01", url="https://x/1")
    base.update(kw)
    return Release(**base)


@pytest.fixture
def db(tmp_path):
    with SportsDb(tmp_path / "t.sqlite") as d:
        yield d


# --- models ---

@pytest.mark.parametrize("name, sport", [
    ("2026 Topps Chrome Basketball Hobby", "basketball"),
    ("2025-26 Upper Deck Series 1 Hockey", "hockey"),
    ("2026 Bowman Draft", "baseball"),
    ("2025-26 Bowman Basketball", "basketball"),
    ("2026 Bowman Football", "football"),
    ("2025 Panini Prizm Football", "football"),
    ("2026 Wild Card Matte Metal Multi-Sport Edition", "multi"),
    ("2026 Leaf Metal Baseball & Football", "multi"),
    ("2026 Onyx Track and Field Hobby Box", "other"),
])
def test_guess_sport(name, sport):
    assert guess_sport(name) == sport


def test_guess_year_takes_season_start():
    assert guess_year("2025-26 Upper Deck Series 1") == 2025
    assert guess_year("2026 Topps Series 1") == 2026
    assert guess_year("Topps Now") is None


# --- db ---

def test_upsert_release_dedupes_and_keeps_known_values(db):
    rel_id, new = db.upsert_release(release())
    assert new
    again_id, new2 = db.upsert_release(release(release_date=None, image_url="https://img"))
    assert again_id == rel_id and not new2
    row = db.releases()[0]
    assert row["release_date"] == "2026-10-01"   # None didn't overwrite
    assert row["image_url"] == "https://img"      # new value landed
    assert db.stats()["releases"] == 1


def test_upsert_release_keeps_richer_name(db):
    db.upsert_release(release(name="2026 Leaf Mélange Baseball Hobby"))
    db.upsert_release(release(name="2026 Leaf Melange"))      # slug-derived, shorter
    assert db.releases()[0]["name"] == "2026 Leaf Mélange Baseball Hobby"
    db.upsert_release(release(name="2026 Leaf Mélange Baseball Hobby Box"))
    assert db.releases()[0]["name"] == "2026 Leaf Mélange Baseball Hobby Box"


def test_upsert_release_keeps_sport_and_raw_from_a_thinner_pass(db):
    db.upsert_release(release(sport="multi", raw={"status": "RELEASED"}))
    db.upsert_release(release(sport="other", raw={}))      # sitemap-only pass
    row = db.releases()[0]
    assert row["sport"] == "multi" and row["raw"] == '{"status": "RELEASED"}'
    db.upsert_release(release(sport="hockey", raw={"k": 1}))
    row = db.releases()[0]
    assert row["sport"] == "hockey" and row["raw"] == '{"k": 1}'


def test_upsert_cards_ignores_duplicates_and_blank_rows(db):
    rel_id, _ = db.upsert_release(release())
    cards = [Card("201", "Connor Bedard", team="Chicago", subset="Young Guns", is_rookie=True),
             Card("201", "Connor Bedard", subset="Young Guns"),   # same key
             Card("201", "Connor Bedard", subset="Young Guns", parallel="Exclusives"),
             Card("", "Nobody"), Card("5", "")]
    assert db.upsert_cards(rel_id, cards) == 2
    assert db.upsert_cards(rel_id, cards) == 0
    assert db.stats()["cards"] == 2


def test_search_cards_matches_words_and_season_year(db):
    rel_id, _ = db.upsert_release(release())
    db.upsert_cards(rel_id, [Card("201", "Connor Bedard"), Card("1", "Sidney Crosby")])
    assert [c["player"] for c in db.search_cards("bedard")] == ["Connor Bedard"]
    assert [c["player"] for c in db.search_cards("Connor Bedard", year=2026)] == ["Connor Bedard"]
    assert db.search_cards("Connor Bedard", year=2023) == []
    assert db.search_cards("") == []


def test_releases_since_days_includes_undated_recent(db):
    db.upsert_release(release(source_id="old", release_date="2020-01-01"))
    db.upsert_release(release(source_id="undated", release_date=None))
    db.upsert_release(release(source_id="new"))
    assert {r["source_id"] for r in db.releases(since_days=30)} == {"undated", "new"}


# --- sync ---

class FakeSource:
    name = "upperdeck"

    def __init__(self, releases, cards=None, fail=None):
        self._releases, self._cards, self._fail = releases, cards or {}, fail

    async def fetch_releases(self, fetcher):
        if self._fail:
            raise self._fail
        return self._releases

    async def fetch_checklist(self, fetcher, rel):
        return self._cards.get(rel.source_id, [])


def test_sync_source_records_releases_cards_and_run(db):
    src = FakeSource([release(), release(source_id="old", release_date="2019-01-01")],
                     cards={"ud-1": [Card("201", "Connor Bedard")], "old": [Card("1", "X")]})
    s = asyncio.run(sync_source(db, src, fetcher=None, since_days=365))
    assert (s.status, s.releases_seen, s.releases_new, s.cards_new) == ("ok", 2, 2, 1)
    assert s.new_release_names == ["2025-26 Upper Deck Series 1 Hockey",
                                   "2025-26 Upper Deck Series 1 Hockey"]
    assert s.checklists_fetched == 1          # the 2019 release is outside since_days
    run = db.last_runs()[0]
    assert run["status"] == "ok" and run["cards_new"] == 1 and run["finished_at"]
    # Second run the same day: nothing new, checklist not refetched.
    s2 = asyncio.run(sync_source(db, src, fetcher=None, since_days=365))
    assert (s2.releases_new, s2.checklists_fetched, s2.cards_new) == (0, 0, 0)


def test_sync_source_blocked_and_error_statuses(db):
    s = asyncio.run(sync_source(db, FakeSource([], fail=Blocked("HTTP 403")), fetcher=None))
    assert (s.status, s.error) == ("blocked", "HTTP 403")
    s = asyncio.run(sync_source(db, FakeSource([], fail=ValueError("boom")), fetcher=None))
    assert s.status == "error" and "ValueError: boom" == s.error
    assert [r["status"] for r in db.last_runs()] == ["error", "blocked"]


# --- fetcher ---

def site(robots="User-agent: *\nDisallow: /private/\nCrawl-delay: 0\n", status=200):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots, headers={"content-type": "text/plain"})
        return httpx.Response(status, json={"ok": True})
    return httpx.MockTransport(handler), calls


def test_fetcher_respects_robots_and_fetches_it_once():
    transport, calls = site()
    async def run():
        async with Fetcher(transport=transport, min_delay=0) as f:
            assert await f.get_json("https://example.com/a") == {"ok": True}
            await f.get_json("https://example.com/b")
            with pytest.raises(Blocked):
                await f.get("https://example.com/private/x")
    asyncio.run(run())
    assert calls.count("https://example.com/robots.txt") == 1


def test_fetcher_403_is_blocked():
    transport, _ = site(status=403)
    async def run():
        async with Fetcher(transport=transport, min_delay=0) as f:
            with pytest.raises(Blocked):
                await f.get("https://example.com/a")
    asyncio.run(run())
