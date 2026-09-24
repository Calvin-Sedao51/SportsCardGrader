import pytest
from pydantic import ValidationError
from app.schemas import (Identity, Condition, Slab, VisionResult, Verdict,
                         CompListing)

def test_identity_confidence_bounds():
    with pytest.raises(ValidationError):
        Identity(subject="Luka Doncic", year="2018", set_name="Prizm",
                 search_string="x", confidence=1.5)

def test_condition_rejects_inverted_range():
    with pytest.raises(ValidationError):
        Condition(observations=[], grade_low=8, grade_high=6)

def test_vision_result_photo_rejected_needs_no_identity():
    r = VisionResult(photo_ok=False, photo_issue="too blurry")
    assert r.identity is None

def test_condition_accepts_half_grades():
    c = Condition(observations=[], grade_low=6.5, grade_high=8.5)
    assert c.grade_low == 6.5
    assert c.grade_high == 8.5

def test_comp_listing_rejects_negative_price():
    with pytest.raises(ValidationError):
        CompListing(title="Luka Doncic Prizm", price=-1, graded=False)

def test_vision_result_photo_not_ok_fills_default_issue():
    r = VisionResult(photo_ok=False)
    assert isinstance(r.photo_issue, str)
    assert r.photo_issue

def test_slab_holds_company_and_string_grade():
    s = Slab(company="BGS", grade="9.5")
    assert s.company == "BGS" and s.grade == "9.5"
    # Non-numeric grades exist ("Authentic" slabs) — grade is a string.
    assert Slab(company="PSA", grade="Authentic").grade == "Authentic"

def test_slab_rejects_unknown_company():
    with pytest.raises(ValidationError):
        Slab(company="HGA", grade="9")

def test_vision_result_slab_defaults_to_none():
    assert VisionResult(photo_ok=True).slab is None

def test_vision_result_accepts_slab_with_null_condition():
    r = VisionResult(photo_ok=True, slab=Slab(company="PSA", grade="9"))
    assert r.slab.company == "PSA" and r.condition is None

def test_identity_category_defaults_to_sports():
    i = Identity(subject="Luka Doncic", year="2018", set_name="Prizm",
                 search_string="x", confidence=0.9)
    assert i.category == "sports"

def test_identity_accepts_tcg_category():
    i = Identity(subject="Charizard", year="1999", set_name="Base Set",
                 category="pokemon", search_string="x", confidence=0.9)
    assert i.category == "pokemon"

def test_identity_rejects_unknown_category():
    with pytest.raises(ValidationError):
        Identity(subject="Charizard", year="1999", set_name="Base Set",
                 category="beanie_babies", search_string="x", confidence=0.9)
