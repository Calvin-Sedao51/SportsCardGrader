import pytest

from app.vision.prompt import VisionParseError, build_prompt, parse_vision_json


def test_prompt_mentions_psa_and_json():
    p = build_prompt()
    assert "PSA" in p and "JSON" in p and "photo_ok" in p


def test_prompt_lists_psa_10_before_psa_1():
    p = build_prompt()
    assert p.index("PSA 10:") < p.index("PSA 1:")


def test_parse_strips_code_fences():
    raw = '```json\n{"photo_ok": false, "photo_issue": "blurry"}\n```'
    r = parse_vision_json(raw)
    assert r.photo_ok is False


def test_parse_full_result():
    raw = ('{"photo_ok": true, "identity": {"subject": "Luka Doncic", "year": "2018", '
           '"set_name": "Panini Prizm", "card_number": "280", "variant": null, '
           '"search_string": "2018 Panini Prizm Luka Doncic #280", "confidence": 0.92}, '
           '"condition": {"observations": [{"area": "corners", "severity": "minor", '
           '"note": "slight fray top-left"}], "grade_low": 6, "grade_high": 8}, '
           '"authenticity": {"red_flags": [], "risk": "low"}, "ai_value_note": null}')
    r = parse_vision_json(raw)
    assert r.identity.subject == "Luka Doncic"
    assert r.condition.grade_high == 8


def test_parse_garbage_raises():
    with pytest.raises(VisionParseError):
        parse_vision_json("I think this is a nice card!")


def test_parse_accepts_half_grades():
    raw = ('{"photo_ok": true, "condition": {"observations": [], '
           '"grade_low": 6.5, "grade_high": 8.5}}')
    r = parse_vision_json(raw)
    assert r.condition.grade_low == 6.5
    assert r.condition.grade_high == 8.5


def test_parse_ignores_extra_unknown_keys():
    raw = ('{"photo_ok": true, "chatter": "sure, here you go!", '
           '"identity": null, "confidence_notes": ["extra"], "ai_value_note": null}')
    r = parse_vision_json(raw)
    assert r.photo_ok is True


def test_parse_photo_not_ok_fills_photo_issue():
    r = parse_vision_json('{"photo_ok": false, "photo_issue": null}')
    assert r.photo_ok is False
    assert isinstance(r.photo_issue, str) and r.photo_issue


def test_prompt_mentions_slab_shape():
    p = build_prompt()
    assert '"slab"' in p
    assert "grading holder" in p
    # Slab rules: read the label, fold company+grade into the search string,
    # null out condition, and watch for fake-slab signs.
    assert "PSA 9" in p
    assert "hologram" in p


def test_parse_round_trips_slab():
    raw = ('{"photo_ok": true, "identity": {"subject": "Luka Doncic", "year": "2018", '
           '"set_name": "Panini Prizm", "card_number": "280", "variant": null, '
           '"search_string": "2018 Panini Prizm Luka Doncic #280 PSA 9", "confidence": 0.9}, '
           '"condition": null, "slab": {"company": "PSA", "grade": "9"}, '
           '"authenticity": {"red_flags": [], "risk": "low"}, "ai_value_note": null}')
    r = parse_vision_json(raw)
    assert r.slab is not None
    assert r.slab.company == "PSA" and r.slab.grade == "9"
    assert r.condition is None


def test_parse_without_slab_key_defaults_to_none():
    r = parse_vision_json('{"photo_ok": true}')
    assert r.slab is None


def test_parse_tolerates_leading_prose_and_trailing_text():
    raw = ('Here is the JSON:\n```json\n'
           '{"photo_ok": false, "photo_issue": "glare"}\n'
           '```\nLet me know if you need anything else!')
    r = parse_vision_json(raw)
    assert r.photo_ok is False
    assert r.photo_issue == "glare"


def test_prompt_covers_tcg():
    p = build_prompt()
    assert "Pokémon" in p and "category" in p and "1st Edition" in p


def test_parse_category_round_trips():
    raw = ('{"photo_ok": true, "identity": {"subject": "Charizard", "category": "pokemon", '
           '"year": "1999", "set_name": "Base Set", "card_number": "4", '
           '"variant": "Holo 1st Edition", '
           '"search_string": "1999 Pokemon Base Set Charizard #4 Holo 1st Edition", '
           '"confidence": 0.9}, "condition": {"observations": [], '
           '"grade_low": 5, "grade_high": 7}, '
           '"authenticity": {"red_flags": [], "risk": "low"}, "ai_value_note": null}')
    r = parse_vision_json(raw)
    assert r.identity.category == "pokemon"
    assert r.identity.subject == "Charizard"


def test_parse_missing_category_defaults_to_sports():
    raw = ('{"photo_ok": true, "identity": {"subject": "Luka Doncic", "year": "2018", '
           '"set_name": "Panini Prizm", "card_number": "280", "variant": null, '
           '"search_string": "2018 Panini Prizm Luka Doncic #280", "confidence": 0.92}, '
           '"condition": {"observations": [], "grade_low": 6, "grade_high": 8}, '
           '"authenticity": {"red_flags": [], "risk": "low"}, "ai_value_note": null}')
    assert parse_vision_json(raw).identity.category == "sports"
