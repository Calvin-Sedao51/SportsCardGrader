import json

import httpx
import pytest

from app.hive.client import DEFAULT_NODES, HiveClient, HiveRpcError, HiveUnavailable

NODES = ["https://node-a.test", "https://node-b.test"]


def rpc_result(payload):
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": payload, "id": 1})


def rpc_error(message, code=-32602):
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": 1}
    )


def make_client(handler, **kwargs) -> HiveClient:
    return HiveClient(NODES, transport=httpx.MockTransport(handler), **kwargs)


async def test_call_returns_result_from_first_node():
    async def handler(request):
        assert request.url.host == "node-a.test"
        body = json.loads(request.content)
        assert body["method"] == "bridge.get_profile"
        return rpc_result({"name": "thebinder"})

    client = make_client(handler)
    assert await client.call("bridge", "get_profile", {"account": "thebinder"}) == {
        "name": "thebinder"
    }


async def test_call_fails_over_on_transport_error_and_5xx():
    calls = []

    async def handler(request):
        calls.append(request.url.host)
        if request.url.host == "node-a.test":
            raise httpx.ConnectError("boom")
        return rpc_result("ok")

    client = make_client(handler)
    assert await client.call("condenser_api", "get_dynamic_global_properties", []) == "ok"
    assert calls == ["node-a.test", "node-b.test"]
    # Last-good node is remembered: the next call starts at node-b.
    calls.clear()
    await client.call("condenser_api", "get_dynamic_global_properties", [])
    assert calls == ["node-b.test"]


async def test_call_raises_unavailable_when_all_nodes_fail():
    async def handler(request):
        return httpx.Response(502)

    client = make_client(handler)
    with pytest.raises(HiveUnavailable):
        await client.call("bridge", "get_post", {})


async def test_call_raises_rpc_error_without_rotating():
    calls = []

    async def handler(request):
        calls.append(request.url.host)
        return rpc_error("Invalid parameters")

    client = make_client(handler)
    with pytest.raises(HiveRpcError):
        await client.call("bridge", "get_post", {})
    assert calls == ["node-a.test"]


async def test_get_post_returns_post():
    async def handler(request):
        return rpc_result({"author": "thebinder", "permlink": "card-x", "json_metadata": {}})

    client = make_client(handler)
    post = await client.get_post("thebinder", "card-x")
    assert post["permlink"] == "card-x"


async def test_get_post_missing_is_none_only_after_alternate_node_confirms():
    calls = []

    async def handler(request):
        calls.append(request.url.host)
        return rpc_error("Post thebinder/card-x does not exist", code=-31999)

    client = make_client(handler)
    assert await client.get_post("thebinder", "card-x") is None
    # Sync-lag guard: a second, different node confirmed the miss.
    assert len(calls) == 2 and calls[0] != calls[1]


async def test_get_post_found_on_alternate_node():
    async def handler(request):
        if request.url.host == "node-a.test":
            return rpc_error("Post thebinder/card-x does not exist", code=-31999)
        return rpc_result({"author": "thebinder", "permlink": "card-x"})

    client = make_client(handler)
    post = await client.get_post("thebinder", "card-x")
    assert post is not None


async def test_get_rc_percent():
    async def handler(request):
        return rpc_result(
            {"rc_accounts": [{"account": "thebinder",
                              "rc_manabar": {"current_mana": "50"}, "max_rc": "200"}]}
        )

    client = make_client(handler, account="thebinder")
    assert await client.get_rc_percent() == 25.0


async def test_dry_run_broadcast_does_not_touch_network():
    async def handler(request):  # any network call fails the test
        raise AssertionError("network used in dry run")

    client = make_client(handler, account="thebinder", posting_key="5J...", dry_run=True)
    tx = await client.broadcast_ops([("comment", {"author": "thebinder"})])
    assert tx == "dry-run"


def test_default_nodes_are_https():
    assert len(DEFAULT_NODES) >= 3
    assert all(n.startswith("https://") for n in DEFAULT_NODES)


def test_default_nodes_exclude_dead_hosts():
    # anyx.io has answered 502 since 2026-10; a dead default only adds a
    # failover timeout to every read.
    assert "https://anyx.io" not in DEFAULT_NODES
