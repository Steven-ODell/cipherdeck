"""Coinbase public market data, no API key: spot candles and quotes from the
Exchange API, the NEAR perp's order book from the Advanced Trade market API.
A demo market with the same methods runs offline for tests and the --demo flag."""

from __future__ import annotations

import asyncio
import math
import random
import time

import httpx

from .cipher import Candle
from .paper import PERPS, Quote

EXCHANGE = "https://api.exchange.coinbase.com"
ADVANCED = "https://api.coinbase.com/api/v3/brokerage/market"
PAGE = 300
USER_AGENT = "cipherdeck/0.1 (paper trader)"


class Market:
    demo = False

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=12, headers={"User-Agent": USER_AGENT})
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, url: str, params: dict | None = None):
        """One request at a time, 120 ms apart: the public limit is ~10/s."""
        async with self._lock:
            for attempt in range(4):
                gap = time.monotonic() - self._last
                if gap < 0.12:
                    await asyncio.sleep(0.12 - gap)
                self._last = time.monotonic()
                try:
                    r = await self._http.get(url, params=params)
                except httpx.HTTPError:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(1 + attempt)
                    continue
                if r.status_code == 429 or r.status_code >= 500:
                    await asyncio.sleep(1 + attempt)
                    continue
                r.raise_for_status()
                return r.json()
            r.raise_for_status()

    async def candles(self, coin: str, seconds: int, count: int = PAGE) -> list[Candle]:
        """Closed candles only, oldest first. Coinbase skips empty buckets; those
        are filled flat at the previous close so the waves see continuous time."""
        now = int(time.time())
        current = now // seconds * seconds
        end = current
        got: dict[int, Candle] = {}
        while len(got) < count:
            start = end - seconds * PAGE
            rows = await self._get(f"{EXCHANGE}/products/{coin}-USD/candles",
                                   {"granularity": seconds, "start": start, "end": end})
            if not rows:
                break
            for t, lo, hi, op, cl, v in rows:
                if t < current:
                    got[t] = Candle(t, op, hi, lo, cl, v)
            end = start
        out: list[Candle] = []
        for t in sorted(got):
            if out:
                prev = out[-1]
                for k in range(1, (t - prev.t) // seconds):
                    out.append(Candle(prev.t + k * seconds, prev.c, prev.c, prev.c, prev.c, 0.0))
            out.append(got[t])
        return out[-count:]

    async def spot(self, coin: str) -> Quote:
        d = await self._get(f"{EXCHANGE}/products/{coin}-USD/ticker")
        return Quote(float(d["bid"]), float(d["ask"]))

    async def perp(self, coin: str) -> Quote:
        d = await self._get(f"{ADVANCED}/product_book", {"product_id": PERPS[coin], "limit": 1})
        book = d["pricebook"]
        return Quote(float(book["bids"][0]["price"]), float(book["asks"][0]["price"]))

    async def base_increments(self, coins: list[str]) -> dict[str, float]:
        out = {}
        for c in coins:
            d = await self._get(f"{EXCHANGE}/products/{c}-USD")
            out[c] = float(d.get("base_increment") or 0)
        return out


class DemoMarket:
    """Seeded random walks with regime flips, so the waves cross often and
    every path (long, short, stop, exit) shows up within a minute or two."""

    demo = True
    START = {"QNT": 72.0, "NEAR": 5.9, "RENDER": 2.4, "AERO": 0.82, "TAO": 310.0, "VIRTUAL": 1.1}

    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.px = dict(self.START)
        self.hist: dict[str, list[Candle]] = {}
        self.t0 = int(time.time())

    async def close(self) -> None:
        pass

    def _step(self, coin: str, seconds: int) -> None:
        h = self.hist.setdefault(coin, [])
        last = h[-1].c if h else self.px[coin]
        t = h[-1].t + seconds if h else self.t0 - 400 * seconds
        drift = 0.004 * math.sin(len(h) / 9 + sum(map(ord, coin)) % 7)
        o, cl = last, last * (1 + drift + self.rng.gauss(0, 0.012))
        hi = max(o, cl) * (1 + abs(self.rng.gauss(0, 0.004)))
        lo = min(o, cl) * (1 - abs(self.rng.gauss(0, 0.004)))
        h.append(Candle(t, o, hi, lo, cl, self.rng.uniform(1e3, 5e3)))
        self.px[coin] = cl

    async def candles(self, coin: str, seconds: int, count: int = PAGE) -> list[Candle]:
        h = self.hist.setdefault(coin, [])
        while len(h) < count:
            self._step(coin, seconds)
        self._step(coin, seconds)               # one new closed candle per scan
        return h[-count:]

    async def spot(self, coin: str) -> Quote:
        p = self.px[coin] * (1 + self.rng.gauss(0, 0.002))
        return Quote(p * 0.9995, p * 1.0005)

    async def perp(self, coin: str) -> Quote:
        p = self.px[coin] * (1 + self.rng.gauss(0, 0.002))
        return Quote(p * 0.9995, p * 1.0005)

    async def base_increments(self, coins: list[str]) -> dict[str, float]:
        return {c: 0.01 for c in coins}
