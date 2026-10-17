"""Panini adapter: sitemap listing + xlsx checklist parsing over fixtures. No network
except the one ``live`` smoke test (deselected by default)."""
import asyncio
from pathlib import Path

import httpx
import pytest

from app.sportsdb.http import Blocked, Fetcher
from app.sportsdb.models import Release
from app.sportsdb.sources.panini import PaniniSource, parse_checklist_xlsx, parse_sitemap

FIXTURES = Path(__file__).parent / "fixtures"
SITEMAP = (FIXTURES / "sportsdb_panini_sitemap.xml").read_text()
# Synthetic workbook (labelled as such in its first row): the live xlsx files were
# unreachable on 2026-10-17, see the module docstring.
XLSX = (FIXTURES / "sportsdb_panini_checklist_synthetic.xlsx").read_bytes()
ROBOTS = "User-agent: *\nDisallow: /cdn-cgi/\n"


def site(status=200):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS, headers={"content-type": "text/plain"})
        if status != 200:
            return httpx.Response(status, text="<body>::CLOUDFLARE_ERROR_1000S_BOX::</body>")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP, headers={"content-type": "text/xml"})
        if request.url.path.endswith(".xlsx"):
            return httpx.Response(200, content=XLSX, headers={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        return httpx.Response(404, text="nope")
    return httpx.MockTransport(handler), calls


def run(coro):
    return asyncio.run(coro)


async def with_fetcher(transport, fn):
    async with Fetcher(transport=transport, min_delay=0) as f:
        return await fn(f)


def release(**kw):
    base = dict(manufacturer="panini", source_id="2026-panini-prizm-wnba-trading-card-box-hobby",
                name="2026 Panini Prizm WNBA Trading Card Box (Hobby)", sport="basketball", year=2026,
                url="https://www.paniniamerica.net/2026-panini-prizm-wnba-trading-card-box-hobby.html")
    base.update(kw)
    return Release(**base)


# --- releases ---

def test_fetch_releases_403_is_blocked():
    transport, _ = site(status=403)
    with pytest.raises(Blocked):
        run(with_fetcher(transport, PaniniSource().fetch_releases))


def test_fetch_releases_parses_sealed_products_from_sitemap():
    transport, calls = site()
    releases = run(with_fetcher(transport, PaniniSource().fetch_releases))
    assert calls[-1] == "https://www.paniniamerica.net/sitemap.xml"
    assert len(releases) == 56
    by_id = {r.source_id: r for r in releases}
    sel = by_id["2025-26-panini-select-basketball-trading-card-box-hobby"]
    assert sel.manufacturer == "panini"
    assert sel.name == "2025-26 Panini Select Basketball Trading Card Box (Hobby)"
    assert (sel.sport, sel.year, sel.product_type) == ("basketball", 2025, "Hobby")
    assert sel.url == "https://www.paniniamerica.net/2025-26-panini-select-basketball-trading-card-box-hobby.html"
    assert sel.release_date is None and sel.checklist_url is None
    assert sel.raw == {"slug": "2025-26-panini-select-basketball-trading-card-box-hobby",
                       "lastmod": "2026-10-17", "name_from": "sitemap slug"}
    # Singles, Panini Instant, stickers, categories and the college/NFT hosts are not releases.
    assert not any(s.startswith("pi-") or "base-card" in s or "sticker" in s for s in by_id)
    assert all(r.url.startswith("https://www.paniniamerica.net/") for r in releases)


@pytest.mark.parametrize("slug, name, sport, year, product_type", [
    ("2026-panini-prizm-wnba-trading-card-box-hobby-mega-box",
     "2026 Panini Prizm WNBA Trading Card Box (Hobby Mega Box)", "basketball", 2026, "Hobby Mega Box"),
    ("2026-panini-donruss-football-trading-card-box-hobby-blaster",
     "2026 Panini Donruss Football Trading Card Box (Hobby Blaster)", "football", 2026, "Hobby Blaster"),
    ("2026-panini-prizm-usa-stars-stripes-baseball-trading-card-box-hobby",
     "2026 Panini Prizm USA Stars Stripes Baseball Trading Card Box (Hobby)", "baseball", 2026, "Hobby"),
    ("2025-26-panini-select-euroleague-trading-card-box-hobby",
     "2025-26 Panini Select Euroleague Trading Card Box (Hobby)", "basketball", 2025, "Hobby"),
    ("2025-panini-prizm-k-league-trading-card-box-hobby",
     "2025 Panini Prizm K League Trading Card Box (Hobby)", "other", 2025, "Hobby"),
    ("2026-panini-prizm-fifa-world-cup-2026-trading-card-box-breakaway",
     "2026 Panini Prizm FIFA World Cup 2026 Trading Card Box (Breakaway)", "other", 2026, "Breakaway"),
    ("2025-26-select-ligue-1-green-ice-pack", "2025-26 Select Ligue 1 Green Ice Pack", "other", 2025, "Pack"),
    ("2023-panini-chronicles-racing-box-blaster", "2023 Panini Chronicles Racing Box (Blaster)", "other", 2023, "Blaster"),
])
def test_release_fields_derived_from_slug(slug, name, sport, year, product_type):
    xml = (f'<urlset><url><loc>https://www.paniniamerica.net/{slug}.html</loc>'
           f'<lastmod>2026-10-17T12:00:02.533Z</lastmod></url></urlset>')
    [rel] = parse_sitemap(xml)
    assert (rel.source_id, rel.name, rel.sport, rel.year, rel.product_type) == (slug, name, sport, year, product_type)


# --- checklists ---

def test_fetch_checklist_without_url_is_empty_and_makes_no_request():
    transport, calls = site()
    cards = run(with_fetcher(transport, lambda f: PaniniSource().fetch_checklist(f, release())))
    assert cards == [] and calls == []


def test_fetch_checklist_pdf_is_empty_without_download():
    transport, calls = site()
    rel = release(checklist_url="https://assets.paniniamerica.net/checklist/2026+Prizm+WNBA.pdf")
    assert run(with_fetcher(transport, lambda f: PaniniSource().fetch_checklist(f, rel))) == []
    assert calls == []


def test_fetch_checklist_downloads_and_parses_xlsx():
    transport, calls = site()
    rel = release(checklist_url="https://assets.paniniamerica.net/checklist/2026+Prizm+WNBA.xlsx")
    cards = run(with_fetcher(transport, lambda f: PaniniSource().fetch_checklist(f, rel)))
    assert calls[-1] == "https://assets.paniniamerica.net/checklist/2026+Prizm+WNBA.xlsx"
    assert len(cards) == 8      # blank row and the player-less row are dropped
    base, silver, gold = cards[0], cards[1], cards[2]
    assert (base.card_number, base.player, base.team, base.subset, base.parallel, base.print_run) == \
        ("1", "Caitlin Clark", "Indiana Fever", "Base", None, None)
    assert (silver.parallel, silver.print_run) == ("Silver Prizm", None)
    assert (gold.parallel, gold.print_run) == ("Gold Prizm", 10)
    assert not (base.is_rookie or base.is_auto or base.is_relic)
    auto = cards[4]
    assert (auto.card_number, auto.subset, auto.print_run, auto.is_rookie, auto.is_auto, auto.is_relic) == \
        ("RA-PB", "Rookie Autographs", 99, True, True, False)
    assert (cards[5].parallel, cards[5].print_run) == ("Black Prizm", 1)
    relic = cards[6]
    assert (relic.subset, relic.print_run, relic.is_relic, relic.is_auto) == ("Jersey Relics", 199, True, False)
    patch_auto = cards[7]
    assert (patch_auto.is_rookie, patch_auto.is_auto, patch_auto.is_relic, patch_auto.print_run) == (True, True, True, 49)
    assert base.raw == {"Set": "Base", "Card #": "1", "Player": "Caitlin Clark", "Team": "Indiana Fever"}


def test_parse_checklist_xlsx_without_header_row_is_empty():
    from io import BytesIO
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["just", "some", "words"])
    buf = BytesIO()
    wb.save(buf)
    assert parse_checklist_xlsx(buf.getvalue()) == []


# --- live ---

@pytest.mark.live
def test_live_sitemap_lists_current_products():
    async def go():
        async with Fetcher() as f:
            return await PaniniSource().fetch_releases(f)
    releases = run(go())
    assert len(releases) >= 10
    print({r.sport for r in releases}, releases[0])
