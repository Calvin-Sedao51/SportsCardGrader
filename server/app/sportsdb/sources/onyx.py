"""Onyx adapter: WooCommerce Store API on onyxauthenticated.com.

Onyx sells boxes and single cards from one shop. The public Store API lists
every product, but a product's own ``categories`` is incomplete (the baseball
singles only carry the parent "trading-cards"), so sport also comes from the
per-category listings. Checklists are published as gallery images only, so
``fetch_checklist`` parses nothing today; the xlsx/HTML paths exist for when
a spreadsheet or table link shows up in a description.
"""
import html
import io
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit

import openpyxl
from bs4 import BeautifulSoup

from app.sportsdb.models import Card, Release, Sport, guess_sport, guess_year

HOST = "https://onyxauthenticated.com"
API = f"{HOST}/wp-json/wc/store/v1"
PER_PAGE = 100
MAX_PAGES = 20   # the store lists ~30 products; a runaway API must not loop forever

_NOT_A_SPORT = {"trading-cards", "collegiate-nil"}
_SPORT_SLUGS: dict[str, Sport] = {"baseball": "baseball", "basketball": "basketball",
                                  "football": "football", "hockey": "hockey", "multi-sport": "multi"}
_NON_CARD_CATEGORIES = {"supplies", "apparel", "accessories", "memorabilia", "redemptions"}
# Signed balls/jerseys/photos share the shop with cards; "card" in the name wins.
_MEMORABILIA = re.compile(r"\b(baseball|football|basketball|puck|jersey|photo|helmet|bat|ball)\b")
_SIGNED = re.compile(r"\b(autographed|signed)\b")
_PRODUCT_TYPES = ("Hobby Box", "Blaster Box", "Hanger Box", "Mega Box", "Retail Box",
                  "Case", "Box", "Pack")
_PRINT_RUN = re.compile(r"/\s*(\d+)\b")
_CHECKLIST_FILE = re.compile(r"\.(xlsx|xls|csv|pdf)(\?|$)", re.I)
_RELEASE_DATE = re.compile(r"release\s*date\s*[:\-]?\s*([A-Za-z]+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
                           re.I)
_DATE_FORMATS = ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y")

# Checklist column headers -> Card field, matched case-insensitively on the
# whole header text.
_COLUMNS = {
    "card_number": ("card #", "card no", "card no.", "card number", "#", "no", "no.", "number", "card"),
    "player": ("player", "player name", "name", "athlete", "subject"),
    "team": ("team", "club", "school"),
    "subset": ("set", "subset", "insert", "set name", "insert set", "series"),
    "parallel": ("parallel", "variation", "version"),
    "print_run": ("print run", "#'d", "numbered", "qty", "quantity", "seq", "/"),
}


class OnyxSource:
    name = "onyx"

    async def fetch_releases(self, fetcher) -> list[Release]:
        categories = await fetcher.get_json(f"{API}/products/categories?per_page=100")
        parent_id = next((c["id"] for c in categories if c["slug"] == "trading-cards"), None)
        sport_slugs = {c["id"]: c["slug"] for c in categories
                       if c["parent"] == parent_id and c["slug"] not in _NOT_A_SPORT}
        listed: dict[int, set[str]] = {}
        for cat_id, slug in sport_slugs.items():
            for product in await self._pages(fetcher, f"&category={cat_id}"):
                listed.setdefault(product["id"], set()).add(slug)
        releases = []
        for product in await self._pages(fetcher, ""):
            slugs = [c["slug"] for c in product.get("categories", [])]
            name = html.unescape(product["name"]).strip()
            if _is_non_card(name, slugs):
                continue
            description = _text(product.get("description", ""))
            releases.append(Release(
                manufacturer=self.name,
                source_id=str(product["id"]),
                name=name,
                sport=_sport(name, slugs, listed.get(product["id"], set())),
                year=guess_year(name),
                release_date=_release_date(description),
                product_type=_product_type(name, [t["slug"] for t in product.get("tags", [])]),
                url=product.get("permalink"),
                image_url=next((i["src"] for i in product.get("images", []) if i.get("src")), None),
                checklist_url=_checklist_url(product),
                description=description or None,
                raw={"id": product["id"], "slug": product.get("slug"), "type": product.get("type"),
                     "categories": slugs, "listed_under": sorted(listed.get(product["id"], ()))},
            ))
        return releases

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        url = release.checklist_url
        if not url or urlsplit(url).netloc != urlsplit(HOST).netloc:
            return []  # never scrape third-party hosts
        path = urlsplit(url).path.lower()
        if path.endswith(".xlsx"):
            return _cards_from_rows(_xlsx_rows(await fetcher.get_bytes(url)))
        if path.endswith((".html", ".htm", "/")) or "." not in path.rsplit("/", 1)[-1]:
            return _cards_from_rows(_html_rows(await fetcher.get_text(url)))
        return []  # pdf / image: nothing to parse without inventing data

    @staticmethod
    async def _pages(fetcher, query: str) -> list[dict]:
        products: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            batch = await fetcher.get_json(
                f"{API}/products?per_page={PER_PAGE}&page={page}&orderby=date{query}")
            products.extend(batch)
            if len(batch) < PER_PAGE:
                break
        return products


