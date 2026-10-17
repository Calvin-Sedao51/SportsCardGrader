"""Wild Card adapter against trimmed copies of wildcardinc.com/checklists/ pages."""
import asyncio
from pathlib import Path

import httpx
import pytest

from app.sportsdb.http import Blocked, Fetcher
from app.sportsdb.models import Release
from app.sportsdb.sources.wildcard import WildCardSource

FIXTURES = Path(__file__).parent / "fixtures"
ROBOTS = "User-agent: *\nAllow: /\nDisallow: /models/\nDisallow: /textures/\nDisallow: /draco/\n"
JOKERS = "5-card-draw__2025-wild-card-jokers-wild-football"
HITS = "alumination__2026-wild-card-alumination-just-the-hits-football"
DRAW_2022 = "5-card-draw__2022-football-5-card-draw-checklist"
MENDOZA = "promos-exclusive__2026-wild-card-mega-mix-mendoza"
PAGES = {
    "/checklists/": "sportsdb_wildcard_index.html",
    f"/checklists/{JOKERS}.html": "sportsdb_wildcard_jokers.html",
    f"/checklists/{HITS}.html": "sportsdb_wildcard_alumination.html",
    f"/checklists/{DRAW_2022}.html": "sportsdb_wildcard_2022.html",
    f"/checklists/{MENDOZA}.html": "sportsdb_wildcard_mendoza.html",
}


def site(blocked_paths=()):
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS, headers={"content-type": "text/plain"})
        if path in blocked_paths:
            return httpx.Response(403, text="forbidden")
        if path in PAGES:
            return httpx.Response(200, text=(FIXTURES / PAGES[path]).read_text(encoding="utf-8"),
                                  headers={"content-type": "text/html; charset=UTF-8"})
        return httpx.Response(404, text="not found")
    return httpx.MockTransport(handler)


def run(coro_fn, transport=None):
    async def go():
        async with Fetcher(transport=transport or site(), min_delay=0) as f:
            return await coro_fn(f)
    return asyncio.run(go())


def releases():
    return run(lambda f: WildCardSource().fetch_releases(f))


def by_id(rels, source_id):
    return next(r for r in rels if r.source_id == source_id)


def test_fetch_releases_reads_the_embedded_index():
    rels = releases()
    assert len(rels) == 6
    jokers = by_id(rels, JOKERS)
    assert jokers.manufacturer == "wildcard"
    assert jokers.name == "2025 Wild Card Jokers Wild™ Football"
    assert (jokers.sport, jokers.year, jokers.release_date) == ("football", 2025, "2025-12-01")
    assert jokers.url == ("https://wildcardinc.com/checklists/"
                          "5-card-draw__2025-wild-card-jokers-wild-football.html")
    assert jokers.checklist_url == jokers.url
    assert jokers.raw["product_line"] == "5 Card Draw™"
    assert jokers.raw["release_month"] == "December"


def test_fetch_releases_maps_site_sport_and_falls_back_to_name():
    rels = releases()
    matte = by_id(rels, "matte__2026-matte-metal-graffiti-multi-sport-hobby")
    assert (matte.sport, matte.product_type) == ("multi", "Hobby")
    # Soccer isn't a Sport the DB knows; the name doesn't say otherwise.
    yamal = by_id(rels, "5-card-draw__2025-wild-card-jokers-wild-lamine-yamal-nationals")
    assert yamal.sport == "other"
    hits = by_id(rels, HITS)
    assert (hits.sport, hits.year) == ("football", 2026)
    assert (hits.release_date, hits.product_type) == (None, None)
    qb1 = by_id(rels, "promos-exclusive__updated-wild-card-qb1-on-the-clock-web-exclusive")
    assert (qb1.year, qb1.release_date) == (None, None)   # site shows "—" and no month


def test_fetch_checklist_one_card_per_subset_variant_and_player():
    rels = releases()
    cards = run(lambda f: WildCardSource().fetch_checklist(
        f, by_id(rels, JOKERS)))
    assert len(cards) == 49 * 4   # 49 players x (Auto Jokers, Auto Jokers True, Jokers, Jokers True)
    auto = next(c for c in cards if c.player == "Bryce Underwood" and c.subset == "Auto Jokers")
    assert auto.card_number == "SDJW-BUA‹clr›(01-10)"
    assert (auto.is_auto, auto.is_relic, auto.is_rookie) == (True, False, False)
    assert (auto.print_run, auto.parallel) == (1, None)
    assert auto.raw["code"] == "SDJW-‹INI›A‹clr›(01-10)"
    assert auto.raw["parallels"]["Rainbow"] == {"Orange (o)": 1, "Purple (p)": 1, "Green (g)": 1,
                                                "Blue (b)": 1, "Red (r)": 1}
    base = next(c for c in cards if c.player == "Brett Favre" and c.subset == "Jokers")
    assert (base.card_number, base.is_auto) == ("SDJW-BFN‹clr›(01-10)", False)


