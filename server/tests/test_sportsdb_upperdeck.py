"""Upper Deck adapter against saved Store API / checklist responses."""
import asyncio
from pathlib import Path

import httpx
import pytest

from app.sportsdb.http import Blocked, Fetcher
from app.sportsdb.models import Release
from app.sportsdb.sources.upperdeck import UpperDeckSource

FIXTURES = Path(__file__).parent / "fixtures"
SERIES1 = "/checklist/2026-27-upper-deck-series-1-checklist/"
SPGU = "/checklist/2025-26-sp-game-used-hockey-checklist/"
MVP = "/checklist/2025-26-mvp-hockey-checklist/"


def fixture(name: str) -> str:
    return (FIXTURES / f"sportsdb_upperdeck_{name}").read_text()


def site(products_status=200):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)      # the real file's Crawl-delay would slow the test
        if path == "/wp-json/wc/store/v1/products":
            if products_status != 200:
                return httpx.Response(products_status)
            page = request.url.params.get("page", "1")
            return httpx.Response(200, text=fixture(f"products_p{page}.json"),
                                  headers={"content-type": "application/json",
                                           "x-wp-total": "10", "x-wp-totalpages": "2"})
        if path == "/checklist-sitemap.xml":
            return httpx.Response(200, text=fixture("checklist_sitemap.xml"),
                                  headers={"content-type": "application/xml"})
        if path in (SERIES1, MVP):
            return httpx.Response(200, text=fixture("checklist_series1.html"),
                                  headers={"content-type": "text/html"})
        if path == SPGU:
            return httpx.Response(200, text=fixture("checklist_spgu.html"),
                                  headers={"content-type": "text/html"})
        return httpx.Response(404, text="<html>not found</html>")
    return httpx.MockTransport(handler), calls


def run(coro):
    return asyncio.run(coro)


async def releases_with(transport):
    async with Fetcher(transport=transport, min_delay=0) as f:
        return await UpperDeckSource().fetch_releases(f)


async def checklist_with(transport, release, source=None):
    async with Fetcher(transport=transport, min_delay=0) as f:
        return await (source or UpperDeckSource()).fetch_checklist(f, release)


def release(name, source_id="x", **kw):
    return Release(manufacturer="upperdeck", source_id=source_id, name=name, **kw)


# --- releases ---

def test_fetch_releases_keeps_card_products_across_pages():
    transport, calls = site()
    releases = run(releases_with(transport))
    # Skipped: Easton Cowan memorabilia, Legendary board game, Wyndham Clark print, McDavid memorabilia.
    assert {r.source_id for r in releases} == {"42265", "41844", "33631", "38032", "37716", "12454", "11882"}
    product_calls = [c for c in calls if "/wp-json/wc/store/v1/products" in c]
    assert len(product_calls) == 2 and "per_page=100" in product_calls[0]
    assert calls.count("https://upperdeck.com/checklist-sitemap.xml") == 1
    s1 = next(r for r in releases if r.source_id == "41844")
    assert s1.manufacturer == "upperdeck"
    assert s1.name == "2026-27 Upper Deck Series 1 Hockey (Hobby)"
    assert (s1.sport, s1.year, s1.product_type) == ("hockey", 2026, "Hobby")
    assert s1.url == "https://upperdeck.com/product/2026-27-upper-deck-series-1-hockey-hobby/"
    assert s1.image_url.startswith("https://upperdeck.com/wp-content/uploads/")
    assert s1.checklist_url == f"https://upperdeck.com{SERIES1}"
    assert s1.release_date is None          # the site never shows one
    assert s1.description.startswith("THE MOST WIDELY-COLLECTED BASE SET")
    assert s1.raw["categories"] == ["trading-cards"]


def test_fetch_releases_sport_and_type_hints():
    transport, _ = site()
    by_id = {r.source_id: r for r in run(releases_with(transport))}
    assert by_id["33631"].sport == "football"            # CFL category, name says nothing
    assert by_id["42265"].sport == "multi"               # "multi-sport" in the blurb
    assert by_id["38032"].sport == "hockey"              # no categories; name says Hockey
    assert by_id["37716"].sport == "hockey"              # only the matched slug says NHL
    assert by_id["37716"].checklist_url == "https://upperdeck.com/checklist/2025-26-nhl-ultimate-collection-checklist/"
    assert (by_id["38032"].year, by_id["38032"].product_type) == (2025, "Blaster")
    assert by_id["11882"].product_type == "e-Pack"
    assert by_id["11882"].checklist_url is None
    assert by_id["12454"].product_type is None


