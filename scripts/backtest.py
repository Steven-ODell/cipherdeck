"""Replay recent Coinbase candles through the bot's own rules and paper account.

    uv run python scripts/backtest.py                 # 30 days of 15m, $300, 1.2% spot fee
    uv run python scripts/backtest.py --days 60 --tf 1h --spot-fee 0.006

Decisions happen on each candle close and fill at that close plus half a
typical spread, which is what the live bot does a few seconds after the close.
Stops are checked against each candle's high and low first."""

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cipherdeck.cipher import waves                       # noqa: E402
from cipherdeck.coinbase import Market                    # noqa: E402
from cipherdeck.paper import COINS, Config, Paper, Quote, Skip  # noqa: E402
from cipherdeck.strategy import exit_now, pick, read_at   # noqa: E402

TF = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
HALF_SPREAD = 0.0005


def q_at(px: float) -> Quote:
    return Quote(px * (1 - HALF_SPREAD), px * (1 + HALF_SPREAD))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--tf", default="15m", choices=TF)
    ap.add_argument("--balance", type=float, default=300)
    ap.add_argument("--spot-fee", type=float, default=0.012)
    ap.add_argument("--no-shorts", action="store_true")
    a = ap.parse_args()

    sec = TF[a.tf]
    warm = 200
    count = a.days * 86400 // sec + warm
    m = Market()
    data = {}
    for c in COINS:
        data[c] = await m.candles(c, sec, count)
        print(f"{c:8} {len(data[c]):5} candles from {datetime.fromtimestamp(data[c][0].t, UTC):%Y-%m-%d %H:%M}")
    await m.close()

    w = {c: waves(data[c]) for c in COINS}
    idx = {c: {k.t: i for i, k in enumerate(data[c])} for c in COINS}
    times = sorted({k.t for c in COINS for k in data[c][warm:]})

    cfg = Config(balance=a.balance, spot_fee=a.spot_fee)
    acct = Paper(cfg)
    skips: Counter[str] = Counter()
    can_short = set() if a.no_shorts else {"NEAR"}

    for t in times:
        p = acct.position
        if p:
            i = idx[p.coin].get(t)
            if i is not None:
                k = data[p.coin][i]
                if p.side == "long" and k.l <= p.stop:
                    acct.close(q_at(min(k.o, p.stop) / (1 - HALF_SPREAD)), t, "stop")
                elif p.side == "short" and k.h >= p.stop:
                    acct.close(q_at(max(k.o, p.stop) / (1 + HALF_SPREAD)), t, "stop")
                elif exit_now(p.side, read_at(p.coin, data[p.coin], w[p.coin], i)):
                    acct.close(q_at(k.c), t, "cross")
        if acct.position:
            continue
        reads = [read_at(c, data[c], w[c], idx[c][t]) for c in COINS if t in idx[c]]
        best = pick(reads, can_short)
        if not best:
            continue
        try:
            acct.open(best.coin, best.signal, q_at(best.close), best.score, t)
        except Skip as e:
            skips[str(e).split(" (")[0]] += 1

    if acct.position:
        p = acct.position
        acct.close(q_at(data[p.coin][-1].c), times[-1], "end")

    tr = acct.trades
    wins = [x for x in tr if x.pnl > 0]
    print(f"\n{a.days}d of {a.tf}  start ${acct.start:.2f}  end ${acct.cash:.2f}  "
          f"({(acct.cash / acct.start - 1) * 100:+.1f}%)  fees ${acct.fees:.2f}")
    print(f"trades {len(tr)}  wins {len(wins)} ({len(wins) / max(len(tr), 1) * 100:.0f}%)  "
          f"per day {len(tr) / a.days:.1f}")
    by = Counter((x.coin, x.side) for x in tr)
    pnl = Counter()
    for x in tr:
        pnl[(x.coin, x.side)] += x.pnl
    for k, n in by.most_common():
        print(f"  {k[0]:8} {k[1]:5}  {n:3} trades  ${pnl[k]:+8.2f}")
    print("exits:", dict(Counter(x.reason for x in tr)))
    if skips:
        print("skipped:", dict(skips))
    for c in COINS:
        k = data[c]
        print(f"  hold {c:8} {(k[-1].c / k[warm].c - 1) * 100:+6.1f}%")


asyncio.run(main())