def test_fetch_checklist_disambiguates_shared_initials():
    rels = releases()
    cards = run(lambda f: WildCardSource().fetch_checklist(
        f, by_id(rels, JOKERS)))
    numbers = {c.player: c.card_number for c in cards if c.subset == "Auto Jokers"}
    assert numbers["Joe Burrow"] == "SDJW-JBURROWA‹clr›(01-10)"
    assert numbers["Jack Bech"] == "SDJW-JBECHA‹clr›(01-10)"
    assert len(set(numbers.values())) == 49


def test_roster_codes_never_collide():
    from app.sportsdb.sources.wildcard import _roster_codes
    names = ("Josh Allen", "Jordan Allen", "Jalen Hurts", "Harold Fannin Jr.", "Brett Favre 1",
             "Brett Favre 12")
    data = {"subjects": [{"name": n} for n in names]}
    codes = dict(_roster_codes(data, {"roster": "subjects"}, "base"))
    assert codes == {"Josh Allen": "JOSHALLEN", "Jordan Allen": "JORDANALLEN", "Jalen Hurts": "JH",
                     "Harold Fannin Jr.": "HF", "Brett Favre 1": "BF1", "Brett Favre 12": "BF12"}


def test_fetch_checklist_uses_variant_rosters_and_dual_names():
    rels = releases()
    cards = run(lambda f: WildCardSource().fetch_checklist(
        f, by_id(rels, HITS)))
    base = [c for c in cards if c.subset == "Base"]
    assert len([c for c in base if c.is_auto]) == 4         # rosterAuto: hits4
    assert len([c for c in base if not c.is_auto]) == 49
    aa_auto = next(c for c in base if c.player == "Aaron Anderson" and c.is_auto)
    aa_base = next(c for c in base if c.player == "Aaron Anderson" and not c.is_auto)
    assert (aa_auto.card_number, aa_auto.print_run) == ("JHBC-AAA", 1)
    assert (aa_base.card_number, aa_base.print_run) == ("JHBC-AA", None)   # ladder runs 1..8
    duals = [c for c in cards if c.subset == "Comix Dual"]
    assert len(duals) == 20
    dual = next(c for c in duals if c.player == "Patrick Mahomes/Joe Burrow")
    assert (dual.card_number, dual.is_auto) == ("ACDA-PMJB", True)
    assert len(cards) == 4 + 49 + 4 + 49 + 20


def test_fetch_checklist_without_code_templates_numbers_by_initials():
    rels = releases()
    cards = run(lambda f: WildCardSource().fetch_checklist(
        f, by_id(rels, DRAW_2022)))
    assert len(cards) == 107 * 4
    card = next(c for c in cards if c.player == "Aidan Hutchinson" and c.subset == "Classic")
    assert (card.card_number, card.is_auto, card.print_run) == ("AH", False, None)
    assert card.raw["code"] is None
    assert card.raw["parallels"]["Gold Foil"]["Orange"] == 5


def test_fetch_checklist_numbered_subjects_and_inline_roster():
    rel = Release(manufacturer="wildcard", source_id=MENDOZA, name="2026 Wild Card Mega Mix Mendoza",
                  url=f"https://wildcardinc.com/checklists/{MENDOZA}.html")
    cards = run(lambda f: WildCardSource().fetch_checklist(f, rel))
    boo = [c for c in cards if c.subset == "Boo"]
    assert len(boo) == 17 * 2                      # "Fernando Mendoza 1" ... "Fernando Mendoza 17"
    assert len({(c.card_number, c.subset) for c in boo}) == 34
    # The template already names the player (no ‹INI› slot), so the initials are appended.
    numbers = {c.player: c.card_number for c in boo if c.is_auto}
    assert numbers["Fernando Mendoza 2"] == "HHBOO-FM(1-17)A-FM2"
    assert numbers["Fernando Mendoza 12"] == "HHBOO-FM(1-17)A-FM12"
    picasso = [c for c in cards if c.subset == "Comix Covers Picasso True 1 of 1"]   # inline roster
    assert [(c.player, c.is_auto) for c in picasso] == [("Fernando Mendoza", True),
                                                        ("Fernando Mendoza", False)]
    assert len(cards) == 36


def test_blocked_propagates():
    jokers = by_id(releases(), JOKERS)
    with pytest.raises(Blocked):
        run(lambda f: WildCardSource().fetch_checklist(f, jokers),
            transport=site(blocked_paths={f"/checklists/{JOKERS}.html"}))
    with pytest.raises(Blocked):
        run(lambda f: WildCardSource().fetch_releases(f),
            transport=site(blocked_paths={"/checklists/"}))


@pytest.mark.live
def test_live_wildcard_index_and_one_checklist():
    async def go():
        async with Fetcher() as f:
            rels = await WildCardSource().fetch_releases(f)
            jokers = by_id(rels, JOKERS)
            return rels, await WildCardSource().fetch_checklist(f, jokers)
    rels, cards = asyncio.run(go())
    assert len(rels) > 150
    assert {r.sport for r in rels} >= {"football", "baseball", "basketball", "multi"}
    assert any(c.player == "Bryce Underwood" and c.is_auto for c in cards)
