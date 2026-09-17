import base64
import time
from typing import Optional

import httpx
from pydantic import ValidationError

from app.comps import is_graded
from app.schemas import CardCategory, CompListing

_BASES = {"production": "https://api.ebay.com", "sandbox": "https://api.sandbox.ebay.com"}

# eBay Browse category ids. 212 = Sports Trading Cards; 183454 = CCG Individual
# Cards (Pokémon, Yu-Gi-Oh!, MTG, One Piece all live here). "other" gets no
# filter. Keep in sync with SACAT_BY_CATEGORY in web/src/screens/ResultsScreen.tsx.
EBAY_CATEGORY: dict[CardCategory, str] = {
    "sports": "212",
    "pokemon": "183454", "yugioh": "183454", "magic": "183454",
    "onepiece": "183454", "other_tcg": "183454",
}


class EbayClient:
    def __init__(self, client_id: str, client_secret: str, env: str = "production",
                 transport: Optional[httpx.BaseTransport] = None):
        self._base = _BASES[env]
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._token: Optional[str] = None
        self._token_expiry = 0.0
        self._http = httpx.AsyncClient(base_url=self._base, transport=transport, timeout=15)

    async def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry - 60:
            return self._token
        resp = await self._http.post(
            "/identity/v1/oauth2/token",
            headers={"Authorization": f"Basic {self._basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials",
                  "scope": "https://api.ebay.com/oauth/api_scope"},
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.monotonic() + payload.get("expires_in", 7200)
        return self._token

    # 200 is the Browse API's max page size. With sort=price we sample the
    # cheapest `limit` matches, so a bigger page keeps raw_median from being
    # the median of only the cheap tail. Residual bias remains for cards with
    # >200 active listings; the fix (two calls: Best Match for the median,
    # price-asc for the floor) is deliberately deferred.
    async def search(self, query: str, category: CardCategory = "sports",
                     limit: int = 200) -> list[CompListing]:
        token = await self._get_token()
        # sort=price → ascending by price (Browse API; "-price" would be
        # descending). Without it eBay returns Best Match ordering, and
        # the cheapest true comps may never appear in the first `limit`.
        params = {"q": query, "limit": limit, "sort": "price"}
        category_id = EBAY_CATEGORY.get(category)
        if category_id:
            params["category_ids"] = category_id
        resp = await self._http.get(
            "/buy/browse/v1/item_summary/search",
            headers={"Authorization": f"Bearer {token}",
                     "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},  # change for non-US deployments
            params=params,
        )
        resp.raise_for_status()
        items = resp.json().get("itemSummaries", [])
        listings: list[CompListing] = []
        for it in items:
            price = it.get("price", {}).get("value")
            if price is None:
                continue
            title = it.get("title", "")
            # A single malformed item (negative or non-numeric price) should be
            # skipped, not fail the whole search.
            try:
                listings.append(CompListing(title=title, price=float(price),
                                            graded=is_graded(title), url=it.get("itemWebUrl")))
            except (ValueError, TypeError, ValidationError):
                continue
        return listings
