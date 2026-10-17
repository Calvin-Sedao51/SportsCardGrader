"""Topps adapter: Cloudflare-blocked plain HTTP, browser-fetched hub + calendar parsing.

Fixtures are trimmed copies of the real pages (headless Chromium, 2026-10-17).
The xlsx checklist is SYNTHETIC: every Topps checklist on the hub that day was
a PDF or legacy .xls, so the xlsx parser is exercised on a made-up workbook.
"""
import asyncio
import io
from pathlib import Path

import httpx
import openpyxl
import pytest

from app.sportsdb.http import Blocked, Fetcher
from app.sportsdb.models import Release
from app.sportsdb.sources.topps import (CALENDAR_URL, HUB_URL, ToppsSource, parse_calendar,
                                        parse_checklist_xlsx, parse_hub)

FIXTURES = Path(__file__).parent / "fixtures"
HUB_HTML = (FIXTURES / "sportsdb_topps_checklists.html").read_text()
CALENDAR_HTML = (FIXTURES / "sportsdb_topps_release_calendar.html").read_text()
MEGA_BOX_PDF = ("https://cdn.shopify.com/s/files/1/0662/9749/5709/files/"
                "2026_Bowman_Chrome_Baseball_Mega_Box_Checklist_1.pdf?v=1786458909")


