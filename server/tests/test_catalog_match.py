import pytest

from app.catalog import VERIFIED_THRESHOLD, apply_match
from app.catalog.match import NAME_ONLY_CAP, name_similarity, norm_number, score, text_similarity
from app.schemas import CatalogMatch, Identity


@pytest.mark.parametrize("raw,expected", [
    ("025/165", "25"),
    ("#4", "4"),
    ("4/102", "4"),
    ("  146 ", "146"),
    ("LOB-EN001", "LOB-1"),
    ("LOB-E001", "LOB-1"),
    ("LOB-001", "LOB-1"),
    ("OP01-025", "OP01-25"),
    ("RC-15", "RC-15"),
    ("113p", "113P"),
    (None, ""),
])
def test_norm_number(raw, expected):
    assert norm_number(raw) == expected


def test_set_names_ignore_punctuation_and_ampersands():
    assert text_similarity("Scarlet & Violet 151", "Scarlet and Violet—151") == 1.0


def test_name_similarity_handles_parallels_and_double_faced_cards():
    assert name_similarity("Roronoa Zoro", "Roronoa Zoro (025) (Parallel)") == 1.0
    assert name_similarity("Lightning Bolt", "Emeritus of Conflict // Lightning Bolt") == 1.0
    assert name_similarity("Charizard", "Charizard ex") >= 0.85
    assert name_similarity("Charizard", "Blastoise") < 0.5


def test_number_and_name_match_scores_high():
    s = score("Charizard", "4/102", "Base Set",
              cand_name="Charizard", cand_number="4", cand_set="Base Set")
    assert s == 1.0


def test_wrong_number_drops_below_verified():
    s = score("Charizard", "4", "Base Set",
              cand_name="Charizard", cand_number="11", cand_set="Base Set")
    assert s < VERIFIED_THRESHOLD


def test_name_only_is_capped():
    s = score("Charizard", None, None, cand_name="Charizard", cand_number="4",
              cand_set="Base Set")
    assert s == NAME_ONLY_CAP


IDENTITY = Identity(subject="Pikachu", category="pokemon", year="2023",
                    set_name="Scarlet & Violet 151", card_number="025/165",
                    search_string="x", confidence=0.9)


def match(conf, **kw):
    base = dict(source="tcgdex", catalog_id="sv03.5-025", name="Pikachu",
                set_name="Scarlet & Violet—Pokémon 151", card_number="025",
                year=None, match_confidence=conf)
    return CatalogMatch(**{**base, **kw})


def test_apply_match_overwrites_set_and_number_when_verified():
    out = apply_match(IDENTITY, match(0.95))
    assert out.set_name == "Scarlet & Violet—Pokémon 151"
    assert out.card_number == "025"
    assert out.year == "2023"  # no catalog year -> keep vision's
    assert out.subject == "Pikachu" and out.search_string == "x"


def test_apply_match_keeps_richer_ai_set_name():
    out = apply_match(IDENTITY, match(0.95, set_name="151"))
    assert out.set_name == "Scarlet & Violet 151"
    assert out.card_number == "025"


def test_apply_match_ignores_suggestions():
    assert apply_match(IDENTITY, match(0.7)) is IDENTITY
    assert apply_match(IDENTITY, None) is IDENTITY


SPORTS_IDENTITY = Identity(subject="Luka Doncic", category="sports", year="2018",
                           set_name="Panini Prizm", card_number="280",
                           search_string="2018 Panini Prizm Luka Doncic #280",
                           confidence=0.9)


def sports_match(conf, **kw):
    base = dict(source="cardsight", catalog_id="prizm-280", name="Luka Doncic",
                set_name="Panini Prizm", card_number="289", year=None,
                match_confidence=conf)
    return CatalogMatch(**{**base, **kw})


def test_apply_match_resyncs_search_string_card_number():
    # A verified number correction must reach the string that actually prices
    # the card, not just the display fields.
    out = apply_match(SPORTS_IDENTITY, sports_match(0.95))
    assert out.card_number == "289"
    assert out.search_string == "2018 Panini Prizm Luka Doncic #289"


def test_apply_match_resyncs_search_string_year():
    out = apply_match(SPORTS_IDENTITY, sports_match(0.95, year="2019"))
    assert out.year == "2019"
    assert out.search_string == "2019 Panini Prizm Luka Doncic #289"


def test_apply_match_set_only_correction_leaves_search_string():
    # set_name is too free-form to substring-replace safely; a set-only
    # correction (no number/year change) shouldn't touch search_string.
    out = apply_match(SPORTS_IDENTITY, sports_match(0.95, card_number=None,
                                                    set_name="Panini Prizm Draft Picks"))
    assert out.set_name == "Panini Prizm Draft Picks"
    assert out.search_string == SPORTS_IDENTITY.search_string
