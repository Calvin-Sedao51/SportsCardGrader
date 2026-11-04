"""Upper Deck adapter: WooCommerce Store API listing + HTML checklist pages.

upperdeck.com is plain WordPress and answers a browser UA. The Store API
(``/wp-json/wc/store/v1/products``) is public JSON; ``checklist-sitemap.xml``
lists every ``/checklist/<slug>/`` page, each one ``table.csv-table`` with
columns like Set Name / Card / Description / Team City / Team Name / Rookie /
Auto / Mem / Tech / Serial #'d (names vary a little per page). Nothing on the
site links a product to its checklist, so they are paired by name: same season
year, every word of the product name in the slug, at most one extra word.
The site shows no release dates, so ``release_date`` stays None.
robots.txt asks for a 10 s crawl delay; a sync is 2 listing pages + 1 sitemap
+ 1 page per checklist.
"""
import html
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.sportsdb.models import Card, Release, Sport, guess_sport, guess_year

BASE = "https://upperdeck.com"
PRODUCTS_URL = f"{BASE}/wp-json/wc/store/v1/products"
SITEMAP_URL = f"{BASE}/checklist-sitemap.xml"
PER_PAGE = 100
MAX_PAGES = 20

SKIP_CATEGORIES = {"memorabilia", "hockey-memorabilia", "games", "prints", "apparel", "supplies"}
# Uncategorised listings are mostly signed memorabilia and framed prints (sized like 20x16).
_SKIP_NAME = re.compile(r"(?i)\bautographed\b|\bmemorabilia\b|\d{2}\s?[x×]\s?\d{2}\b")
_CATEGORY_SPORT: dict[str, Sport] = {"hockey": "hockey", "pwhl": "hockey", "basketball": "basketball",
                                     "cfl": "football", "ufl": "football"}
_PRODUCT_TYPE = re.compile(r"\(([^)]+)\)\s*$")          # "... (Hobby)", "... (Mass Blaster)"
_SHORT_SEASON = re.compile(r"^\s*(\d{2})-\d{2}\b")      # "24-25 DC Annual"
_SEASON = re.compile(r"\b(20\d{2})-(?:20)?(\d{2})\b")
_TAGS = re.compile(r"<[^>]+>")
_SITEMAP_LOC = re.compile(rf"<loc>\s*({re.escape(BASE)}/checklist/[^<\s]+)\s*</loc>")

# Words that differ freely between a product name and its checklist slug.
_NOISE = {"upper", "deck", "ud", "trading", "cards", "card", "checklist", "hockey", "nhl", "the", "of",
          "hobby", "retail", "blaster", "e", "pack", "epack", "mass", "box", "set", "edition", "final",
          "skybox"}
_ALIASES = {"one": "1", "two": "2", "juniors": "jrs", "jr": "jrs"}

# Checklist columns we read, by header keyword; earlier entries claim a header first.
_COLUMNS = (("set", ("set",)), ("number", ("card",)), ("player", ("description", "player")),
            ("city", ("city",)), ("team", ("team",)), ("rookie", ("rookie",)), ("auto", ("auto",)),
            ("mem", ("mem",)), ("serial", ("#'d", "serial")), ("tech", ("tech",)), ("sps", ("sp",)),
            ("odds", ("odds",)))
_ROOKIE = re.compile(r"(?i)rookie|\brc\b|xrc")
_AUTO = re.compile(r"(?i)\bauto(graph)?s?\b|\bsignature")
_RELIC = re.compile(r"(?i)\b(jsy|jersey|patch|puck|stick|glove|banner|net cord|letter|relic|memorabilia|material|swatch)")
_RELIC_SET = re.compile(r"(?i)\b(jersey|patch|relic|memorabilia|material)")


class UpperDeckSource:
    name = "upperdeck"

    def __init__(self) -> None:
        self._checklist_pages: Optional[list[str]] = None

    async def fetch_releases(self, fetcher) -> list[Release]:
        releases: list[Release] = []
        page, total_pages = 1, None     # X-WP-TotalPages when the API sends it
        while page <= min(total_pages or MAX_PAGES, MAX_PAGES):
            resp = await fetcher.get(f"{PRODUCTS_URL}?per_page={PER_PAGE}&page={page}&orderby=date")
            resp.raise_for_status()
            products = resp.json()
            header = resp.headers.get("x-wp-totalpages")
            total_pages = int(header) if header else None
            releases.extend(r for r in (self._release(p) for p in products) if r)
            if not products or (total_pages is None and len(products) < PER_PAGE):
                break
            page += 1
        # Pair checklists here, not in fetch_checklist: sync stores the release
        # first, so a URL found later would only land on the next day's run.
        try:
            pages = await self._checklists(fetcher)
        except httpx.HTTPError:          # the listing is still worth storing
            pages = []
        for rel in releases:
            rel.checklist_url = _match_checklist(rel.name, pages)
            if rel.sport == "other" and rel.checklist_url:
                rel.sport = guess_sport(_slug(rel.checklist_url).replace("-", " "))
            if rel.sport == "other" and "multi-sport" in (rel.description or "").lower():
                rel.sport = "multi"
        return releases

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        url = release.checklist_url or _match_checklist(release.name, await self._checklists(fetcher))
        if not url:
            return []
        resp = await fetcher.get(url)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        cards = _parse_checklist(resp.text)
        if cards:
            release.checklist_url = url
        return cards

    async def _checklists(self, fetcher) -> list[str]:
        if self._checklist_pages is None:
            resp = await fetcher.get(SITEMAP_URL)
            if resp.status_code != 404:
                resp.raise_for_status()
            self._checklist_pages = _SITEMAP_LOC.findall(resp.text)
        return self._checklist_pages

    def _release(self, product: dict) -> Optional[Release]:
        name = html.unescape(product["name"]).strip()
        categories = [c["slug"] for c in product.get("categories") or []]
        if set(categories) & SKIP_CATEGORIES or _SKIP_NAME.search(f"{name} {product.get('slug', '')}"):
            return None
        blurb = html.unescape(_TAGS.sub("", product.get("short_description") or "")).strip()
        sport = next((_CATEGORY_SPORT[c] for c in categories if c in _CATEGORY_SPORT), None) or guess_sport(name)
        kind = _PRODUCT_TYPE.search(name)
        images = product.get("images") or []
        return Release(manufacturer=self.name, source_id=str(product["id"]), name=name, sport=sport,
                       year=_year(name), product_type=kind.group(1) if kind else None,
                       url=product.get("permalink"), image_url=images[0]["src"] if images else None,
                       description=blurb or None,
                       raw={"slug": product.get("slug"), "categories": categories,
                            "tags": [t["slug"] for t in product.get("tags") or []]})


