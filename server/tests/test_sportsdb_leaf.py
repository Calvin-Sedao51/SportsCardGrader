"""Leaf adapter: Webflow sitemap + /releases listing + product pages, Google Sheet CSV checklists."""
import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.sportsdb.http import Fetcher
from app.sportsdb.models import Release
from app.sportsdb.sources.leaf import LeafSource, _name_from_slug, _parse_checklist_csv

FIXTURES = Path(__file__).parent / "fixtures"
HTML = {"content-type": "text/html"}


def fixture(name: str) -> bytes:
    return (FIXTURES / f"sportsdb_leaf_{name}").read_bytes()


def site():
    """MockTransport routed by path; product pages come from per-slug fixtures."""
    calls: list[str] = []

    def handler(request):
        path = request.url.path
        calls.append(f"{request.url.host}{path}")
        if path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "docs.google.com":
            return httpx.Response(200, content=fixture("checklist.csv"), headers={"content-type": "text/csv"})
        if path == "/sitemap.xml":
            return httpx.Response(200, content=fixture("sitemap.xml"), headers={"content-type": "application/xml"})
        if path == "/releases":
            return httpx.Response(200, content=fixture("releases.html"), headers=HTML)
        if path.startswith("/products/"):
            page = FIXTURES / f"sportsdb_leaf_product_{path.rsplit('/', 1)[1].replace('-', '_')}.html"
            if page.exists():
                return httpx.Response(200, content=page.read_bytes(), headers=HTML)
        return httpx.Response(404)
    return httpx.MockTransport(handler), calls


def run(source, method, *args):
    transport, calls = site()

    async def go():
        async with Fetcher(transport=transport, min_delay=0) as f:
            return await getattr(source, method)(f, *args)
    return asyncio.run(go()), calls


@pytest.fixture
def source():
    # Pin "today" so the fixture lastmods (2026-09/10 recent, 2026-03 old) keep meaning the same thing.
    src = LeafSource(today=date(2026, 10, 17))
    src.recent_days = 30
    return src


def test_fetch_releases_merges_sitemap_and_listing_and_fetches_only_recent_pages(source):
    releases, calls = run(source, "fetch_releases")
    by = {r.source_id: r for r in releases}
    assert set(by) == {
        "products/2026-leaf-city-of-champions", "products/2026-leaf-metal-golden-set-tennis",
        "products/2025-26-leaf-nll", "products/2013-leaf-power-showcase-baseball",
        "products/2012-13-draft-basketball-retail", "products/leaf-valiant-football-2",
        "products/2026-leaf-metal-happy-holidays",   # listing only, not in the sitemap yet
    }   # no /shopify-products/ shop SKUs
    assert all(r.manufacturer == "leaf" for r in releases)
    # Two recent sitemap entries + the listing's extra slug; nothing older than recent_days.
    assert sorted(c for c in calls if "/products/" in c) == [
        "www.leaftradingcards.com/products/2026-leaf-city-of-champions",
        "www.leaftradingcards.com/products/2026-leaf-metal-golden-set-tennis",
        "www.leaftradingcards.com/products/2026-leaf-metal-happy-holidays",
    ]
    assert len(calls) == 6   # robots + sitemap + releases + 3 product pages

    city = by["products/2026-leaf-city-of-champions"]
    assert (city.name, city.sport, city.year) == ("2026 Leaf City of Champions", "multi", 2026)
    assert city.url == "https://www.leaftradingcards.com/products/2026-leaf-city-of-champions"
    assert city.release_date == "2026-10-07"
    assert city.image_url.endswith("6a466e12c3ce9360b773d10c_2-Piece_Mockups-2-Piece%20Camera.avif")
    assert city.checklist_url == ("https://docs.google.com/spreadsheets/d/e/2PACX-1vS2B7hqmxD-OsDjvg8qtgE1zG01s"
                                  "LtyvmtUsaILa4xNkFnqZSSbDi-LQBEE5LlnSg/pub?output=csv")
    assert city.description.startswith("Leaf Trading Cards is excited to announce")
    assert city.product_type is None
    assert city.raw["status"] == "RELEASED"
    assert city.raw["config"] == {"Premium cards per box": "4", "BOXES PER CASE": "10"}
    assert city.raw["lastmod"] == "2026-10-07"

    tennis = by["products/2026-leaf-metal-golden-set-tennis"]
    assert (tennis.sport, tennis.release_date, tennis.raw["status"]) == ("other", "2026-10-27", "COMING SOON")

    holidays = by["products/2026-leaf-metal-happy-holidays"]
    assert (holidays.name, holidays.release_date) == ("2026 Leaf Metal Happy Holidays", "2026-12-11")
    assert holidays.checklist_url is None and "lastmod" not in holidays.raw

    # Older products are known from the sitemap URL alone: name from the slug, no page fetched.
    old = by["products/2012-13-draft-basketball-retail"]
    assert (old.name, old.sport, old.year, old.product_type) == (
        "2012-13 Draft Basketball Retail", "basketball", 2012, "Retail")
    assert old.url == "https://www.leaftradingcards.com/products/2012-13-draft-basketball-retail"
    assert old.description is None and old.release_date is None and old.checklist_url is None
    assert old.raw == {"lastmod": "2026-03-19"}
    assert by["products/2025-26-leaf-nll"].name == "2025-26 Leaf NLL"
    assert by["products/leaf-valiant-football-2"].year is None


