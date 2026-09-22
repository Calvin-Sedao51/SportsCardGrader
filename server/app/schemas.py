from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

CardCategory = Literal["sports", "pokemon", "yugioh", "magic", "onepiece",
                       "other_tcg", "other"]

class Identity(BaseModel):
    subject: str  # player (sports) or card/character name (TCG)
    category: CardCategory = "sports"  # lenient default for models that omit it
    year: str
    set_name: str
    card_number: Optional[str] = None
    variant: Optional[str] = None
    search_string: str
    confidence: float = Field(ge=0, le=1)

class AreaObservation(BaseModel):
    area: Literal["corners", "edges", "surface", "centering"]
    severity: Literal["none", "minor", "moderate", "heavy"]
    note: str

class Condition(BaseModel):
    observations: list[AreaObservation]
    grade_low: float = Field(ge=1, le=10)
    grade_high: float = Field(ge=1, le=10)

    @model_validator(mode="after")
    def range_ordered(self):
        if self.grade_low > self.grade_high:
            raise ValueError("grade_low must be <= grade_high")
        return self

class Authenticity(BaseModel):
    red_flags: list[str]
    risk: Literal["low", "caution", "high"]

class Slab(BaseModel):
    """Professional grading holder read from the label (the easiest vision read)."""
    company: Literal["PSA", "BGS", "SGC", "CGC", "TAG"]
    grade: str  # "9", "9.5", "10", ... — string because "Authentic" slabs exist

class VisionResult(BaseModel):
    photo_ok: bool
    photo_issue: Optional[str] = None
    identity: Optional[Identity] = None
    condition: Optional[Condition] = None  # null for slabbed cards — the slab already graded it
    slab: Optional[Slab] = None
    authenticity: Optional[Authenticity] = None
    ai_value_note: Optional[str] = None  # model's rough value memory; fallback when comps are empty

    @model_validator(mode="after")
    def fill_photo_issue(self):
        if not self.photo_ok and self.photo_issue is None:
            self.photo_issue = (
                "Photo could not be assessed — try retaking with less glare "
                "and the whole card in frame."
            )
        return self

class CompListing(BaseModel):
    title: str
    price: float = Field(ge=0)
    graded: bool
    grade_label: Optional[str] = None
    url: Optional[str] = None

class CompsSummary(BaseModel):
    source: Literal["active_listings", "sold"]
    raw_count: int = Field(ge=0)
    # *_low are robust lows (trimmed floors from comps._robust_low), not the
    # absolute cheapest listing — a lone $0.99 outlier never sets the floor.
    raw_low: Optional[float] = None
    raw_median: Optional[float] = None
    graded_count: int = Field(ge=0)
    graded_low: Optional[float] = None
    graded_median: Optional[float] = None

class Verdict(BaseModel):
    value_low: Optional[float] = None
    value_high: Optional[float] = None
    verdict: Literal["undervalued", "fair", "overpriced", "no_ask", "not_enough_data",
                     "authenticity_risk", "low_value", "high_value"]
    reasoning: str

class ScanResponse(BaseModel):
    vision: VisionResult
    comps: Optional[CompsSummary] = None
    comps_error: Optional[str] = None
    verdict: Optional[Verdict] = None
