"""Pricing sources: pluggable market-data adapters for the scan pipeline.

The pipeline needs exactly two things from a pricing provider: comp listings
for a search string, and whether those prices are asks or actual solds — the
verdict wording and the web captions key off ``source_type``.

To plug in your own provider (a sold-data service, or your own exports):

1. Write a class with a ``source_type`` of ``"sold"`` or ``"active_listings"``
   and an ``async def search(self, query: str,
   category: CardCategory = "sports") -> list[CompListing]`` — ``category`` is
   the ``CardCategory`` detected by vision (sports, pokemon, ...); use it to
   scope provider results, or ignore it if your provider has no such notion.
   ``source_type`` must be one of those two literals — the verdict engine and
   the web UI only understand asks-vs-solds semantics. The constructor
   receives the app ``Settings``; read any provider credentials of your own
   from ``os.environ`` directly.
2. Register it: ``_REGISTRY["my_source"] = MySource``.
3. Set ``PRICING_SOURCE=my_source`` in your ``.env``.

The free default stays eBay active listings (``ebay_active``).
"""
from typing import Literal, Optional, Protocol

from app.config import Settings
from app.ebay import EbayClient
from app.schemas import CardCategory, CompListing


class PricingSource(Protocol):
    source_type: Literal["active_listings", "sold"]

    async def search(self, query: str,
                     category: CardCategory = "sports") -> list[CompListing]: ...


class EbayActiveSource:
    """Free default: eBay Browse API active listings (asking prices)."""

    source_type: Literal["active_listings", "sold"] = "active_listings"

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Optional[EbayClient] = None

    async def search(self, query: str,
                     category: CardCategory = "sports") -> list[CompListing]:
        # Checked per-search, not at construction, so an unconfigured server
        # still boots and scans still return vision results + comps_error.
        if not self._settings.ebay_configured:
            raise RuntimeError("eBay credentials not configured on this server")
        if self._client is None:
            self._client = EbayClient(self._settings.ebay_client_id,
                                      self._settings.ebay_client_secret,
                                      self._settings.ebay_env)
        return await self._client.search(query, category=category)


_REGISTRY: dict[str, type] = {"ebay_active": EbayActiveSource}


def get_pricing_source(settings: Settings) -> PricingSource:
    cls = _REGISTRY.get(settings.pricing_source)
    if cls is None:
        raise RuntimeError(
            f"Unknown PRICING_SOURCE {settings.pricing_source!r}; "
            f"valid names: {', '.join(sorted(_REGISTRY))}")
    return cls(settings)
