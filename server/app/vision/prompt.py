"""Vision prompt text and strict parsing of the model's JSON into VisionResult.

This module owns both sides of the vision contract: ``build_prompt`` produces
the instruction text sent alongside the card photos, and ``parse_vision_json``
turns the model's raw reply back into a validated ``VisionResult``. Parsing is
lenient about LLM wrapping (code fences, leading prose, trailing chatter) but
strict about the JSON payload itself.
"""

import json
import re

from pydantic import ValidationError

from app.data.grading_scales import PSA_SCALE
from app.schemas import VisionResult


class VisionParseError(Exception):
    pass


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def build_prompt() -> str:
    scale = "\n".join(f"PSA {g}: {e['label']} — {e['description']}"
                      for g, e in sorted(PSA_SCALE.items(), key=lambda kv: int(kv[0]), reverse=True))
    return f"""You are analyzing photos of a trading card — a sports card or a trading card game
(TCG) card such as Pokémon, Yu-Gi-Oh!, Magic: The Gathering, or One Piece — for a collector
deciding whether to buy it.

Respond with ONLY a JSON object, no prose, matching exactly this shape:
{{
  "photo_ok": bool,            // false if too blurry/glared/cropped to judge
  "photo_issue": str|null,     // if photo_ok is false: what to fix when retaking
  "identity": {{"subject": str,  // player name (sports) or card/character name (TCG)
               "category": "sports"|"pokemon"|"yugioh"|"magic"|"onepiece"|"other_tcg"|"other",
                             // other_tcg = any other card game (Lorcana, Digimon...); other = non-game collectible card
               "year": str, "set_name": str, "card_number": str|null,
               "variant": str|null, "search_string": str, "confidence": float 0-1}} | null,
  "condition": {{"observations": [{{"area": "corners"|"edges"|"surface"|"centering",
                "severity": "none"|"minor"|"moderate"|"heavy", "note": str}}],
                "grade_low": number 1-10, "grade_high": number 1-10}} | null,
  "slab": {{"company": "PSA"|"BGS"|"SGC"|"CGC"|"TAG", "grade": str}} | null,
                               // ONLY if the card is in a professional grading holder — read the label
  "authenticity": {{"red_flags": [str], "risk": "low"|"caution"|"high"}} | null,
  "ai_value_note": str|null    // rough market value from your knowledge, one sentence, or null
}}

Rules:
- "search_string" must be a normalized eBay search, e.g.
  sports: "2018 Panini Prizm Luka Doncic #280 Silver"
  TCG:    "1999 Pokemon Base Set Charizard #4 Holo 1st Edition"
- For TCG cards, put edition (1st Edition/Unlimited), holo/foil, language, and rarity in
  "variant" AND in "search_string" — these dominate TCG pricing.
- Grade as a RANGE; half grades like 6.5 or 8.5 are allowed. A phone photo cannot distinguish
  PSA 9 from 10 — be honest about the spread.
- Authenticity red_flags are warning signs (print dot pattern, era-inconsistent fonts/logos,
  gloss, miscut suggesting a reprint sheet; for TCGs also missing texture/foil pattern or
  wrong card-back shade), NOT a certification.
- If the card is slabbed: read company and grade from the label; include the company and
  grade in search_string (e.g. "2018 Panini Prizm Luka Doncic #280 PSA 9"); set condition
  to null (the slab already graded it); authenticity red flags should consider fake-slab
  signs (label font, hologram).
- If photo_ok is false, set identity/condition/authenticity to null.

PSA grading scale for reference (applies to sports and TCG cards alike):
{scale}"""


def parse_vision_json(raw: str) -> VisionResult:
    cleaned = _FENCE_RE.sub("", raw.strip())
    # LLMs often wrap the JSON in prose ("Here is the JSON: ... Let me know!").
    # Keep only the span from the first "{" to the last "}".
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise VisionParseError("Model returned unparseable analysis: no JSON object found")
    cleaned = cleaned[start:end + 1]
    try:
        return VisionResult.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as e:
        raise VisionParseError(f"Model returned unparseable analysis: {type(e).__name__}") from e
