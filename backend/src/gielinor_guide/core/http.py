"""One shared HTTP client for every upstream API.

Everything that talks to Jagex or the Wiki goes through here, so the polite-API rules are
enforced in one place:

* a descriptive User-Agent on every request (the OSRS Wiki asks for this);
* a TTL cache, so repeated questions don't re-hit the APIs;
* a per-host minimum interval between requests (simple rate limiting);
* a few retries with backoff on 429 / 5xx, honouring Retry-After.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from gielinor_guide.core.config import Settings, load_settings
from gielinor_guide.core.errors import UpstreamError

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class TTLCache:
    """Tiny in-memory cache. Good enough for one process; swap for Redis when we deploy."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._clock = clock

    def get(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires, value = hit
        if self._clock() >= expires:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._data[key] = (self._clock() + ttl, value)

    def clear(self) -> None:
        self._data.clear()


class OSRSHttp:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        min_interval: float = 1.0,
        max_retries: int = 3,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.cache = TTLCache()
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._last_request: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self.settings.user_agent, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        ttl: float = 60.0,
        not_found: Callable[[], Exception] | None = None,
        not_found_statuses: frozenset[int] = frozenset({404}),
    ) -> Any:
        """GET a JSON document, served from cache when fresh.

        `not_found` lets callers turn a 404 (or any of `not_found_statuses`) into a meaningful
        error, e.g. an unknown player.
        """
        key = f"{url}?{urlencode(sorted((params or {}).items()))}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # One in-flight request per key: concurrent callers wait and then hit the cache.
        lock = self._key_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            data = await self._fetch(url, params, not_found, not_found_statuses)
            self.cache.set(key, data, ttl)
            return data

    async def _fetch(
        self,
        url: str,
        params: dict[str, str] | None,
        not_found: Callable[[], Exception] | None,
        not_found_statuses: frozenset[int],
    ) -> Any:
        host = urlsplit(url).netloc
        for attempt in range(self._max_retries + 1):
            await self._throttle(host)
            try:
                resp = await self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise UpstreamError(f"Couldn't reach {host}: {exc}") from exc
                await asyncio.sleep(2**attempt)
                continue

            if resp.status_code in not_found_statuses and not_found is not None:
                raise not_found()
            if resp.status_code in RETRY_STATUSES and attempt < self._max_retries:
                delay = _retry_after(resp) or 2**attempt
                log.warning("%s returned %s; retrying in %.1fs", host, resp.status_code, delay)
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise UpstreamError(f"{host} returned HTTP {resp.status_code} for {resp.url}")
            try:
                return resp.json()
            except ValueError as exc:
                raise UpstreamError(f"{host} returned a non-JSON response") from exc

        raise UpstreamError(f"Gave up on {host} after {self._max_retries + 1} attempts")

    async def _throttle(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_request.get(host, 0.0) + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request[host] = time.monotonic()


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return min(float(value), 30.0)
    except ValueError:
        return None