def blocked_site():
    """topps.com as seen over plain HTTPS: Cloudflare 403 for every path."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(403, text="<title>Attention Required! | Cloudflare</title>")
    return httpx.MockTransport(handler), calls


class FakeBrowser:
    """Stands in for the headless-Chromium helper: serves fixture text per URL."""
    opened = 0

    def __init__(self, pages=None, fail=None):
        self._pages = pages if pages is not None else {HUB_URL: HUB_HTML, CALENDAR_URL: CALENDAR_HTML}
        self._fail = fail
        self.urls = []

    def __call__(self):
        return self

    async def __aenter__(self):
        FakeBrowser.opened += 1
        if self._fail:
            raise self._fail
        return self

    async def __aexit__(self, *exc):
        return None

    async def get_text(self, url):
        self.urls.append(url)
        if url not in self._pages:
            raise Blocked(f"HTTP 403 for {url}")
        return self._pages[url]


def run_releases(source, transport):
    async def go():
        async with Fetcher(transport=transport, min_delay=0) as f:
            return await source.fetch_releases(f)
    return asyncio.run(go())


# --- plain HTTP is blocked ---

def test_fetch_releases_without_browser_raises_blocked():
    transport, calls = blocked_site()
    with pytest.raises(Blocked):
        run_releases(ToppsSource(use_browser=False), transport)
    assert any("topps.com" in u for u in calls)      # the fetcher was tried first


def test_fetch_releases_browser_blocked_too_raises_blocked():
    transport, _ = blocked_site()
    browser = FakeBrowser(pages={})
    with pytest.raises(Blocked, match="topps.com"):
        run_releases(ToppsSource(browser_factory=browser), transport)


def test_fetch_releases_browser_unavailable_raises_blocked():
    transport, _ = blocked_site()
    browser = FakeBrowser(fail=RuntimeError("Executable doesn't exist"))
    with pytest.raises(Blocked, match="Executable doesn't exist"):
        run_releases(ToppsSource(browser_factory=browser), transport)


# --- browser path: hub + calendar -> releases ---

def test_fetch_releases_via_browser_merges_hub_and_calendar():
    transport, calls = blocked_site()
    browser = FakeBrowser()
    releases = run_releases(ToppsSource(browser_factory=browser), transport)
    assert browser.urls == [HUB_URL, CALENDAR_URL]
    assert len(releases) == 10                       # 7 hub + 4 calendar - 1 overlap
    assert len({r.source_id for r in releases}) == 10
    assert all(r.manufacturer == "topps" for r in releases)
    by_id = {r.source_id: r for r in releases}

    mega = by_id["2026-bowman-chrome-baseball-mega-box"]
    assert mega.name == "2026 Bowman Chrome Baseball Mega Box"   # ® stripped
    assert (mega.sport, mega.year, mega.product_type) == ("baseball", 2026, "Mega Box")
    assert mega.checklist_url == MEGA_BOX_PDF
    assert mega.url == HUB_URL and mega.release_date is None

    ufc = by_id["2026-topps-chrome-ufc-sapphire-edition"]
    assert ufc.sport == "other" and ufc.product_type == "Sapphire Edition"
    assert ufc.checklist_url.split("?")[0].endswith(".xls")

    assert by_id["2025-26-bowman-basketball"].year == 2025
    assert by_id["2025-26-bowman-basketball"].sport == "basketball"
    alabama = by_id["2023-bowman-u-alabama-football"]
    assert (alabama.year, alabama.sport) == (2023, "football")
    assert alabama.checklist_url == "https://www.topps.com/media/NIL2309-2023BowmanUAlabamaChecklist.pdf"

    # Calendar entry that is also on the hub: hub checklist + calendar date/url/image.
    ubest = by_id["2026-bowman-u-best-football"]
    assert ubest.release_date == "2026-10-30"
    assert ubest.url == "https://www.topps.com/pages/bowman-u-best-football"
    assert ubest.image_url.startswith("https://images.contentstack.io/")
    assert ubest.checklist_url.endswith("2026_Bowman_U_Best_FB_Checklist.pdf?v=1791206274")
    assert ubest.sport == "football"

    # Calendar-only entries.
    debut = by_id["2026-topps-pro-debut-baseball"]
    assert debut.release_date == "2026-10-19"        # from dropDate 2026-10-19T16:00:00.000Z
    assert debut.checklist_url is None and debut.sport == "baseball"
    assert debut.raw["productCategory"] == "Sports"
    assert by_id["2026-topps-chrome-football"].name == "2026 Topps Chrome Football"
    assert by_id["2025-26-topps-stadium-club-basketball"].year == 2025


def test_parse_hub_uses_heading_year_and_skips_non_card_products():
    # SYNTHETIC markup in the hub's shape: a name without a year, a supplies entry, a nav link.
    html = """<main><h2>2024</h2><ul>
      <li><a href="https://cdn.shopify.com/x/Toy_Story_Checklist.pdf?v=1">Topps 30 Years of Toy Story</a></li>
      <li><a href="https://cdn.shopify.com/x/Sleeves.pdf?v=1">2024 Topps Card Sleeves 100 Count</a></li>
      <li><a href="/collections/topps-now">Topps NOW</a></li>
    </ul></main>"""
    rels = parse_hub(html)
    assert [r.name for r in rels] == ["Topps 30 Years of Toy Story"]
    assert rels[0].year == 2024 and rels[0].sport == "other"
    assert rels[0].source_id == "topps-30-years-of-toy-story"


def test_parse_calendar_reads_split_flight_chunks():
    items = parse_calendar(CALENDAR_HTML)
    assert [i.name for i in items][:2] == ["2026 Topps Pro Debut Baseball", "2026 Topps Chrome Football"]
    assert items[0].raw["dropDate"] == "2026-10-19T16:00:00.000Z"
    assert parse_calendar("<html><body>no data</body></html>") == []


# --- checklists ---

def xlsx_bytes():
    """SYNTHETIC workbook shaped like a manufacturer checklist export."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Base"
    ws.append(["2026 Bowman Chrome Baseball"])
    ws.append(["Card #", "Player", "Team", "Subset", "Parallel", "Print Run", "RC", "Auto"])
    ws.append(["BCP-1", "Konnor Griffin", "Pittsburgh Pirates", "Chrome Prospects", None, None, "RC", None])
    ws.append(["BCP-1", "Konnor Griffin", "Pittsburgh Pirates", "Chrome Prospects", "Refractor", "/499", "RC", None])
    ws.append(["CPA-KG", "Konnor Griffin", "Pittsburgh Pirates", "Chrome Prospect Autographs", "Gold", 50, "RC", "Yes"])
    ws.append([None, None, None, None, None, None, None, None])
    ws.append(["", "Checklist subject to change"])
    ws2 = wb.create_sheet("Relics")
    ws2.append(["Card No.", "Name", "Team"])
    ws2.append(["R-PS", "Paul Skenes", "Pittsburgh Pirates"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_checklist_xlsx_maps_columns_and_flags():
    cards = parse_checklist_xlsx(xlsx_bytes())
    assert [(c.card_number, c.player, c.parallel, c.print_run) for c in cards] == [
        ("BCP-1", "Konnor Griffin", None, None),
        ("BCP-1", "Konnor Griffin", "Refractor", 499),
        ("CPA-KG", "Konnor Griffin", "Gold", 50),
        ("R-PS", "Paul Skenes", None, None),
    ]
    assert cards[0].team == "Pittsburgh Pirates" and cards[0].subset == "Chrome Prospects"
    assert (cards[0].is_rookie, cards[0].is_auto, cards[0].is_relic) == (True, False, False)
    assert (cards[2].is_rookie, cards[2].is_auto) == (True, True)
    assert cards[3].subset == "Relics" and cards[3].is_relic     # sheet name stands in for a subset column


def run_checklist(release, transport):
    async def go():
        async with Fetcher(transport=transport, min_delay=0) as f:
            return await ToppsSource(use_browser=False).fetch_checklist(f, release)
    return asyncio.run(go())


def test_fetch_checklist_pdf_and_xls_return_nothing_without_fetching():
    transport, calls = blocked_site()
    rels = {r.source_id: r for r in parse_hub(HUB_HTML)}
    assert run_checklist(rels["2026-bowman-chrome-baseball-mega-box"], transport) == []
    assert run_checklist(rels["2026-topps-chrome-ufc-sapphire-edition"], transport) == []
    assert run_checklist(Release(manufacturer="topps", source_id="x", name="x"), transport) == []
    assert calls == []
    assert rels["2026-bowman-chrome-baseball-mega-box"].checklist_url == MEGA_BOX_PDF   # kept for the DB


def test_fetch_checklist_xlsx_goes_through_fetcher():
    calls = []
    data = xlsx_bytes()

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        return httpx.Response(200, content=data, headers={
            "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
    rel = parse_hub(HUB_HTML)[0]
    rel.checklist_url = "https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2026_Bowman_Baseball_Checklist.xlsx?v=1"
    cards = run_checklist(rel, httpx.MockTransport(handler))
    assert len(cards) == 4 and cards[0].player == "Konnor Griffin"
    assert calls[-1] == rel.checklist_url


# --- live ---

@pytest.mark.live
def test_live_topps_hub_via_browser():
    async def go():
        async with Fetcher() as f:
            return await ToppsSource().fetch_releases(f)
    try:
        releases = asyncio.run(go())
    except Blocked as e:
        pytest.skip(f"topps.com blocked: {e}")
    assert len(releases) > 500
    assert any(r.sport == "baseball" and r.checklist_url for r in releases)
    assert any(r.release_date for r in releases)
