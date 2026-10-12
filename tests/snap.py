"""Headless runs with screenshots. State goes to .scratch/, never your real account.

    uv run python tests/snap.py demo     # made-up market: waits for longs, a short, a stop
    uv run python tests/snap.py live     # real Coinbase prices, one scan, no waiting for trades
"""

import asyncio
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SCRATCH = PROJECT / ".scratch"
SHOTS = SCRATCH / "shots"
os.environ["XDG_DATA_HOME"] = str(SCRATCH / "data")
sys.path.insert(0, str(PROJECT / "src"))

from cipherdeck.app import CipherDeck  # noqa: E402
from cipherdeck.paper import Config    # noqa: E402


async def shot(app, pilot, name: str) -> None:
    await pilot.pause(0.3)
    SHOTS.mkdir(parents=True, exist_ok=True)
    app.save_screenshot(filename=f"{name}.svg", path=str(SHOTS))
    print("saved", SHOTS / f"{name}.svg")


async def wait_for(pilot, cond, seconds: float) -> bool:
    for _ in range(int(seconds / 0.2)):
        if cond():
            return True
        await pilot.pause(0.2)
    return cond()


async def demo() -> None:
    app = CipherDeck(cfg=Config(balance=1000), demo=True)
    async with app.run_test(size=(150, 42)) as pilot:
        assert await wait_for(pilot, lambda: len(app.reads) == 6, 10), "no reads"
        await shot(app, pilot, "demo-01-scan")
        assert await wait_for(pilot, lambda: app.acct.position is not None, 60), "never opened"
        await shot(app, pilot, "demo-02-open")
        seen: set[str] = set()

        def sides() -> set[str]:
            seen.update(t.side for t in app.acct.trades)
            if app.acct.position:
                seen.add(app.acct.position.side)
            return seen

        ok = await wait_for(pilot, lambda: {"long", "short"} <= sides() and len(app.acct.trades) >= 4, 300)
        reasons = {t.reason for t in app.acct.trades}
        print("trades", len(app.acct.trades), "sides", sides(), "exits", reasons,
              "equity", round(app.acct.equity(app.pos_quote()), 2))
        await shot(app, pilot, "demo-03-trades")
        assert ok, "did not see both a long and a short"
        # pause blocks entries; manual close works
        await pilot.press("space")
        assert app.paused
        if app.acct.position is None:
            await wait_for(pilot, lambda: app.acct.position is not None, 1)
        if app.acct.position is not None:
            n = len(app.acct.trades)
            await pilot.press("x")
            assert await wait_for(pilot, lambda: len(app.acct.trades) == n + 1, 5)
            assert app.acct.trades[-1].reason == "manual"
        await pilot.pause(8)
        assert app.acct.position is None, "opened while paused"
        await shot(app, pilot, "demo-04-paused")
        # reset needs two presses
        await pilot.press("R")
        assert app.acct.trades
        await pilot.press("R")
        assert not app.acct.trades and app.acct.cash == 1000
        # stops: tighten them so the price poll trips one
        app.cfg.spot_stop = app.cfg.perp_stop = 0.0015
        await pilot.press("space")
        assert not app.paused
        ok = await wait_for(pilot, lambda: any(t.reason == "stop" for t in app.acct.trades), 120)
        print("after stop test:", [(t.coin, t.side, t.reason, round(t.pnl, 2)) for t in app.acct.trades])
        await shot(app, pilot, "demo-05-stop")
        assert ok, "no stop exit"


async def live() -> None:
    app = CipherDeck()
    async with app.run_test(size=(150, 42)) as pilot:
        ok = await wait_for(pilot, lambda: len(app.reads) == 6 and len(app.spot) == 6 and app.perp, 60)
        await pilot.pause(1.5)
        await shot(app, pilot, "live-01")
        assert ok, f"feed={app.feed} reads={list(app.reads)} spot={list(app.spot)}"
        for c, r in app.reads.items():
            q = app.spot[c]
            print(f"{c:8} close {r.close:<10} bid {q.bid:<10} wt1 {r.wt1:+.1f} wt2 {r.wt2:+.1f} "
                  f"mf {r.mf:+.1f} {r.signal}")
        print("NEAR perp", app.perp["NEAR"], "next scan in", round(app.next_scan - __import__('time').time()))


asyncio.run(demo() if sys.argv[1:] == ["demo"] else live())
