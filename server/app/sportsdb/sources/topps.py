"""Topps adapter: official checklist hub + release calendar, fetched by headless Chromium.

Recon 2026-10-17 (project browser UA throughout):

- Plain HTTPS (curl and ``Fetcher``): 403 "Attention Required! | Cloudflare"
  for every path, including /robots.txt, /products.json,
  /collections/all/products.json and /collections/all.
- Playwright headless shell (``chromium_headless_shell``): same 403 page for
  every path, before and after a 20 s wait.
- Full Chromium (``channel="chromium"``) headless with its stock UA
  (``HeadlessChrome/156``): same 403 page. Headed: HTML pages 200.
- Full Chromium headless with ``HeadlessChrome`` -> ``Chrome`` in the UA: the
  first request in a fresh context always gets a 403 challenge page ("You
  have been blocked" / "Just a moment...", /cdn-cgi/challenge-platform) that
  sets the ``__cf_bm`` cookie; the page title never changes, but the same URL
  retried 3 s later returns 200, as do later requests in that context for /,
  /pages/*, /release-calendar, /sitemap.xml, /products/sitemap.xml,
  /collections/<handle> and /products/<handle>. ``*.json`` stays 403 in every
  mode (path-level WAF rule); the storefront is a Next.js app, so there is no
  Shopify ``products.json`` to begin with.
- robots.txt (read through the browser): ``Allow: /``, ``Disallow: /*?*``,
  ``/customer/``, ``/account/``. Only query-less topps.com URLs are fetched.

What the adapter reads:

- https://www.topps.com/pages/checklists -- 912 products (2018-2026) under
  ``<h2>`` year headings, each an ``<a>`` to its checklist file: 909 PDFs,
  3 legacy ``.xls`` (BIFF, unreadable by openpyxl), 7 of them hosted on
  www.topps.com/media/. That is the release listing. ``fetch_checklist``
  yields cards only for ``.xlsx``, which the hub did not have on 2026-10-17;
  PDFs and ``.xls`` keep ``checklist_url`` and return ``[]``.
- https://www.topps.com/release-calendar -- the next few weeks of drops
  (14 that day) embedded in the Next.js flight payload: ``releaseName``,
  ``dropDate`` (ISO), ``url`` (/pages/<slug>), ``card.image.url``,
  ``productCategory``. Merged into hub entries by name slug.
- Not used: /products/sitemap.xml (436 child sitemaps, ~108k URLs, mostly
  Topps NOW singles and supplies; ``lastmod`` is the crawl time, no names).

Checklist files sit on cdn.shopify.com, which answers plain HTTPS, so
``fetch_checklist`` goes through the fetcher and never opens the browser.
"""
import asyncio
import io
import json
import re
import time
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import openpyxl
from bs4 import BeautifulSoup

from app.sportsdb.http import BLOCKED_STATUSES, Blocked
from app.sportsdb.models import Card, Release, guess_sport, guess_year

HUB_URL = "https://www.topps.com/pages/checklists"
CALENDAR_URL = "https://www.topps.com/release-calendar"
BROWSER_DELAY = 2.0
RETRY_DELAY = 3.0
BLOCKED_MSG = ("topps.com: Cloudflare challenge blocks plain HTTP and headless Chromium; "
               "see module docstring")

_CHECKLIST_EXT = re.compile(r"\.(pdf|xlsx?|csv)$", re.I)
_NON_CARD = re.compile(r"\b(sleeves?|top ?loaders?|magnetic holders?|binders?|t-shirts?|hoodies?|"
                       r"apparel|digital|nft|gift cards?)\b", re.I)
_PRODUCT_TYPE = re.compile(r"\b(Hobby|Jumbo|Mega Box|Blaster|Hanger|Value Box|Sapphire Edition|"
                           r"Retail|Giant Box)\b", re.I)
_FLIGHT_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_CALENDAR_ITEM = re.compile(r'\{"card":\{"image"')
_PRINT_RUN = re.compile(r"(\d+)\s*$")

# xlsx header keywords -> Card field. Checklist exports vary between brands,
# so matching is by normalized header text, first hit wins.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "card_number": ("card #", "card#", "card no", "card no.", "card number", "number", "no.", "no", "#"),
    "player": ("player", "player name", "name", "subject", "athlete"),
    "team": ("team",),
    "subset": ("subset", "insert", "insert set", "set", "set name"),
    "parallel": ("parallel", "variation", "version"),
    "print_run": ("print run", "seq", "sequence", "numbered", "#'d", "qty"),
    "is_rookie": ("rc", "rookie"),
    "is_auto": ("auto", "autograph"),
    "is_relic": ("relic", "memorabilia"),
}


