import json
from pathlib import Path

import httpx
import pytest

from app.catalog import build_catalog_source
from app.catalog.cardsight import CardsightSource
from app.catalog.optcg import OptcgSource
from app.catalog.scryfall import ScryfallSource
from app.catalog.tcgdex import TcgdexSource
from app.catalog.ygoprodeck import YgoprodeckSource
from app.config import Settings
from app.schemas import Identity

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def ident(subject, category, set_name, card_number=None, year="1999", variant=None):
    return Identity(subject=subject, category=category, year=year, set_name=set_name,
                    card_number=card_number, variant=variant, search_string="x",
                    confidence=0.9)


def failing_transport(status=500):
    return httpx.MockTransport(lambda request: httpx.Response(status, json={}))


# --- TCGdex (Pokémon) ---

def tcgdex_transport(empty=False):
    data = load("catalog_tcgdex_charizard.json")
    def handler(request):
        path = request.url.path
        if path == "/v2/en/cards":
            return httpx.Response(200, json=[] if empty else data["list"])
        card_id = path.rsplit("/", 1)[-1]
        if card_id in data["details"]:
            return httpx.Response(200, json=data["details"][card_id])
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_tcgdex_picks_base_set_printing():
    src = TcgdexSource(transport=tcgdex_transport())
    m = await src.lookup(ident("Charizard", "pokemon", "Base Set", "4/102"))
    assert m.source == "tcgdex" and m.catalog_id == "base1-4"
    assert m.set_name == "Base Set" and m.card_number == "4"
    assert m.match_confidence == 1.0
    assert m.image_url.endswith("/high.webp")


async def test_tcgdex_no_results_is_none():
    src = TcgdexSource(transport=tcgdex_transport(empty=True))
    assert await src.lookup(ident("Nobodymon", "pokemon", "Base Set", "4")) is None


async def test_tcgdex_http_error_raises():
    with pytest.raises(httpx.HTTPStatusError):
        await TcgdexSource(transport=failing_transport()).lookup(
            ident("Charizard", "pokemon", "Base Set", "4"))


# --- Scryfall (Magic) ---

def scryfall_transport(seen=None):
    data = load("catalog_scryfall_bolt.json")
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.params["q"].startswith('!"Lightning Bolt"'):
            return httpx.Response(200, json=data)
        return httpx.Response(404, json={"object": "error", "code": "not_found"})
    return httpx.MockTransport(handler)


async def test_scryfall_picks_printing_by_set_and_number():
    seen = []
    src = ScryfallSource(transport=scryfall_transport(seen))
    m = await src.lookup(ident("Lightning Bolt", "magic", "Magic 2010", "146"))
    assert (m.set_name, m.card_number, m.year) == ("Magic 2010", "146", "2009")
    assert m.url.startswith("https://scryfall.com/")
    assert "CardScanner" in seen[0].headers["User-Agent"]


async def test_scryfall_not_found_is_none():
    src = ScryfallSource(transport=scryfall_transport())
    assert await src.lookup(ident("Not A Card", "magic", "Alpha")) is None


# --- YGOPRODeck (Yu-Gi-Oh!) ---

def ygo_transport():
    data = load("catalog_ygoprodeck_blueeyes.json")
    def handler(request):
        if request.url.params["fname"] == "Blue-Eyes White Dragon":
            return httpx.Response(200, json=data)
        return httpx.Response(400, json={"error": "No card matching your query"})
    return httpx.MockTransport(handler)


async def test_ygoprodeck_matches_set_code_across_regions():
    src = YgoprodeckSource(transport=ygo_transport())
    m = await src.lookup(ident("Blue-Eyes White Dragon", "yugioh",
                               "Legend of Blue Eyes White Dragon", "LOB-EN001"))
    assert m.set_name == "Legend of Blue Eyes White Dragon"
    assert m.card_number.startswith("LOB-")
    assert m.match_confidence == 1.0


async def test_ygoprodeck_no_match_is_none():
    src = YgoprodeckSource(transport=ygo_transport())
    assert await src.lookup(ident("Zzz", "yugioh", "Nothing", "XXX-001")) is None


