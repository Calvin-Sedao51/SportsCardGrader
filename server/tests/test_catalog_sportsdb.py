"""Local sports DB as a catalog source, and how sports lookups are registered."""
import pytest

from app.catalog import FirstMatchSource, build_catalog_source
from app.catalog.cardsight import CardsightSource
from app.catalog.sportsdb import SportsDbSource
from app.config import Settings
from app.schemas import Identity
from app.sportsdb.db import SportsDb
from app.sportsdb.models import Card, Release


def ident(subject, set_name="Upper Deck Series 1", card_number="201", year="2025"):
    return Identity(subject=subject, category="sports", year=year, set_name=set_name,
                    card_number=card_number, search_string="x", confidence=0.9)


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "sportsdb.sqlite"
    with SportsDb(path) as db:
        rel_id, _ = db.upsert_release(Release(
            manufacturer="upperdeck", source_id="ud-1",
            name="2025-26 Upper Deck Series 1 Hockey", sport="hockey", year=2025,
            url="https://upperdeck.com/ud-1", image_url="https://img/ud-1.jpg"))
        db.upsert_cards(rel_id, [
            Card(card_number="201", player="Connor Bedard", subset="Young Guns", is_rookie=True),
            Card(card_number="1", player="Sidney Crosby", subset="Base"),
        ])
    return path


@pytest.fixture
def release_only_file(tmp_path):
    path = tmp_path / "releases.sqlite"
    with SportsDb(path) as db:
        db.upsert_release(Release(manufacturer="upperdeck", source_id="ud-1",
                                  name="2025-26 Upper Deck Series 1 Hockey", year=2025))
    return path


# --- lookup ---

async def test_lookup_matches_player_number_and_set(db_file):
    m = await SportsDbSource(db_file).lookup(ident("Connor Bedard"))
    assert m.source == "sportsdb" and m.catalog_id.startswith("sportsdb:")
    assert m.name == "Connor Bedard" and m.card_number == "201" and m.year == "2025"
    assert "Young Guns" in m.set_name
    assert m.url == "https://upperdeck.com/ud-1" and m.image_url == "https://img/ud-1.jpg"
    assert m.match_confidence >= 0.8


async def test_lookup_base_subset_is_just_the_release(db_file):
    m = await SportsDbSource(db_file).lookup(ident("Sidney Crosby", card_number="1"))
    assert m.set_name == "2025-26 Upper Deck Series 1 Hockey"


async def test_lookup_wrong_number_is_only_a_suggestion(db_file):
    right = await SportsDbSource(db_file).lookup(ident("Connor Bedard"))
    wrong = await SportsDbSource(db_file).lookup(ident("Connor Bedard", card_number="99"))
    assert wrong is not None and wrong.match_confidence < right.match_confidence


async def test_lookup_unknown_player_is_none(db_file):
    assert await SportsDbSource(db_file).lookup(ident("Nobody Nowhere")) is None


async def test_lookup_non_numeric_year_still_searches(db_file):
    assert await SportsDbSource(db_file).lookup(ident("Connor Bedard", year="2025-26")) is not None


async def test_lookup_missing_file_is_none_and_does_not_create_it(tmp_path):
    path = tmp_path / "absent.sqlite"
    assert await SportsDbSource(path).lookup(ident("Connor Bedard")) is None
    assert not path.exists()


# --- registry ---

def settings(path, key=""):
    return Settings(_env_file=None, catalog_enabled=True, sportsdb_path=str(path),
                    cardsight_api_key=key)


def test_settings_reads_sportsdb_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SPORTSDB_PATH", str(tmp_path / "x.sqlite"))
    assert Settings(_env_file=None).sportsdb_file == tmp_path / "x.sqlite"
    monkeypatch.delenv("SPORTSDB_PATH")
    from app.sportsdb.db import DEFAULT_DB_PATH
    assert Settings(_env_file=None).sportsdb_file == DEFAULT_DB_PATH


def test_registry_no_file_falls_back_to_cardsight_or_nothing(tmp_path):
    absent = tmp_path / "absent.sqlite"
    assert build_catalog_source("sports", settings(absent)) is None
    assert isinstance(build_catalog_source("sports", settings(absent, "k")), CardsightSource)
    assert not absent.exists()


def test_registry_release_only_db_is_not_used(release_only_file):
    assert build_catalog_source("sports", settings(release_only_file)) is None


def test_registry_db_with_cards_is_the_local_source(db_file):
    assert isinstance(build_catalog_source("sports", settings(db_file)), SportsDbSource)


def test_registry_db_and_key_try_local_first(db_file):
    src = build_catalog_source("sports", settings(db_file, "k"))
    assert isinstance(src, FirstMatchSource)
    assert [type(s) for s in src.sources] == [SportsDbSource, CardsightSource]


class Stub:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def lookup(self, identity):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


async def test_first_match_stops_at_first_hit():
    first, second = Stub(result="hit"), Stub(result="other")
    assert await FirstMatchSource([first, second]).lookup(ident("x")) == "hit"
    assert second.calls == 0


async def test_first_match_falls_through_none_and_errors():
    assert await FirstMatchSource([Stub(), Stub(result="hit")]).lookup(ident("x")) == "hit"
    assert await FirstMatchSource([Stub(error=RuntimeError("db locked")),
                                   Stub(result="hit")]).lookup(ident("x")) == "hit"
    assert await FirstMatchSource([Stub(), Stub()]).lookup(ident("x")) is None


async def test_first_match_reraises_only_when_every_source_failed():
    with pytest.raises(RuntimeError, match="db locked"):
        await FirstMatchSource([Stub(error=RuntimeError("db locked")),
                                Stub(error=RuntimeError("cardsight 500"))]).lookup(ident("x"))
    # A source answering None is still an answer, whichever side of the error.
    assert await FirstMatchSource([Stub(error=RuntimeError("db locked")),
                                   Stub()]).lookup(ident("x")) is None
    assert await FirstMatchSource([Stub(),
                                   Stub(error=RuntimeError("cardsight 500"))]).lookup(ident("x")) is None