def _clean_name(text: str) -> str:
    return " ".join(re.sub(r"[®™©]", "", text).split())


def _slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def _product_type(name: str) -> Optional[str]:
    m = _PRODUCT_TYPE.search(name)
    return m.group(1).title() if m else None


def _sport(name: str):
    # guess_sport counts "Bowman" as baseball, which turns Bowman Basketball /
    # Bowman Football into "multi"; drop the brand word and ask again.
    sport = guess_sport(name)
    if sport == "multi" and re.search(r"\bbowman\b", name, re.I):
        return guess_sport(re.sub(r"\bbowman\b", "", name, flags=re.I))
    return sport


def _release(name: str, *, year: Optional[int] = None, **kw) -> Release:
    return Release(manufacturer="topps", source_id=_slug(name), name=name, sport=_sport(name),
                   year=guess_year(name) or year, product_type=_product_type(name), **kw)


def parse_hub(html: str) -> list[Release]:
    """Checklist hub -> one Release per checklist link, year from the name or its heading."""
    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, Release] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(HUB_URL, a["href"])
        if not _CHECKLIST_EXT.search(urlsplit(href).path):
            continue
        name = _clean_name(a.get_text(" "))
        if not name or _NON_CARD.search(name):
            continue
        heading = a.find_previous(["h2", "h3"])
        section = _clean_name(heading.get_text(" ")) if heading else ""
        rel = _release(name, year=guess_year(section), url=HUB_URL, checklist_url=href,
                       raw={"section": section, "checklist_file": urlsplit(href).path.rsplit("/", 1)[-1]})
        seen.setdefault(rel.source_id, rel)
    return list(seen.values())


def _flight_payload(html: str) -> str:
    # Next.js streams page data as JS string literals pushed in several chunks;
    # one item can straddle a chunk boundary, so decode and join before parsing.
    return "".join(json.loads(f'"{chunk}"') for chunk in _FLIGHT_CHUNK.findall(html))


def _json_object_at(text: str, start: int) -> Optional[str]:
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_calendar(html: str) -> list[Release]:
    """Release calendar page -> Releases with release_date, landing url and image."""
    payload = _flight_payload(html)
    out: list[Release] = []
    for m in _CALENDAR_ITEM.finditer(payload):
        obj = _json_object_at(payload, m.start())
        if obj is None:
            continue
        try:
            item = json.loads(obj)
        except ValueError:
            continue
        notify = item.get("notifyMe") or {}
        card = item.get("card") or {}
        name = _clean_name(notify.get("releaseName") or card.get("cardTitle") or "")
        if not name or _NON_CARD.search(name):
            continue
        drop = item.get("dropDate") or ""
        image = (card.get("image") or {}).get("url")
        out.append(_release(
            name,
            release_date=drop[:10] if re.match(r"\d{4}-\d{2}-\d{2}", drop) else None,
            url=urljoin(CALENDAR_URL, item["url"]) if item.get("url") else None,
            image_url=image if isinstance(image, str) and image.startswith("http") else None,
            raw={"dropDate": drop, "url": item.get("url"), "preorder": card.get("preorder"),
                 "productCategory": notify.get("productCategory"), "leadText": notify.get("leadText")}))
    return out


def _merge(hub: list[Release], calendar: list[Release]) -> list[Release]:
    by_id = {r.source_id: r for r in hub}
    merged: list[Release] = []
    for cal in calendar:
        rel = by_id.pop(cal.source_id, None)
        if rel is None:
            merged.append(cal)
            continue
        rel.release_date, rel.url, rel.image_url = cal.release_date, cal.url or rel.url, cal.image_url
        rel.raw.update(cal.raw)
        merged.append(rel)
    return merged + list(by_id.values())


def _header_map(row: list) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, cell in enumerate(row):
        text = " ".join(str(cell or "").lower().split())
        for field, words in _COLUMNS.items():
            if field not in cols and text in words:
                cols[field] = i
    return cols


