import asyncio
from typing import get_args

import httpx
import pytest

from app.ebay import EBAY_CATEGORY, EbayClient
from app.schemas import CardCategory


def make_transport(token_calls: list | None = None, extra_items: list | None = None,
                   seen_params: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            if token_calls is not None:
                token_calls.append(1)
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        if request.url.path == "/buy/browse/v1/item_summary/search":
            if seen_params is not None:
                seen_params.append(dict(request.url.params))
            assert request.headers["Authorization"] == "Bearer tok"
            # Browse API: sort=price is ascending by price; without it eBay
            # returns Best Match, so the cheapest listings may not appear at all.
            assert request.url.params["sort"] == "price"
            # Max page size (200) so the median isn't computed from only the
            # cheapest tail of a high-volume card's listings.
            assert request.url.params["limit"] == "200"
            items = [
                {"title": "Luka PSA 10", "price": {"value": "400.00"},
                 "itemWebUrl": "https://ebay.com/itm/1"},
                {"title": "Luka raw RC", "price": {"value": "60.00"},
                 "itemWebUrl": "https://ebay.com/itm/2"},
            ] + (extra_items or [])
            return httpx.Response(200, json={"itemSummaries": items})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_search_returns_listings():
    client = EbayClient("id", "secret", "production", transport=make_transport())
    listings = await client.search("2018 Prizm Luka Doncic")
    assert len(listings) == 2
    assert listings[0].graded and listings[0].price == 400.0
    assert not listings[1].graded


async def test_search_raises_on_auth_failure():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_client"})
    client = EbayClient("id", "bad", "production", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search("anything")


async def test_token_cached_across_searches():
    token_calls: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(token_calls=token_calls))
    await client.search("first query")
    await client.search("second query")
    assert len(token_calls) == 1


def test_every_category_except_other_has_ebay_mapping():
    assert set(EBAY_CATEGORY) == set(get_args(CardCategory)) - {"other"}


async def test_search_default_category_is_sports():
    seen: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(seen_params=seen))
    await client.search("2018 Prizm Luka Doncic")
    assert seen[0]["category_ids"] == "212"


async def test_search_sports_uses_sports_category():
    seen: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(seen_params=seen))
    await client.search("2018 Prizm Luka Doncic", category="sports")
    assert seen[0]["category_ids"] == "212"


async def test_search_tcg_uses_ccg_category():
    seen: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(seen_params=seen))
    await client.search("1999 Pokemon Base Set Charizard", category="pokemon")
    assert seen[0]["category_ids"] == "183454"


async def test_search_other_omits_category_filter():
    seen: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(seen_params=seen))
    await client.search("mystery card", category="other")
    assert "category_ids" not in seen[0]


async def test_item_without_price_skipped():
    extra = [{"title": "Luka no price", "itemWebUrl": "https://ebay.com/itm/3"}]
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(extra_items=extra))
    listings = await client.search("2018 Prizm Luka Doncic")
    assert len(listings) == 2
    assert all(l.title != "Luka no price" for l in listings)


async def test_item_with_malformed_price_skipped():
    extra = [
        {"title": "Luka negative price", "price": {"value": "-5.00"},
         "itemWebUrl": "https://ebay.com/itm/4"},
        {"title": "Luka junk price", "price": {"value": "not-a-number"},
         "itemWebUrl": "https://ebay.com/itm/5"},
    ]
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(extra_items=extra))
    listings = await client.search("2018 Prizm Luka Doncic")
    assert [l.title for l in listings] == ["Luka PSA 10", "Luka raw RC"]


def make_401_transport(token_calls: list, search_auths: list, ok_after: int):
    """Search answers 401 for the first `ok_after` calls, then 200. Each token
    mint returns a distinct token so the retry's header proves a re-mint."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            token_calls.append(1)
            return httpx.Response(200, json={"access_token": f"tok{len(token_calls)}",
                                             "expires_in": 7200})
        if request.url.path == "/buy/browse/v1/item_summary/search":
            search_auths.append(request.headers["Authorization"])
            if len(search_auths) <= ok_after:
                return httpx.Response(401, json={"errors": [{"errorId": 1001}]})
            return httpx.Response(200, json={"itemSummaries": [
                {"title": "Luka raw RC", "price": {"value": "60.00"},
                 "itemWebUrl": "https://ebay.com/itm/2"}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_search_401_drops_cached_token_and_retries_once():
    token_calls: list = []
    search_auths: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_401_transport(token_calls, search_auths, ok_after=1))
    listings = await client.search("2018 Prizm Luka Doncic")
    assert len(listings) == 1
    assert len(token_calls) == 2
    assert search_auths == ["Bearer tok1", "Bearer tok2"]


async def test_search_401_after_token_refresh_raises():
    token_calls: list = []
    search_auths: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_401_transport(token_calls, search_auths, ok_after=2))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.search("2018 Prizm Luka Doncic")
    assert exc_info.value.response.status_code == 401
    assert len(token_calls) == 2 and len(search_auths) == 2  # exactly one retry


async def test_concurrent_searches_share_one_token_refresh():
    """Several scans landing while the token is expired/uncached must not
    each mint their own — one request should refresh, the rest should wait
    and reuse it."""
    token_calls: list = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            token_calls.append(1)
            # Widen the race window so concurrent callers would overlap here
            # without the lock.
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        if request.url.path == "/buy/browse/v1/item_summary/search":
            assert request.headers["Authorization"] == "Bearer tok"
            return httpx.Response(200, json={"itemSummaries": []})
        return httpx.Response(404)

    client = EbayClient("id", "secret", "production",
                        transport=httpx.MockTransport(handler))
    await asyncio.gather(*(client.search(f"query {i}") for i in range(5)))
    assert len(token_calls) == 1
