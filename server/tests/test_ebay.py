import httpx
import pytest

from app.ebay import EbayClient


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
