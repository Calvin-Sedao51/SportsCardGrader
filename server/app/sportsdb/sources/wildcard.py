"""Wild Card adapter: wildcardinc.com/checklists/ (static pages, embedded JSON).

The marketing site is a React SPA, but ``/checklists/`` and every
``/checklists/<line>__<slug>.html`` are self-contained static pages, so no
browser is needed. The index embeds ``const ITEMS = [...]`` (one entry per
product: file, title, product line, year, sport, release month) and each
product page embeds ``const C = {...}`` (rosters plus subsets, each a ladder
of foil boards x colour rows with print runs). Product pages run 0.2-9 MB
because card images are inlined as base64.

Wild Card has no integer card numbers. Each subset carries a code template
such as ``SDJW-‹INI›A‹clr›(01-10)`` where ‹INI› are the player's initials;
``card_number`` is that template with ‹INI› filled in and ``raw["code"]``
keeps the template. When two roster names share initials the site's own
disambiguation isn't published, so those get first-initial + surname.

One Card per subset x signed/unsigned variant x roster entry: the full
board/colour ladder would be ~3.5M rows site-wide and bury player searches,
so it lives in ``raw["parallels"]`` and ``print_run`` is set only when every
rung has the same run.
"""
import json
import re
from collections import Counter
from typing import Optional

from app.sportsdb.models import Card, Release, Sport, guess_sport, guess_year

BASE = "https://wildcardinc.com"
INDEX_URL = f"{BASE}/checklists/"
_SITE_SPORTS: dict[str, Sport] = {"Football": "football", "Baseball": "baseball", "Hockey": "hockey",
                                   "Basketball": "basketball", "Multi-Sport": "multi"}
_MONTHS = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July",
                                       "August", "September", "October", "November", "December"), 1)}
_PRODUCT_TYPES = ("Hobby Jumbo", "Hobby", "Jumbo", "Promo", "Brick", "Blaster", "Retail")
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_INI = "‹INI›"
_VARIANT_ROW_KEY = {"auto": "au", "base": "base"}


class WildCardSource:
    name = "wildcard"

    async def fetch_releases(self, fetcher) -> list[Release]:
        items = _embedded(await fetcher.get_text(INDEX_URL), "ITEMS")
        return [_release(item) for item in items if item.get("f")]

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        data = _embedded(await fetcher.get_text(release.checklist_url or release.url), "C")
        return _cards(data)


def _embedded(html: str, name: str):
    """The JSON literal assigned to ``const <name> = `` in the page's script."""
    m = re.search(rf"const {name}\s*=\s*", html)
    if not m:
        raise ValueError(f"no `const {name}` in page")
    value, _ = json.JSONDecoder().raw_decode(html, m.end())
    return value


def _release(item: dict) -> Release:
    name = (item.get("t") or "").strip()
    year_text = str(item.get("y") or "")
    year = int(year_text) if year_text.isdigit() else guess_year(name)
    month = _MONTHS.get(item.get("mo") or "")
    url = INDEX_URL + item["f"]
    return Release(
        manufacturer="wildcard",
        source_id=item["f"].removesuffix(".html"),
        name=name,
        sport=_SITE_SPORTS.get(item.get("s") or "") or guess_sport(name),
        year=year,
        # The site only shows "Release December 2025": month precision.
        release_date=f"{year}-{month:02d}-01" if year and month else None,
        product_type=_product_type(name),
        url=url,
        checklist_url=url,
        raw={"product_line": item.get("p"), "site_sport": item.get("s"),
             "release_month": item.get("mo"), "subsets": item.get("sub"),
             "boards": item.get("bd"), "players": item.get("pl")},
    )


def _product_type(name: str) -> Optional[str]:
    for kind in _PRODUCT_TYPES:
        if re.search(rf"\b{kind}\b", name, re.IGNORECASE):
            return kind
    return None


def _cards(data: dict) -> list[Card]:
    cards: list[Card] = []
    for subset in data.get("subsets") or []:
        for variant in ("auto", "base"):
            ladder = _ladder(subset, variant)
            if not ladder:
                continue
            runs = {run for rows in ladder.values() for run in rows.values()}
            print_run = runs.pop() if len(runs) == 1 else None
            template = (subset.get("codes") or {}).get(variant) or None
            code = template or _INI + ("A" if variant == "auto" else "")
            raw = {"code": template, "parallels": ladder}
            for player, initials in _roster_codes(data, subset, variant):
                number = code.replace(_INI, initials) if _INI in code else f"{code}-{initials}"
                cards.append(Card(number, player, subset=subset.get("name"), print_run=print_run,
                                  is_auto=variant == "auto", raw=raw))
    return cards


def _ladder(subset: dict, variant: str) -> dict[str, dict[str, int]]:
    """{board: {colour: print_run}} for the rungs that exist in this variant."""
    key = _VARIANT_ROW_KEY[variant]
    ladder = {}
    for group in subset.get("groups") or []:
        board = group.get("board") or ""
        rows = {row.get("p") or board: int(row[key]) for row in group.get("rows") or []
                if isinstance(row.get(key), (int, float))}
        if rows:
            ladder[board] = rows
    return ladder


def _roster_codes(data: dict, subset: dict, variant: str) -> list[tuple[str, str]]:
    """(player, initials) for every roster entry, initials made unique within the roster."""
    key = subset.get("roster")
    if variant == "auto" and subset.get("rosterAuto"):
        key = subset["rosterAuto"]
    if not key and subset.get("hit"):
        key = "triples" if "trip" in subset.get("name", "").lower() else "duals"
    # ``roster`` is usually the name of a top-level list, sometimes the list itself.
    entries = key if isinstance(key, list) else data.get(key or "subjects") or []
    names = [(e.get("name") if isinstance(e, dict) else e) or "" for e in entries]
    names = [n.strip() for n in names if n.strip()]
    codes = [_initials(n) for n in names]
    # Shared initials fall back to first-initial + surname, then to the whole
    # name: the DB keys cards by number, so two names must never share one.
    for longer in (_long_initials, lambda n: re.sub(r"[^A-Za-z0-9]", "", n).upper()):
        shared = {c for c, count in Counter(codes).items() if count > 1}
        codes = [longer(n) if c in shared else c for n, c in zip(names, codes)]
    return list(zip(names, codes))


def _name_words(component: str) -> tuple[list[str], str]:
    """(name words, numeral): rosters number repeat subjects ("Brett Favre 12")."""
    words = [w for w in component.split()
             if w[0].isalpha() and w.lower().rstrip(".") not in _NAME_SUFFIXES]
    digits = "".join(w for w in component.split() if w.isdigit())
    return words, digits


def _initials(name: str) -> str:
    """'Patrick Mahomes/Joe Burrow' -> 'PMJB' (duals share one code); 'Brett Favre 12' -> 'BF12'."""
    out = []
    for part in name.split("/"):
        words, digits = _name_words(part)
        out.append("".join(w[0] for w in words) + digits)
    return "".join(out).upper()


def _long_initials(name: str) -> str:
    """'Joe Burrow' -> 'JBURROW'."""
    out = []
    for part in name.split("/"):
        words, digits = _name_words(part)
        if len(words) > 1:
            out.append(words[0][0] + re.sub(r"[^A-Za-z]", "", words[-1]) + digits)
        elif words:
            out.append(words[0] + digits)
    return "".join(out).upper()
