"""Cliente asíncrono mínimo para la API pública /info de Hyperliquid."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class InfoClient:
    def __init__(self, url: str, concurrency: int = 4, timeout: float = 15.0):
        self._url = url
        self._http = httpx.AsyncClient(timeout=timeout)
        self._sem = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._http.aclose()

    async def info(self, payload: dict[str, Any], attempts: int = 5) -> Any:
        delay = 1.0
        last: Exception | None = None
        for _ in range(attempts):
            try:
                async with self._sem:
                    r = await self._http.post(self._url, json=payload)
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (429, 500, 502, 503, 504):
                    r.raise_for_status()
                last = RuntimeError(f"HTTP {r.status_code}")
            except httpx.HTTPError as e:
                last = e
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
        raise RuntimeError(f"info {payload.get('type')} falló: {last}")

    async def get_json(self, url: str, timeout: float = 60.0) -> Any:
        r = await self._http.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