def _year(text: str) -> Optional[int]:
    year = guess_year(text)
    if year is None and (m := _SHORT_SEASON.match(text)):
        year = 2000 + int(m.group(1))
    return year


def _season(text: str) -> Optional[str]:
    m = _SEASON.search(text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _words(text: str) -> set[str]:
    text = re.sub(r"\([^)]*\)", " ", html.unescape(text).lower())
    text = re.sub(r"[’'‘]", "", text)
    words = (_ALIASES.get(w, w) for w in re.findall(r"[a-z0-9]+", text))
    return {w for w in words if w not in _NOISE and not re.fullmatch(r"\d{2,4}", w)}


def _match_checklist(name: str, urls: list[str]) -> Optional[str]:
    want = _words(name)
    if not want:
        return None
    year, season = _year(name), _season(name)
    best: Optional[tuple[tuple, str]] = None
    for url in urls:
        slug = _slug(url)
        if _year(slug) != year:
            continue
        have = _words(slug.replace("-", " "))
        extra = have - want
        if not want <= have or len(extra) > 1:
            continue
        key = (len(extra), season != _season(slug), slug)
        if best is None or key < best[0]:
            best = (key, url)
    return best[1] if best else None


def _slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _parse_checklist(page: str) -> list[Card]:
    table = BeautifulSoup(page, "lxml").find("table", class_="csv-table")
    if table is None:
        return []
    rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
    if len(rows) < 2:
        return []
    columns = _columns([h.lower() for h in rows[0]])
    if "number" not in columns or "player" not in columns:
        return []

    def cell(row: list[str], field: str) -> str:
        i = columns.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    data = [r for r in rows[1:] if cell(r, "number") and cell(r, "player")]
    set_names = {cell(r, "set") for r in data}
    inserts = sorted((s for s in set_names if " - " not in s and not s.lower().endswith("parallel")
                      and s.lower() not in ("base set", "base")), key=len, reverse=True)
    base_keys = {(cell(r, "number"), cell(r, "player")) for r in data if cell(r, "set").lower() == "base set"}
    cards = []
    for r in data:
        set_name, mem = cell(r, "set"), cell(r, "mem")
        subset, parallel = _split_set(set_name, inserts, (cell(r, "number"), cell(r, "player")) in base_keys)
        serial = cell(r, "serial")
        raw = {k: v for k in ("set", "mem", "tech", "sps", "odds") if (v := cell(r, k))}
        cards.append(Card(card_number=cell(r, "number"), player=cell(r, "player"),
                          team=" ".join(x for x in (cell(r, "city"), cell(r, "team")) if x) or None,
                          subset=subset, parallel=parallel,
                          print_run=int(serial) if serial.isdigit() else None,
                          is_rookie=bool(_ROOKIE.search(cell(r, "rookie"))),
                          is_auto=bool(cell(r, "auto")) or bool(_AUTO.search(set_name)),
                          is_relic=bool(_RELIC.search(mem)) or bool(_RELIC_SET.search(set_name)),
                          raw=raw))
    return cards


def _columns(header: list[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for field, keys in _COLUMNS:
        for key in keys:
            i = next((i for i, h in enumerate(header) if key in h and i not in columns.values()), None)
            if i is not None:
                columns[field] = i
                break
    return columns


def _split_set(set_name: str, inserts: list[str], is_base_card: bool) -> tuple[str, Optional[str]]:
    """'Green Foil Parallel - Young Guns' -> ('Young Guns', 'Green Foil');
    'Base Set' -> ('Base', None); 'Debut Dates Speckle Parallel' -> ('Debut Dates', 'Speckle')."""
    tier, _, sub = (s.strip() for s in set_name.partition(" - "))
    if tier.lower() in ("base set", "base"):
        return sub or "Base", None
    if not tier.lower().endswith(" parallel"):
        return set_name, None
    tier = tier[:-len(" parallel")].strip()
    if sub:
        return sub, tier
    for insert in inserts:
        if tier.lower().startswith(insert.lower() + " "):
            return insert, tier[len(insert):].strip()
    if is_base_card:
        return "Base", tier
    return set_name, None