@pytest.mark.parametrize("slug, name", [
    ("2013-leaf-power-showcase-baseball", "2013 Leaf Power Showcase Baseball"),
    ("2026-leaf-city-of-champions", "2026 Leaf City of Champions"),
    ("2012-13-draft-basketball-retail", "2012-13 Draft Basketball Retail"),
    ("leaf-valiant-football-2", "Leaf Valiant Football 2"),
    ("2025-26-leaf-nll", "2025-26 Leaf NLL"),
])
def test_name_from_slug(slug, name):
    assert _name_from_slug(slug) == name


def release(**kw):
    base = dict(manufacturer="leaf", source_id="products/2026-leaf-city-of-champions",
                name="2026 Leaf City of Champions",
                checklist_url="https://docs.google.com/spreadsheets/d/e/2PACX-x/pub?output=csv")
    base.update(kw)
    return Release(**base)


def test_fetch_checklist_parses_published_sheet_csv(source):
    cards, calls = run(source, "fetch_checklist", release())
    assert calls == ["docs.google.com/robots.txt",
                     "docs.google.com/spreadsheets/d/e/2PACX-x/pub"]
    assert len(cards) == 22      # header + "*Plates were made..." note row skipped
    first = cards[0]
    assert (first.card_number, first.player, first.subset, first.parallel) == (
        "SC-BW1", "Bernie Williams", "Signs of a Champion", "Prismatic")
    assert (first.is_auto, first.is_relic, first.is_rookie, first.print_run) == (True, False, False, None)
    assert first.raw == {"type": "Auto", "print_runs": {
        "Silver": 10, "White": 9, "Orange": 8, "Platinum": 7, "Blue": 6, "Pink": 5,
        "Green": 4, "Red": 3, "Purple": 2, "Gold": 1}}
    last = cards[-1]
    assert (last.card_number, last.player, last.parallel, last.is_auto, last.is_relic) == (
        "WC-23", "Willie Mays / Hoyt Wilhelm", None, False, True)
    assert last.raw["print_runs"]["Silver Holo"] == 20


def test_parse_checklist_csv_keeps_only_this_sets_rows():
    text = ("Set,Subset,Name,Checklist,BASE\n"
            "2026 Leaf A,Base,Player One,1,Base\n"
            "2026 Leaf B,Base,Player Two,2,Base\n"
            "2026 Leaf A,,No Number,,Base\n")
    cards = _parse_checklist_csv(text, "2026 Leaf A")
    assert [(c.card_number, c.player, c.subset) for c in cards] == [("1", "Player One", "Base")]


def test_parse_checklist_csv_tolerates_renamed_name_column():
    # 2026 Leaf Vivid Baseball's sheet heads the player column "Name_Oscar".
    text = "Set,Subset,Name_Oscar,Checklist,Silver\n2026 Leaf Vivid Baseball,Animated Auto,Bo Jackson,AA-BJ1,5\n"
    cards = _parse_checklist_csv(text, "2026 Leaf Vivid Baseball")
    assert [(c.card_number, c.player, c.raw["print_runs"]) for c in cards] == [("AA-BJ1", "Bo Jackson", {"Silver": 5})]


def test_fetch_checklist_without_sheet_makes_no_requests(source):
    for url in (None, "https://www.leaftradingcards.com/files/checklist.pdf"):
        cards, calls = run(source, "fetch_checklist", release(checklist_url=url))
        assert cards == [] and calls == []


@pytest.mark.live
def test_live_smoke():
    src = LeafSource()
    src.recent_days = 3

    async def go():
        async with Fetcher() as f:
            releases = await src.fetch_releases(f)
            recent = [r for r in releases if r.checklist_url]
            cards = await src.fetch_checklist(f, recent[0]) if recent else []
            return releases, cards
    releases, cards = asyncio.run(go())
    assert len(releases) > 500
    assert any(r.release_date for r in releases)
    assert cards and cards[0].player
