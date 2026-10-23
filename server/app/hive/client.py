"""Hive JSON-RPC client with node failover, plus broadcast via lighthive.

Reads are plain async JSON-RPC over httpx (no signing needed). Broadcasts go
through lighthive in a worker thread. Callers must never retry a broadcast
blindly — a duplicate hits the chain; verify on-chain first (see queue.py).
"""
import asyncio
import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_NODES = [
    "https://api.hive.blog",
    "https://api.deathwing.me",
    "https://api.openhive.network",
    "https://api.syncad.com",
    "https://rpc.mahdiyari.info",
]

RPC_TIMEOUT_SECONDS = 10.0


class HiveUnavailable(Exception):
    """All RPC nodes failed."""


class HiveRpcError(Exception):
    """A node returned a JSON-RPC error (request reached the node fine)."""

    def __init__(self, code: int, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HiveClient:
    def __init__(self, nodes: list[str], *, account: str = "", posting_key: str = "",
                 dry_run: bool = False, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.nodes = nodes
        self.account = account
        self._posting_key = posting_key
        self.dry_run = dry_run
        self._transport = transport
        self._good = 0  # index of last node that answered

    async def _post_node(self, node: str, payload: dict) -> Any:
        async with httpx.AsyncClient(transport=self._transport,
                                     timeout=RPC_TIMEOUT_SECONDS) as http:
            resp = await http.post(node, json=payload)
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(f"{resp.status_code} from {node}",
                                        request=resp.request, response=resp)
        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise HiveRpcError(err.get("code", 0), err.get("message", "unknown"))
        return body["result"]

    async def call(self, api: str, method: str, params: Any, *,
                   node: Optional[str] = None) -> Any:
        """JSON-RPC call with failover across nodes (transport/HTTP errors only).

        JSON-RPC errors are raised as HiveRpcError without rotating: the node
        understood the request, so other nodes would answer the same.
        """
        payload = {"jsonrpc": "2.0", "method": f"{api}.{method}", "params": params, "id": 1}
        if node is not None:
            return await self._post_node(node, payload)
        last_exc: Exception | None = None
        for i in range(len(self.nodes)):
            idx = (self._good + i) % len(self.nodes)
            try:
                result = await self._post_node(self.nodes[idx], payload)
                self._good = idx
                return result
            except HiveRpcError:
                self._good = idx
                raise
            except (httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError) as exc:
                logger.warning("hive node %s failed: %s", self.nodes[idx], exc)
                last_exc = exc
        raise HiveUnavailable(f"all {len(self.nodes)} hive nodes failed") from last_exc

    async def get_post(self, author: str, permlink: str) -> Optional[dict]:
        """bridge.get_post, or None if the post does not exist.

        A miss is confirmed on a second node before returning None — a single
        lagging Hivemind must never make the queue re-broadcast (duplicate post).
        """
        params = {"author": author, "permlink": permlink}
        try:
            return await self.call("bridge", "get_post", params)
        except HiveRpcError:
            pass
        alternate = self.nodes[(self._good + 1) % len(self.nodes)]
        try:
            return await self.call("bridge", "get_post", params, node=alternate)
        except HiveRpcError:
            return None

    async def get_ranked_posts(self, *, sort: str = "created", tag: str, limit: int = 20,
                               start_author: str = "", start_permlink: str = "") -> list[dict]:
        return await self.call("bridge", "get_ranked_posts", {
            "sort": sort, "tag": tag, "limit": limit,
            "start_author": start_author, "start_permlink": start_permlink,
        })

    async def get_rc_percent(self) -> float:
        result = await self.call("rc_api", "find_rc_accounts", {"accounts": [self.account]})
        accounts = result.get("rc_accounts", [])
        if not accounts:
            return 0.0
        acct = accounts[0]
        current = int(acct["rc_manabar"]["current_mana"])
        max_rc = int(acct["max_rc"])
        return (current / max_rc * 100.0) if max_rc else 0.0

    async def broadcast_ops(self, ops: list[tuple[str, dict]]) -> str:
        """Broadcast ops signed with the posting key. Called at most once per
        publish attempt (queue.py owns the verify-before-retry contract)."""
        if self.dry_run:
            logger.info("HIVE_DRY_RUN broadcast: %s", json.dumps(ops, default=str))
            return "dry-run"
        return await asyncio.to_thread(self._broadcast_sync, ops)

    def _broadcast_sync(self, ops: list[tuple[str, dict]]) -> str:
        from lighthive.client import Client as LightClient
        from lighthive.datastructures import Operation

        client = LightClient(nodes=self.nodes, keys=[self._posting_key])
        operations = [Operation(name, value) for name, value in ops]
        result = client.broadcast_sync(operations)
        return str(result.get("id", "")) if isinstance(result, dict) else str(result)