# --- OPTCG (One Piece) ---

def optcg_transport():
    data = load("catalog_optcg_zoro.json")
    return httpx.MockTransport(lambda request: httpx.Response(200, json=data))


async def test_optcg_prefers_base_printing():
    m = await OptcgSource(transport=optcg_transport()).lookup(
        ident("Roronoa Zoro", "onepiece", "Romance Dawn", "OP01-025"))
    assert m.card_number == "OP01-025" and "(Parallel)" not in m.name


async def test_optcg_prefers_parallel_when_vision_saw_one():
    m = await OptcgSource(transport=optcg_transport()).lookup(
        ident("Roronoa Zoro", "onepiece", "Romance Dawn", "OP01-025",
              variant="Parallel alt art"))
    assert m.card_number == "OP01-025" and "(Parallel)" in m.name


# --- CardSight (sports) ---

CARDSIGHT_PAGE = {"total_count": 2, "skip": 0, "take": 25, "cards": [
    {"id": "c1", "releaseId": "r1", "setId": "s1", "number": "280",
     "name": "Luka Doncic", "setName": "Base Set", "releaseName": "Panini Prizm",
     "releaseYear": "2018"},
    {"id": "c2", "releaseId": "r1", "setId": "s2", "number": "280",
     "name": "Luka Doncic", "setName": "Rated Rookies", "releaseName": "Donruss",
     "releaseYear": "2018"},
]}


async def test_cardsight_sends_key_and_maps_release_as_set():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=CARDSIGHT_PAGE)
    src = CardsightSource("secret", transport=httpx.MockTransport(handler))
    m = await src.lookup(ident("Luka Doncic", "sports", "Panini Prizm", "#280",
                               year="2018"))
    assert seen[0].headers["X-API-Key"] == "secret"
    assert seen[0].url.path == "/v1/catalog/cards"
    assert dict(seen[0].url.params) == {"name": "Luka Doncic", "take": "25",
                                        "year": "2018", "number": "280"}
    assert (m.catalog_id, m.set_name, m.year) == ("c1", "Panini Prizm", "2018")


async def test_cardsight_retries_without_number():
    calls = []
    def handler(request):
        calls.append(dict(request.url.params))
        if "number" in request.url.params:
            return httpx.Response(200, json={**CARDSIGHT_PAGE, "cards": []})
        return httpx.Response(200, json=CARDSIGHT_PAGE)
    src = CardsightSource("k", transport=httpx.MockTransport(handler))
    m = await src.lookup(ident("Luka Doncic", "sports", "Panini Prizm", "999",
                               year="2018"))
    assert len(calls) == 2 and "number" not in calls[1]
    assert m is not None and m.match_confidence < 0.8  # number disagrees -> suggestion


async def test_cardsight_bad_key_raises():
    src = CardsightSource("bad", transport=failing_transport(401))
    with pytest.raises(httpx.HTTPStatusError):
        await src.lookup(ident("Luka Doncic", "sports", "Panini Prizm", "280"))


# --- registry ---

@pytest.mark.parametrize("category,cls", [
    ("pokemon", TcgdexSource), ("magic", ScryfallSource),
    ("yugioh", YgoprodeckSource), ("onepiece", OptcgSource),
])
def test_registry_free_sources(category, cls):
    assert isinstance(build_catalog_source(category, Settings(catalog_enabled=True)), cls)


def test_registry_sports_needs_cardsight_key():
    assert build_catalog_source("sports", Settings(catalog_enabled=True,
                                                   cardsight_api_key="")) is None
    assert isinstance(build_catalog_source(
        "sports", Settings(catalog_enabled=True, cardsight_api_key="k")), CardsightSource)


@pytest.mark.parametrize("category", ["other_tcg", "other"])
def test_registry_unsupported_categories(category):
    assert build_catalog_source(category, Settings(catalog_enabled=True)) is None


def test_registry_disabled():
    assert build_catalog_source("pokemon", Settings(catalog_enabled=False)) is None
