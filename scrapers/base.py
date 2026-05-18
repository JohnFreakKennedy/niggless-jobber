"""Abstract base class for all job board scrapers."""
from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


def random_ua() -> str:
    return random.choice(USER_AGENTS)


async def _retry_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    **kwargs: Any,
) -> httpx.Response:
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", delay * 4))
                jitter = random.uniform(0, 2)
                log.warning("Rate limited by %s; sleeping %.1fs", url, retry_after + jitter)
                await asyncio.sleep(retry_after + jitter)
                continue
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                log.debug("Request to %s failed (%s); retrying in %.1fs", url, exc, delay)
                await asyncio.sleep(delay + random.uniform(0, 1))
                delay *= 4
    raise last_exc or RuntimeError(f"All retries exhausted for {url}")


class BaseScraper(ABC):
    source: str = ""

    def __init__(self, keywords: list[str], locations: list[str], max_results: int = 50):
        self.keywords = keywords
        self.locations = locations
        self.max_results = max_results
        self._client: httpx.AsyncClient | None = None

    def _make_client(self, extra_headers: dict | None = None) -> httpx.AsyncClient:
        headers = {**DEFAULT_HEADERS, "User-Agent": random_ua()}
        if extra_headers:
            headers.update(extra_headers)
        return httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=30.0,
        )

    async def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None, "Client not initialised"
        return await _retry_request(self._client, "GET", url, **kwargs)

    @abstractmethod
    async def search(self) -> list[dict]:
        """Return a list of raw listing dicts."""

    async def run(self) -> list[dict]:
        async with self._make_client() as client:
            self._client = client
            try:
                results = await self.search()
            finally:
                self._client = None
        return results
