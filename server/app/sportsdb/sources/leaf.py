"""Leaf adapter: Webflow site; each product page embeds one published Google
Sheet (CSV) that the page's own JS renders as the checklist.

Request budget per run: sitemap + /releases page 1 + one product page per
``/products/<slug>`` whose sitemap lastmod is within ``recent_days`` or that
the listing shows (~40 today), + one CSV per checklist the sync asks for.
Older products are represented from the sitemap URL alone so the DB still
knows they exist. ``/shopify-products/`` are shop SKUs (boxes, singles), not
releases, and are skipped.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.sportsdb.models import Card, Release, guess_sport, guess_year

HOST = "https://www.leaftradingcards.com"
_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_SHEET_URL = re.compile(r"SHEET_URL\s*=\s*'([^']*)'")
# The full release date is only in the meta description: "...text. Oct 07, 2026 Multi-Sport"
_META_DATE = re.compile(r"([A-Z][a-z]{2} \d{1,2}, \d{4})(?:\s+[\w-]+)?\s*$")
_PRODUCT_TYPE = re.compile(r"\b(hobby|blaster|jumbo|mega|retail|web exclusive)\b", re.I)
_ACRONYMS = {"nfl", "nba", "mlb", "nhl", "nll", "wnba", "pwhl", "usa", "ufc", "nft"}
_SMALL_WORDS = {"of", "the", "and", "in", "for", "a", "to"}
# Every other CSV column is a parallel whose cell is that parallel's print run.
_CARD_COLUMNS = {"Set", "Subset", "Checklist", "Foil", "Note", "MEM", "BASE"}


class LeafSource:
    name = "leaf"
    recent_days = 120   # product pages with an older sitemap lastmod are not refetched

    def __init__(self, today: Optional[date] = None):
        self._today = today

    async def fetch_releases(self, fetcher) -> list[Release]:
        sitemap = _parse_sitemap(await fetcher.get_bytes(f"{HOST}/sitemap.xml"))
        listed = _listing_slugs(await fetcher.get_text(f"{HOST}/releases"))
        cutoff = (self._today or date.today()) - timedelta(days=self.recent_days)
        slugs: dict[str, Optional[date]] = {slug: sitemap.get(slug) for slug in listed}
        slugs.update(sitemap)
        releases = []
        for slug, lastmod in slugs.items():
            if slug in listed or (lastmod and lastmod >= cutoff):
                rel = _parse_product(slug, await fetcher.get_text(f"{HOST}/products/{slug}"))
            else:
                rel = _release_from_slug(slug)
            if lastmod:
                rel.raw["lastmod"] = lastmod.isoformat()
            releases.append(rel)
        return releases

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        url = release.checklist_url
        if not url or "output=csv" not in url:   # only the published-sheet CSV is parseable
            return []
        text = (await fetcher.get_bytes(url)).decode("utf-8-sig")
        return _parse_checklist_csv(text, release.name)


def _parse_sitemap(xml: bytes) -> dict[str, Optional[date]]:
    """/products/<slug> -> lastmod date, in sitemap order."""
    out: dict[str, Optional[date]] = {}
    for url in ET.fromstring(xml).iter(f"{_NS}url"):
        path = urlsplit((url.findtext(f"{_NS}loc") or "").strip()).path
        if not path.startswith("/products/"):
            continue
        lastmod = (url.findtext(f"{_NS}lastmod") or "").strip()
        out[path.removeprefix("/products/")] = date.fromisoformat(lastmod[:10]) if lastmod else None
    return out


def _listing_slugs(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    slugs: dict[str, None] = {}
    for a in soup.select(".w-dyn-item a[href^='/products/']"):
        slugs.setdefault(a["href"].rsplit("/", 1)[1], None)
    return list(slugs)


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _parse_product(slug: str, html: str) -> Release:
    soup = BeautifulSoup(html, "lxml")
    name = _text(soup.select_one("h1.ds-h3")) or _name_from_slug(slug)
    labels = [_text(e) for e in soup.select(".ds-accent-div .ds-form-label")]  # ["Multi-Sport", "2026"]
    sport_label = labels[0] if labels else ""
    year = guess_year(name)
    if year is None and len(labels) > 1 and labels[1].isdigit():
        year = int(labels[1])
    meta = soup.find("meta", attrs={"name": "description"})
    m = _META_DATE.search(meta["content"]) if meta else None
    release_date = datetime.strptime(m.group(1), "%b %d, %Y").date().isoformat() if m else None
    og_image = soup.find("meta", property="og:image")
    sheet = _SHEET_URL.search(html)
    config = {_text(c.select_one(".ds-label-micro")): _text(c.select_one(".ds-config-number"))
              for c in soup.select(".ds-badge-config")}
    raw = {k: v for k, v in {
        "status": _text(soup.select_one(".ds-product-title .ds-badge")),
        "sport_label": sport_label,
        "config": config,
        "notable_signers": _text(soup.select_one(".product-celebrities")),
    }.items() if v}
    return Release(
        manufacturer="leaf", source_id=f"products/{slug}", name=name,
        sport=guess_sport(f"{name} {sport_label}"), year=year, release_date=release_date,
        product_type=_product_type(name), url=f"{HOST}/products/{slug}",
        image_url=og_image["content"] if og_image else None,
        checklist_url=(sheet.group(1) or None) if sheet else None,
        description=_text(soup.select_one(".ds-product-description-full")) or None, raw=raw)


def _release_from_slug(slug: str) -> Release:
    name = _name_from_slug(slug)
    return Release(manufacturer="leaf", source_id=f"products/{slug}", name=name,
                   sport=guess_sport(name), year=guess_year(name), product_type=_product_type(name),
                   url=f"{HOST}/products/{slug}")


def _name_from_slug(slug: str) -> str:
    """'2012-13-draft-basketball-retail' -> '2012-13 Draft Basketball Retail'."""
    words = slug.split("-")
    if len(words) > 1 and re.fullmatch(r"(?:19|20)\d{2}", words[0]) and re.fullmatch(r"\d{2}", words[1]):
        words[:2] = [f"{words[0]}-{words[1]}"]
    out = []
    for i, w in enumerate(words):
        if w in _ACRONYMS:
            out.append(w.upper())
        elif i and w in _SMALL_WORDS:
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _product_type(name: str) -> Optional[str]:
    m = _PRODUCT_TYPE.search(name)
    return m.group(1).title() if m else None


def _parse_checklist_csv(text: str, set_name: str) -> list[Card]:
    cards = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        # Mirrors the page's JS: rows whose Set isn't this product (incl. "*..." note rows) are not shown.
        if "Set" in row and row["Set"] != set_name.strip():
            continue
        # One sheet names the player column "Name_Oscar"; the site shows it as-is.
        player = next((v for k, v in row.items() if k.startswith("Name")), "")
        number = row.get("Checklist", "")
        if not number or not player:
            continue
        print_runs = {k: int(v) for k, v in row.items()
                      if k not in _CARD_COLUMNS and not k.startswith("Name") and v.isdigit()}
        raw = {k: v for k, v in {"type": row.get("BASE", ""), "note": row.get("Note", ""),
                                 "print_runs": print_runs}.items() if v}
        cards.append(Card(card_number=number, player=player, subset=row.get("Subset") or None,
                          parallel=row.get("Foil") or None,
                          is_auto=row.get("BASE", "").lower() == "auto",
                          is_relic=row.get("MEM", "").lower() == "yes", raw=raw))
    return cards
