"""Adapter contract: what every manufacturer source hands back."""
import re
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

Sport = Literal["basketball", "hockey", "baseball", "football", "multi", "other"]
SPORTS: tuple[Sport, ...] = ("basketball", "hockey", "baseball", "football", "multi", "other")

# Order matters: the first family whose keyword appears wins, except that two
# different families in one name means a multi-sport product.
_SPORT_KEYWORDS: list[tuple[Sport, tuple[str, ...]]] = [
    ("basketball", ("basketball", "nba", "wnba", "hoops")),
    ("hockey", ("hockey", "nhl", "pwhl")),
    ("baseball", ("baseball", "mlb", "bowman")),
    ("football", ("football", "nfl", "gridiron")),
]
_YEAR = re.compile(r"\b((?:19|20)\d{2})(?:-(?:\d{2}|\d{4}))?\b")


def guess_sport(text: str) -> Sport:
    """'2026 Topps Chrome Basketball' -> 'basketball'; 'Multi-Sport' -> 'multi'."""
    lowered = text.lower()
    if "multi-sport" in lowered or "multi sport" in lowered or "multisport" in lowered:
        return "multi"
    found = [sport for sport, words in _SPORT_KEYWORDS
             if any(re.search(rf"\b{w}\b", lowered) for w in words)]
    if len(found) > 1:
        return "multi"
    return found[0] if found else "other"


def guess_year(text: str) -> Optional[int]:
    """'2025-26 Upper Deck Series 1' -> 2025. First season-style year wins."""
    m = _YEAR.search(text)
    return int(m.group(1)) if m else None


@dataclass
class Release:
    manufacturer: str          # registry key: topps, panini, upperdeck, leaf, onyx, wildcard
    source_id: str             # stable id on the manufacturer's site (slug, product id)
    name: str
    sport: Sport = "other"
    year: Optional[int] = None
    release_date: Optional[str] = None   # ISO date YYYY-MM-DD when the site says
    product_type: Optional[str] = None   # "Hobby Box", "Blaster", ... free text
    url: Optional[str] = None
    image_url: Optional[str] = None
    checklist_url: Optional[str] = None  # xlsx/pdf/html the site links for this product
    description: Optional[str] = None
    raw: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class Card:
    card_number: str
    player: str
    team: Optional[str] = None
    subset: Optional[str] = None     # "Base", "Rated Rookies", "Young Guns"
    parallel: Optional[str] = None   # "Silver Prizm", "Red /99"
    print_run: Optional[int] = None
    is_rookie: bool = False
    is_auto: bool = False
    is_relic: bool = False
    raw: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return asdict(self)
