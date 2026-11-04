"""Onyx adapter against trimmed WooCommerce Store API fixtures. No network
except the one ``live`` smoke test (deselected by default)."""
import asyncio
import copy
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.sportsdb.http import Fetcher
from app.sportsdb.models import Release
from app.sportsdb.sources.onyx import OnyxSource, PER_PAGE

FIXTURES = Path(__file__).parent / "fixtures"
PRODUCTS = json.loads((FIXTURES / "sportsdb_onyx_products.json").read_text())
CATEGORIES = json.loads((FIXTURES / "sportsdb_onyx_categories.json").read_text())
BASEBALL = json.loads((FIXTURES / "sportsdb_onyx_category_baseball.json").read_text())
XLSX = (FIXTURES / "sportsdb_onyx_checklist.xlsx").read_bytes()
HOST = "https://onyxauthenticated.com"


def site(pages=None, html=None):
    """MockTransport for onyxauthenticated.com. ``pages`` overrides the
    uncategorised product listing by page number."""
    pages = pages or {1: PRODUCTS}
    calls = []

    def handler(request):
        calls.append(str(request.url))
        path, q = request.url.path, parse_qs(request.url.query.decode())
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n",
                                  headers={"content-type": "text/plain"})
        if path == "/wp-json/wc/store/v1/products/categories":
            return httpx.Response(200, json=CATEGORIES)
        if path == "/wp-json/wc/store/v1/products":
            page = int(q.get("page", ["1"])[0])
            if "category" in q:
                return httpx.Response(200, json=BASEBALL if (q["category"][0], page) == ("40", 1) else [])
            return httpx.Response(200, json=pages.get(page, []))
        if path.endswith(".xlsx"):
            return httpx.Response(200, content=XLSX, headers={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        if path.endswith(".html"):
            return httpx.Response(200, text=html or "", headers={"content-type": "text/html"})
        return httpx.Response(404)
    return httpx.MockTransport(handler), calls


def run(coro):
    return asyncio.run(coro)


async def releases_with(transport):
    async with Fetcher(transport=transport, min_delay=0) as f:
        return await OnyxSource().fetch_releases(f)


async def checklist_with(transport, release):
    async with Fetcher(transport=transport, min_delay=0) as f:
        return await OnyxSource().fetch_checklist(f, release)


def by_id(releases):
    return {r.source_id: r for r in releases}


# --- releases ---

def test_fetch_releases_maps_store_api_products():
    transport, _ = site()
    rels = by_id(run(releases_with(transport)))
    box = rels["2356"]
    assert box.manufacturer == "onyx"
    assert box.name == "2026 Onyx Track and Field Hobby Box"
    assert (box.sport, box.year, box.product_type) == ("other", 2026, "Hobby Box")
    assert box.url == f"{HOST}/product/2026-onyx-track-and-field-hobby-box/"
    assert box.image_url == f"{HOST}/wp-content/uploads/2026/07/New-TF-Hobby-Box.png"
    # The only checklist Onyx publishes is a gallery image.
    assert box.checklist_url == f"{HOST}/wp-content/uploads/2026/07/New-TF-Checklist.png.jpg"
    assert box.release_date is None
    assert "45-athlete checklist" in box.description and "<p>" not in box.description
    assert box.raw["categories"] == ["track-and-field"] and box.raw["id"] == 2356
    # HTML entities in names are decoded.
    assert rels["2035"].name == "Kaleena Smith Autographed Rare “Rim Shaker” LE Card – Gray Variation"


def test_sport_comes_from_own_category_then_name_then_category_listing():
    transport, _ = site()
    rels = by_id(run(releases_with(transport)))
    assert rels["2043"].sport == "basketball"       # own category slug
    assert rels["2046"].sport == "baseball"         # "Bowman" in the name
    assert rels["2082"].sport == "baseball"         # only listed under ?category=40
    assert rels["2082"].year == 2025
    assert rels["1903"].year is None
    assert rels["1903"].product_type == rels["2082"].product_type == "Single Card"


def test_non_card_products_are_skipped():
    transport, _ = site()
    rels = by_id(run(releases_with(transport)))
    assert "2058" not in rels                        # autographed MLB baseball (the ball)
    assert len(rels) == len(PRODUCTS) - 1


def test_pagination_stops_on_short_page():
    full = [dict(PRODUCTS[0], id=i, name=f"Box {i}") for i in range(1, PER_PAGE + 1)]
    transport, calls = site(pages={1: full, 2: PRODUCTS, 3: [dict(PRODUCTS[0], id=999)]})
    rels = run(releases_with(transport))
    listing = [c for c in calls if "/products?" in c and "category=" not in c]
    assert [c for c in listing if "page=1" in c] and [c for c in listing if "page=2" in c]
    assert not [c for c in listing if "page=3" in c]
    assert len(rels) == PER_PAGE + len(PRODUCTS) - 1


def test_release_date_parsed_from_description_when_present():
    product = copy.deepcopy(PRODUCTS[0])
    product["description"] += "<p>Release Date: March 4, 2026</p>"
    transport, _ = site(pages={1: [product]})
    assert run(releases_with(transport))[0].release_date == "2026-03-04"


# --- checklists ---

def test_fetch_checklist_image_only_returns_nothing_without_fetching():
    transport, calls = site()
    box = by_id(run(releases_with(transport)))["2356"]
    assert run(checklist_with(transport, box)) == []
    assert not [c for c in calls if "Checklist.png" in c]


def test_fetch_checklist_parses_xlsx():
    transport, _ = site()
    rel = Release("onyx", "1", "2026 Onyx Track and Field Hobby Box",
                  checklist_url=f"{HOST}/wp-content/uploads/2026/07/checklist.xlsx")
    cards = {c.card_number: c for c in run(checklist_with(transport, rel))}
    assert list(cards) == ["1", "2", "A-NL", "R-KH", "RC-GG"]
    assert cards["1"].player == "Noah Lyles" and cards["1"].subset == "Base"
    assert cards["2"].parallel == "Gold" and cards["2"].print_run == 10
    assert cards["A-NL"].is_auto and cards["A-NL"].print_run == 25
    assert cards["R-KH"].is_relic and cards["R-KH"].print_run == 1
    assert cards["RC-GG"].is_rookie and not cards["RC-GG"].is_auto
    assert cards["2"].raw["Event"] == "Pole Vault"


def test_fetch_checklist_parses_html_table():
    html = """<html><body><h2>Checklist</h2><table>
      <tr><th>No.</th><th>Athlete</th><th>Team</th><th>Insert</th></tr>
      <tr><td>7</td><td>Rai Benjamin</td><td>USA</td><td>Base</td></tr>
      <tr><td>A-RB</td><td>Rai Benjamin</td><td>USA</td><td>Autograph /49</td></tr>
    </table></body></html>"""
    transport, _ = site(html=html)
    rel = Release("onyx", "1", "x", checklist_url=f"{HOST}/checklist.html")
    cards = run(checklist_with(transport, rel))
    assert [(c.card_number, c.player, c.team) for c in cards] == [
        ("7", "Rai Benjamin", "USA"), ("A-RB", "Rai Benjamin", "USA")]
    assert cards[1].subset == "Autograph /49" and cards[1].is_auto and cards[1].print_run == 49


def test_fetch_checklist_never_leaves_onyx_or_fetches_pdfs():
    transport, calls = site()
    for url in ("https://example.com/checklist.xlsx", f"{HOST}/wp-content/uploads/checklist.pdf", None):
        assert run(checklist_with(transport, Release("onyx", "1", "x", checklist_url=url))) == []
    assert not [c for c in calls if "checklist" in c]


@pytest.mark.live
def test_live_fetch_releases():
    async def go():
        async with Fetcher() as f:
            return await OnyxSource().fetch_releases(f)
    rels = run(go())
    assert len(rels) > 0
    assert all(r.source_id.isdigit() and r.url for r in rels)
