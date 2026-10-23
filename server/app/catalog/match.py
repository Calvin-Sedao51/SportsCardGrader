"""Normalization and scoring shared by every catalog adapter.

Adapters fetch candidates; this module decides how well each one matches the
vision identity. Keep all the tuning here so the sources stay comparable.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

# Weights for the components that are present on both sides; missing ones are
# dropped and the rest re-normalized.
NAME_WEIGHT, NUMBER_WEIGHT, SET_WEIGHT = 0.5, 0.3, 0.2
# A name alone can't pin a specific printing (Charizard has dozens).
NAME_ONLY_CAP = 0.65

# "LOB-EN001" / "LOB-E001" / "LOB-001" -> prefix + region + digits.
_CODE = re.compile(r"^([A-Z0-9]+)-([A-Z]{0,2})(\d+)$")


def norm_text(value: Optional[str]) -> str:
    if not value:
        return ""
    # Drop accents (é -> e) but keep other punctuation as a word break, so an
    # em dash in "Violet—151" doesn't glue the words together.
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = plain.lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def strip_parens(value: str) -> str:
    """'Roronoa Zoro (001) (Parallel)' -> 'Roronoa Zoro'."""
    return re.sub(r"\s*\([^)]*\)", "", value).strip()


def norm_number(value: Optional[str]) -> str:
    """'#025/165' -> '25'; 'LOB-EN001' -> 'LOB-1'; 'OP01-001' -> 'OP01-1'."""
    if not value:
        return ""
    v = value.strip().upper().lstrip("#").split("/")[0].strip()
    v = re.sub(r"\s+", "", v)
    m = _CODE.match(v)
    if m:
        # Region letters (EN/E/...) differ between printings of the same card.
        return f"{m.group(1)}-{int(m.group(3))}"
    if v.isdigit():
        return str(int(v))
    return v


def text_similarity(a: Optional[str], b: Optional[str]) -> float:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    # "Charizard" vs "Charizard ex": every word of one side is in the other.
    if ta <= tb or tb <= ta:
        ratio = max(ratio, 0.85)
    return ratio


def name_similarity(subject: str, candidate: str) -> float:
    """Double-faced cards ('A // B') match on either face."""
    faces = [strip_parens(f) for f in candidate.split("//")] + [strip_parens(candidate)]
    return max(text_similarity(subject, f) for f in faces)


def score(subject: str, card_number: Optional[str], set_name: Optional[str], *,
          cand_name: str, cand_number: Optional[str], cand_set: Optional[str]) -> float:
    parts: list[tuple[float, float]] = [(NAME_WEIGHT, name_similarity(subject, cand_name))]
    if norm_number(card_number) and norm_number(cand_number):
        parts.append((NUMBER_WEIGHT,
                      1.0 if norm_number(card_number) == norm_number(cand_number) else 0.0))
    if norm_text(set_name) and norm_text(cand_set):
        parts.append((SET_WEIGHT, text_similarity(set_name, cand_set)))
    total = sum(w for w, _ in parts)
    result = sum(w * v for w, v in parts) / total
    if len(parts) == 1:
        result = min(result, NAME_ONLY_CAP)
    return round(result, 3)