def test_fetch_releases_blocked_propagates():
    transport, _ = site(products_status=403)
    with pytest.raises(Blocked):
        run(releases_with(transport))


# --- checklists ---

def test_fetch_checklist_parses_series1_rows():
    transport, calls = site()
    rel = release("2026-27 Upper Deck Series 1 Hockey (Hobby)", "41844", year=2026,
                  checklist_url=f"https://upperdeck.com{SERIES1}")
    cards = run(checklist_with(transport, rel))
    assert "https://upperdeck.com/checklist-sitemap.xml" not in calls   # a known URL needs no lookup
    assert len(cards) == 10
    by_key = {(c.card_number, c.subset, c.parallel): c for c in cards}
    base = by_key[("1", "Base", None)]
    assert (base.player, base.team, base.is_rookie, base.print_run) == ("Chris Kreider", "Anaheim Ducks", False, None)
    yg = by_key[("201", "Young Guns", None)]
    assert (yg.player, yg.team, yg.is_rookie) == ("Anton Frondell", "Chicago Blackhawks", True)
    assert by_key[("201", "Young Guns", "Green Foil")].raw["set"] == "Green Foil Parallel - Young Guns"
    assert by_key[("1", "Base", "Deluxe")].print_run == 250
    assert by_key[("201", "Oversized Young Guns", None)].is_rookie
    auto = by_key[("10", "Snow Spray Auto Variations", None)]
    assert (auto.player, auto.is_auto, auto.is_rookie) == ("Fraser Minten", True, False)
    assert ("DD-1", "Debut Dates", None) in by_key
    assert ("DD-1", "Debut Dates", "Speckle") in by_key
    # No parent insert to split from, so the printed set name stays whole.
    assert ("DZ-1", "Dazzlers Pink Parallel", None) in by_key
    assert not any(c.is_relic for c in cards)


def test_fetch_checklist_relic_and_auto_flags():
    transport, _ = site()
    cards = run(checklist_with(transport, release("2025-26 SP Game Used Hockey", "34993", year=2025)))
    by_key = {(c.card_number, c.subset, c.parallel): c for c in cards}
    assert len(cards) == 6
    assert by_key[("1", "Base", "Red Jersey")].is_relic
    gold = by_key[("1", "Base", "Gold Premium Memorabilia")]
    assert (gold.is_relic, gold.print_run, gold.raw["mem"]) == (True, 25, "Patch")
    assert by_key[("1", "Base", "Blue Auto")].is_auto
    purple = by_key[("2", "Auto", "Purple")]
    assert (purple.player, purple.is_auto, purple.print_run) == ("Miro Heiskanen", True, 5)
    assert by_key[("AR-AL", "Authentic Rookies", None)].is_rookie
    assert not by_key[("1", "Base", None)].is_relic


def test_fetch_checklist_matches_closest_slug_and_caches_sitemap():
    transport, calls = site()
    src = UpperDeckSource()

    async def go():
        async with Fetcher(transport=transport, min_delay=0) as f:
            mvp = release("2025-26 MVP (Blaster)", "12735")
            await src.fetch_checklist(f, mvp)
            # "2025-26-mvp-silver-collection-hockey-checklist" is also a candidate; fewer extra words wins.
            assert mvp.checklist_url == f"https://upperdeck.com{MVP}"
            assert await src.fetch_checklist(f, release("Fleer Ultra Golf (Hobby)")) == []
            # A season-less name must not fall into a different year's or brand's checklist.
            assert await src.fetch_checklist(f, release("Upper Deck Series 1 (e-Pack)")) == []
            assert await src.fetch_checklist(f, release("2026-27 Upper Deck Series 1 Hockey (Hobby)", year=2026))
    run(go())
    assert calls.count("https://upperdeck.com/checklist-sitemap.xml") == 1
    assert [c for c in calls if "/checklist/" in c] == [f"https://upperdeck.com{MVP}", f"https://upperdeck.com{SERIES1}"]


def test_fetch_checklist_missing_page_returns_empty():
    transport, _ = site()
    rel = release("2026-27 MVP Hockey (Hobby)", "38181", year=2026)   # in the sitemap, page 404s
    assert run(checklist_with(transport, rel)) == []
    assert rel.checklist_url is None


@pytest.mark.live
def test_live_fetch_releases():
    async def go():
        async with Fetcher() as f:
            return await UpperDeckSource().fetch_releases(f)
    releases = run(go())
    assert len(releases) > 0
    assert all(r.source_id.isdigit() and r.url for r in releases)
