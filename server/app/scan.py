"""Scan orchestration: vision -> (comps -> verdict || catalog), with partial results.

Vision-path errors propagate to the endpoint (mapped to HTTP statuses);
pricing/comps errors are swallowed into ScanResponse.comps_error, and catalog
errors into ScanResponse.catalog_error, so the user still gets the vision
analysis they paid an AI call for.
"""
import asyncio
from typing import Optional

from app.catalog import CatalogSource, apply_match, build_catalog_source
from app.comps import matching_grade_summary, summarize
from app.config import get_settings
from app.pricing import PricingSource, get_pricing_source
from app.schemas import (CardCategory, CatalogMatch, CompListing, Identity,
                         ScanResponse, VisionResult)
from app.verdict import decide, decide_slab
from app.vision.providers import analyze_card

_pricing_source: Optional[PricingSource] = None
# One adapter (and its HTTP connection pool) per category, built lazily.
_catalog_sources: dict[CardCategory, Optional[CatalogSource]] = {}


def _get_pricing_source() -> PricingSource:
    global _pricing_source
    if _pricing_source is None:
        _pricing_source = get_pricing_source(get_settings())
    return _pricing_source


async def run_vision(front: bytes, front_type: str, back: Optional[tuple[bytes, str]],
                     provider: str, api_key: str, model: str) -> VisionResult:
    return await analyze_card(front, front_type, back, provider=provider,
                              api_key=api_key, model=model)


async def search_comps(query: str, category: CardCategory = "sports") -> list[CompListing]:
    return await _get_pricing_source().search(query, category=category)


async def lookup_catalog(identity: Identity) -> Optional[CatalogMatch]:
    source = _catalog_sources.get(identity.category)
    if source is None:
        # Not cached when None: the local sports DB may get its first cards
        # (daily sync) while the server is running, and the check is cheap.
        source = build_catalog_source(identity.category, get_settings())
        if source is not None:
            _catalog_sources[identity.category] = source
    return await source.lookup(identity) if source is not None else None


async def enrich_identity(identity: Identity) -> tuple[Optional[CatalogMatch], Optional[str]]:
    """Best-effort catalog lookup: (match, None) or (None, catalog_error)."""
    try:
        return await lookup_catalog(identity), None
    except Exception as e:
        # httpx timeouts stringify to "", so the type name keeps it diagnosable.
        return None, f"Card catalog lookup failed: {type(e).__name__}: {e}".rstrip(": ")


async def perform_scan(front: bytes, front_type: str, back: Optional[tuple[bytes, str]],
                       provider: str, api_key: str, model: str,
                       asking_price: Optional[float]) -> ScanResponse:
    vision = await run_vision(front, front_type, back, provider, api_key, model)
    # A slabbed card legitimately has condition=null (the slab already graded
    # it), so condition is only required on the raw path.
    if (not vision.photo_ok or vision.identity is None
            or (vision.condition is None and vision.slab is None)):
        return ScanResponse(vision=vision)

    # Pricing searches the vision search_string, so it doesn't wait on the catalog.
    (comps, verdict, _listings, comps_error), (catalog, catalog_error) = await asyncio.gather(
        price_vision(vision, asking_price), enrich_identity(vision.identity))
    vision = vision.model_copy(update={"identity": apply_match(vision.identity, catalog)})
    return ScanResponse(vision=vision, comps=comps, comps_error=comps_error,
                        verdict=verdict, catalog=catalog, catalog_error=catalog_error)


async def price_vision(vision, asking_price: Optional[float]):
    """Comps + verdict for an already-identified card. Shared by the scan
    pipeline and the Binder comps-refresh path.

    Returns (comps, verdict, listings, comps_error); comps_error is None on
    success and carries the self-diagnosing message otherwise.
    """
    try:
        # Resolved inside the try so a misconfigured PRICING_SOURCE degrades to
        # a self-diagnosing comps_error instead of a 500.
        source_type = _get_pricing_source().source_type
        listings = await search_comps(vision.identity.search_string,
                                      category=vision.identity.category)
    except Exception as e:
        # httpx timeouts stringify to "", so the type name keeps it diagnosable.
        return None, None, [], str(e) or type(e).__name__

    # The query rides along so junk tokens that are part of the card's name
    # ("Greninja BREAK") are exempt from the junk filter.
    query = vision.identity.search_string
    if vision.slab is not None:
        # Slab path: the search string already carries company+grade (per the
        # vision prompt), so the listings skew toward same-grade slabs. Price
        # from same-grade comps when there are enough, else all graded comps.
        overall = summarize(listings, source=source_type, query=query)
        matching = matching_grade_summary(listings, vision.slab.company,
                                          vision.slab.grade, source=source_type,
                                          query=query)
        verdict = decide_slab(matching, overall, vision.slab, asking_price,
                              vision.identity.confidence,
                              authenticity=vision.authenticity)
        # The result carries the summary that actually priced the card.
        comps = matching if matching is not None else overall
        return comps, verdict, listings, None

    comps = summarize(listings, source=source_type, query=query)
    verdict = decide(comps, vision.condition, asking_price, vision.identity.confidence,
                     authenticity=vision.authenticity)
    return comps, verdict, listings, None
