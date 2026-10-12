"""Cipher B math, ported line for line from the VuManChu Pine v4 source
(default inputs). Plain Python on lists: a scan only ever looks at ~300 candles.

Every series is oldest first and the same length as the candles. Values that
need more history than exists are NaN, like Pine's na."""

from __future__ import annotations

import math
from dataclasses import dataclass

NAN = float("nan")

# WaveTrend: channel 9, average 12, MA 3, source hlc3. Oversold/overbought 53.
WT_CHANNEL = 9
WT_AVERAGE = 12
WT_MA = 3
OB_LEVEL = 53
OS_LEVEL = -53
# RSI+MFI area: period 60, multiplier 150, y position 2.5
MF_PERIOD = 60
MF_MULT = 150.0
MF_POS_Y = 2.5


@dataclass
class Candle:
    t: int          # bucket start, unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float


def ema(xs: list[float], length: int) -> list[float]:
    """Pine ema: seeded with the first value, alpha 2/(n+1)."""
    a = 2 / (length + 1)
    out, prev = [], NAN
    for x in xs:
        if math.isnan(x):
            out.append(prev)
            continue
        prev = x if math.isnan(prev) else a * x + (1 - a) * prev
        out.append(prev)
    return out


def sma(xs: list[float], length: int) -> list[float]:
    out = []
    for i in range(len(xs)):
        if i + 1 < length:
            out.append(NAN)
            continue
        win = xs[i + 1 - length:i + 1]
        out.append(NAN if any(math.isnan(x) for x in win) else sum(win) / length)
    return out


@dataclass
class Waves:
    wt1: list[float]
    wt2: list[float]
    mf: list[float]         # RSI+MFI area; above 0 is the green money flow

    def cross_up(self, i: int) -> bool:
        """wt1 crossed above wt2 on candle i (Pine cross() and wt2 - wt1 <= 0)."""
        if i < 1 or any(math.isnan(v) for v in (self.wt1[i], self.wt2[i], self.wt1[i-1], self.wt2[i-1])):
            return False
        return self.wt1[i] >= self.wt2[i] and self.wt1[i-1] < self.wt2[i-1]

    def cross_down(self, i: int) -> bool:
        if i < 1 or any(math.isnan(v) for v in (self.wt1[i], self.wt2[i], self.wt1[i-1], self.wt2[i-1])):
            return False
        return self.wt1[i] <= self.wt2[i] and self.wt1[i-1] > self.wt2[i-1]

    def green_dot(self, i: int) -> bool:
        return self.cross_up(i) and self.wt2[i] <= OS_LEVEL

    def red_dot(self, i: int) -> bool:
        return self.cross_down(i) and self.wt2[i] >= OB_LEVEL


def waves(candles: list[Candle]) -> Waves:
    src = [(c.h + c.l + c.c) / 3 for c in candles]
    esa = ema(src, WT_CHANNEL)
    de = ema([abs(s - e) for s, e in zip(src, esa)], WT_CHANNEL)
    ci = [(s - e) / (0.015 * d) if d else NAN for s, e, d in zip(src, esa, de)]
    wt1 = ema(ci, WT_AVERAGE)
    wt2 = sma(wt1, WT_MA)
    body = [((c.c - c.o) / (c.h - c.l) * MF_MULT) if c.h != c.l else 0.0 for c in candles]
    mf = [x - MF_POS_Y if not math.isnan(x) else NAN for x in sma(body, MF_PERIOD)]
    return Waves(wt1, wt2, mf)