def _cell(row: list, cols: dict[str, int], field: str) -> Optional[str]:
    i = cols.get(field)
    if i is None or i >= len(row) or row[i] is None:
        return None
    text = str(row[i]).strip()
    return text or None


def _flag(row: list, cols: dict[str, int], field: str) -> bool:
    value = _cell(row, cols, field)
    return bool(value) and value.lower() not in ("no", "n", "0", "false", "-")


def parse_checklist_xlsx(data: bytes) -> list[Card]:
    """Every sheet whose header row names a card number and a player column."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    cards: list[Card] = []
    for ws in wb.worksheets:
        cols: dict[str, int] = {}
        for row in ws.iter_rows(values_only=True):
            row = list(row)
            if not cols:
                found = _header_map(row)
                if "card_number" in found and "player" in found:
                    cols = found
                continue
            number, player = _cell(row, cols, "card_number"), _cell(row, cols, "player")
            if not number or not player:
                continue
            subset = _cell(row, cols, "subset") or (ws.title if "subset" not in cols else None)
            descriptors = " ".join(filter(None, (subset, _cell(row, cols, "parallel")))).lower()
            run = _cell(row, cols, "print_run")
            run_match = _PRINT_RUN.search(run) if run else None
            cards.append(Card(
                card_number=number, player=player, team=_cell(row, cols, "team"), subset=subset,
                parallel=_cell(row, cols, "parallel"),
                print_run=int(run_match.group(1)) if run_match else None,
                is_rookie=_flag(row, cols, "is_rookie") or "rookie" in descriptors,
                is_auto=_flag(row, cols, "is_auto") or "auto" in descriptors,
                is_relic=_flag(row, cols, "is_relic") or any(w in descriptors for w in ("relic", "memorabilia", "patch")),
                raw={"sheet": ws.title}))
    wb.close()
    return cards


class _Browser:
    """One headless full-Chromium context per run; Cloudflare's clearance cookie lives in it.

    The headless shell and the stock ``HeadlessChrome`` UA are hard-blocked;
    the full build with that token removed passes from the second request on.
    """

    def __init__(self, min_delay: float = BROWSER_DELAY):
        self._min_delay = min_delay
        self._last = 0.0

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=True, channel="chromium")
            probe = await self._browser.new_context()
            ua = await (await probe.new_page()).evaluate("navigator.userAgent")
            await probe.close()
            self._context = await self._browser.new_context(
                locale="en-US", user_agent=ua.replace("HeadlessChrome", "Chrome"))
            self._page = await self._context.new_page()
        except Exception:
            await self._pw.stop()
            raise
        return self

    async def __aexit__(self, *exc):
        await self._browser.close()
        await self._pw.stop()

    async def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self._min_delay:
            await asyncio.sleep(self._min_delay - elapsed)
        self._last = time.monotonic()

    async def get_text(self, url: str) -> str:
        status = None
        for attempt in range(2):  # the first navigation is the challenge; the retry passes
            await self._wait_turn()
            resp = await self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            status = resp.status if resp else None
            if status is not None and status < 400:
                return await resp.text()
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
        if status in BLOCKED_STATUSES or status is None:
            raise Blocked(f"HTTP {status} for {url}")
        raise RuntimeError(f"HTTP {status} for {url}")


class ToppsSource:
    name = "topps"

    def __init__(self, use_browser: bool = True, browser_factory: Callable[[], _Browser] = _Browser):
        self._use_browser = use_browser
        self._browser_factory = browser_factory

    async def fetch_releases(self, fetcher) -> list[Release]:
        try:
            hub = await fetcher.get_text(HUB_URL)
            calendar = await fetcher.get_text(CALENDAR_URL)
        except Blocked:
            if not self._use_browser:
                raise
            hub, calendar = await self._via_browser()
        return _merge(parse_hub(hub), parse_calendar(calendar))

    async def _via_browser(self) -> tuple[str, str]:
        try:
            async with self._browser_factory() as browser:
                return await browser.get_text(HUB_URL), await browser.get_text(CALENDAR_URL)
        except Exception as e:
            raise Blocked(f"{BLOCKED_MSG} ({e})") from e

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        url = release.checklist_url
        if not url or not urlsplit(url).path.lower().endswith(".xlsx"):
            return []    # PDFs and legacy .xls: checklist_url is kept, nothing to parse
        return parse_checklist_xlsx(await fetcher.get_bytes(url))
