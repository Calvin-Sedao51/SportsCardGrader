"""Polite HTTP for scrapers: browser UA, robots.txt, per-host delay, retries.

Every adapter goes through one Fetcher so politeness is enforced in one
place. Tests pass an ``httpx.MockTransport`` and ``min_delay=0``.
"""
import asyncio
import time
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

# Topps and Panini sit behind Cloudflare and 403 the project's own UA; the
# sites that do answer treat a browser UA the same, so use one everywhere.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT_SECONDS = 30.0
RETRIES = 3
BACKOFF_SECONDS = 2.0
BLOCKED_STATUSES = {401, 403, 429, 503}


class Blocked(Exception):
    """The site refused us (Cloudflare, WAF, robots.txt). Run status: blocked."""


class Fetcher:
    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None,
                 min_delay: float = 2.0, respect_robots: bool = True):
        self._http = httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS,
                                       headers=DEFAULT_HEADERS, follow_redirects=True)
        self._min_delay = min_delay
        self._respect_robots = respect_robots
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self._delay: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    @staticmethod
    def _host(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    async def _robots_for(self, host: str) -> Optional[RobotFileParser]:
        if host in self._robots:
            return self._robots[host]
        parser: Optional[RobotFileParser] = None
        try:
            resp = await self._http.get(f"{host}/robots.txt")
            if resp.status_code == 200 and "text" in resp.headers.get("content-type", "text"):
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
        except httpx.HTTPError:
            parser = None  # unreachable robots.txt == no rules
        self._robots[host] = parser
        delay = parser.crawl_delay("*") if parser else None
        self._delay[host] = max(self._min_delay, float(delay or 0))
        return parser

    async def _wait_turn(self, host: str) -> None:
        delay = self._delay.get(host, self._min_delay)
        elapsed = time.monotonic() - self._last.get(host, 0.0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last[host] = time.monotonic()

    async def get(self, url: str, **kwargs) -> httpx.Response:
        host = self._host(url)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            if self._respect_robots:
                robots = await self._robots_for(host)
                if robots and not robots.can_fetch(USER_AGENT, url):
                    raise Blocked(f"robots.txt disallows {url}")
            last_exc: Optional[Exception] = None
            for attempt in range(RETRIES):
                await self._wait_turn(host)
                try:
                    resp = await self._http.get(url, **kwargs)
                except httpx.HTTPError as e:
                    last_exc = e
                    await asyncio.sleep(BACKOFF_SECONDS * (attempt + 1))
                    continue
                if resp.status_code in BLOCKED_STATUSES:
                    raise Blocked(f"HTTP {resp.status_code} for {url}")
                if resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request,
                                                     response=resp)
                    await asyncio.sleep(BACKOFF_SECONDS * (attempt + 1))
                    continue
                return resp
            raise last_exc or RuntimeError(f"no response for {url}")

    async def get_text(self, url: str, **kwargs) -> str:
        resp = await self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.text

    async def get_json(self, url: str, **kwargs):
        resp = await self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def get_bytes(self, url: str, **kwargs) -> bytes:
        resp = await self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.content
