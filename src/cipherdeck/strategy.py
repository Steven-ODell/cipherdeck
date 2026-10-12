"""The trading rules. The bot and scripts/backtest.py both call these, so a
backtest number is a number for exactly what the bot does.

    LONG   waves cross up below zero while money flow is green (mf > 0)
    SHORT  waves cross down above zero while money flow is red (mf < 0)
    EXIT   the opposite cross, or the stop

Several coins can signal on the same candle. Score = how far from zero the
waves crossed plus how strong the money flow is; the highest score wins."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .cipher import Candle, Waves, waves


@dataclass
class Read:
    """One coin at its last closed candle."""
    coin: str
    t: int
    close: float
    wt1: float
    wt2: float
    mf: float
    signal: str             # "long", "short" or ""
    score: float
    cross: str              # "up", "down" or "" on this candle
    dot: str                # "green", "red" or "" (the Cipher B dots, shown, not traded on)
    last_cross: str         # "up 3 bars ago" style hint for the scanner


def read_at(coin: str, candles: list[Candle], w: Waves, i: int) -> Read:
    wt1, wt2, mf = w.wt1[i], w.wt2[i], w.mf[i]
    up, down = w.cross_up(i), w.cross_down(i)
    signal, score = "", 0.0
    if not any(math.isnan(x) for x in (wt1, wt2, mf)):
        if up and wt2 < 0 and mf > 0:
            signal, score = "long", -wt2 + mf
        elif down and wt2 > 0 and mf < 0:
            signal, score = "short", wt2 - mf
    dot = "green" if w.green_dot(i) else "red" if w.red_dot(i) else ""
    last = ""
    for k in range(i, max(i - 60, 0), -1):
        if w.cross_up(k) or w.cross_down(k):
            last = f"{'up' if w.cross_up(k) else 'dn'} {i - k}"
            break
    return Read(coin, candles[i].t, candles[i].c, wt1, wt2, mf, signal, score,
                "up" if up else "down" if down else "", dot, last)


def read_last(coin: str, candles: list[Candle]) -> Read:
    """Read the newest candle in the list; the caller passes closed candles only."""
    return read_at(coin, candles, waves(candles), len(candles) - 1)


def exit_now(side: str, r: Read) -> bool:
    """Opposite cross closes the trade, wherever the waves are."""
    return (side == "long" and r.cross == "down") or (side == "short" and r.cross == "up")


def pick(reads: list[Read], can_short: set[str]) -> Read | None:
    """Best tradeable signal: longs on any coin, shorts only where a perp exists."""
    ok = [r for r in reads if r.signal == "long" or (r.signal == "short" and r.coin in can_short)]
    return max(ok, key=lambda r: r.score) if ok else None