def _is_non_card(name: str, slugs: list[str]) -> bool:
    lowered = name.lower()
    if set(slugs) & _NON_CARD_CATEGORIES or "redemption" in lowered:
        return True
    return "card" not in lowered and bool(_SIGNED.search(lowered) and _MEMORABILIA.search(lowered))


def _sport_from_slugs(slugs) -> Optional[Sport]:
    sports = {_SPORT_SLUGS.get(s, "other") for s in slugs if s not in _NOT_A_SPORT}
    if not sports:
        return None
    return "multi" if len(sports) > 1 or "multi" in sports else sports.pop()


def _sport(name: str, own: list[str], listed: set[str]) -> Sport:
    guessed = guess_sport(name)
    return (_sport_from_slugs(own) or (guessed if guessed != "other" else None)
            or _sport_from_slugs(listed) or "other")


def _product_type(name: str, tags: list[str]) -> Optional[str]:
    lowered = name.lower()
    for kind in _PRODUCT_TYPES:
        if re.search(rf"\b{kind.lower()}\b", lowered):
            return kind
    if "single-cards" in tags or "card" in lowered or _PRINT_RUN.search(name):
        return "Single Card"
    return None


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(markup, "lxml").get_text(" ")).strip() if markup else ""


def _release_date(text: str) -> Optional[str]:
    m = _RELEASE_DATE.search(text)
    if not m:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(m.group(1), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _checklist_url(product: dict) -> Optional[str]:
    """A linked spreadsheet/PDF beats a link or gallery image named 'checklist'."""
    soup = BeautifulSoup(product.get("description", "") + product.get("short_description", ""), "lxml")
    links = [(a["href"], a.get_text(" ")) for a in soup.find_all("a", href=True)]
    for href, _ in links:
        if _CHECKLIST_FILE.search(href):
            return href
    for href, text in links:
        if "checklist" in (href + text).lower():
            return href
    for image in product.get("images", []):
        if "checklist" in " ".join(str(image.get(k, "")) for k in ("src", "name", "alt")).lower():
            return image["src"]
    return None


def _xlsx_rows(data: bytes) -> list[list[str]]:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    return [["" if v is None else str(v).strip() for v in row]
            for ws in wb.worksheets for row in ws.iter_rows(values_only=True)]


def _html_rows(markup: str) -> list[list[str]]:
    soup = BeautifulSoup(markup, "lxml")
    return [[c.get_text(" ").strip() for c in tr.find_all(["th", "td"])]
            for table in soup.find_all("table") for tr in table.find_all("tr")]


def _header_map(row: list[str]) -> Optional[dict[str, int]]:
    cols: dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = cell.lower().strip()
        for field, names in _COLUMNS.items():
            if key in names and field not in cols:
                cols[field] = idx
                break
    return cols if {"card_number", "player"} <= cols.keys() else None


def _cards_from_rows(rows: list[list[str]]) -> list[Card]:
    """Rows above the header (titles) and blank rows are skipped; a new header
    row re-maps columns so multi-sheet workbooks with varying layouts work."""
    cards: list[Card] = []
    cols: Optional[dict[str, int]] = None
    header: list[str] = []
    for row in rows:
        mapped = _header_map(row)
        if mapped:
            cols, header = mapped, row
            continue
        if not cols:
            continue
        get = lambda f: row[cols[f]].strip() if f in cols and cols[f] < len(row) else ""
        number, player = get("card_number"), get("player")
        if not number or not player:
            continue
        subset, parallel = get("subset") or None, get("parallel") or None
        flags = f"{subset or ''} {parallel or ''}".lower()
        run_text = get("print_run") or " ".join(x for x in (subset, parallel) if x)
        m = _PRINT_RUN.search(run_text) or re.fullmatch(r"\s*(\d+)\s*", run_text)
        cards.append(Card(
            card_number=re.sub(r"\.0$", "", number), player=player, team=get("team") or None,
            subset=subset, parallel=parallel, print_run=int(m.group(1)) if m else None,
            is_rookie=bool(re.search(r"\b(rc|rookie|rookies)\b", flags)),
            is_auto="auto" in flags, is_relic=bool(re.search(r"relic|patch|jersey|used", flags)),
            raw={h: (row[i] if i < len(row) else "") for i, h in enumerate(header) if h},
        ))
    return cards
