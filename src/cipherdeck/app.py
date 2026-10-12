"""The screen and the loop. Prices are polled every few seconds for stops and
the P&L line; on every candle close the six coins are rescanned, an open
position exits on the opposite cross, and a flat account takes the best signal."""

from __future__ import annotations

import argparse
import math
import os
import time
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import RichLog, Static

from .coinbase import DemoMarket, Market
from .paper import COINS, PERPS, Config, Paper, Quote, Skip
from .strategy import Read, exit_now, pick, read_last

TF = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600}
GREEN, PINK, PURPLE, CYAN, DIM = "#5fd75f", "#ff5ea0", "#b07cf5", "#5ef1ff", "#7a7a7a"
FRESH = 120         # only trade a candle that closed this many seconds ago or less
SCAN_DELAY = 8      # Coinbase publishes a candle a few seconds after it closes


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "cipherdeck"


def money(x: float, sign: bool = False) -> str:
    s = f"{abs(x):,.2f}"
    if sign:
        return ("+$" if x >= 0 else "-$") + s
    return ("-$" if x < 0 else "$") + s


def px(x: float) -> str:
    if x <= 0 or math.isnan(x):
        return "-"
    d = 2 if x >= 100 else 3 if x >= 10 else 4 if x >= 1 else 5
    return f"{x:,.{d}f}"


def num(x: float, w: int = 6) -> str:
    return f"{'-':>{w}}" if math.isnan(x) else f"{x:>+{w}.1f}"


def hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def dur(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


class Panel(Static, can_focus=True):
    pass


class CipherDeck(App):
    CSS_PATH = "cipherdeck.tcss"
    TITLE = "cipherdeck"
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("space", "pause", "pause"),
        Binding("x", "close_now", "close position"),
        Binding("R", "reset", "reset account"),
        Binding("s", "scan_now", "scan now"),
        Binding("1", "focus('scanner')", show=False),
        Binding("2", "focus('position')", show=False),
        Binding("3", "focus('log')", show=False),
        Binding("4", "focus('trades')", show=False),
    ]

    def __init__(self, tf: str = "15m", cfg: Config | None = None, demo: bool = False,
                 reset: bool = False) -> None:
        super().__init__()
        self.demo = demo
        self.tf_name = tf
        self.tf = TF[tf]
        self.cfg = cfg or Config()
        self.market = DemoMarket() if demo else Market()
        d = data_dir()
        self.state_path = d / ("paper-demo.json" if demo else "paper.json")
        self.log_path = d / ("events-demo.log" if demo else "events.log")
        if reset or demo:
            self.state_path.unlink(missing_ok=True)
        self.acct = Paper.load(self.cfg, self.state_path)
        self.reads: dict[str, Read] = {}
        self.spot: dict[str, Quote] = {}
        self.perp: dict[str, Quote] = {}
        self.acted_t = 0            # newest candle already traded on
        self.next_scan = math.inf     # boot() runs the first scan
        self.paused = False
        self.feed = "starting"
        self.reset_armed = 0.0
        self.busy = False

    # ------------------------------------------------------------ layout --

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            with Horizontal(id="top"):
                yield Panel(id="scanner", classes="panel")
                yield Panel(id="position", classes="panel")
            with Horizontal(id="bottom"):
                yield RichLog(id="log", classes="panel", wrap=True, markup=False, max_lines=2000)
                yield Panel(id="trades", classes="panel")
        yield Static(id="bar")

    def on_mount(self) -> None:
        self.query_one("#scanner").border_title = "[1] Scanner"
        self.query_one("#position").border_title = "[2] Position"
        self.query_one("#log").border_title = "[3] Log"
        self.query_one("#trades").border_title = "[4] Trades"
        self.query_one("#log").focus()
        mode = "DEMO market" if self.demo else "Coinbase live prices"
        self.say(f"paper account {money(self.acct.equity(None))}, {self.tf_name} candles, {mode}", PURPLE)
        if self.acct.position:
            p = self.acct.position
            self.say(f"resumed open {p.side} {p.coin} from {hm(p.opened)}", PURPLE)
        self.boot()
        self.set_interval(1.0 if self.demo else 5.0, self.poll)
        self.set_interval(1.0, self.tick)

    @work(exclusive=True, group="boot")
    async def boot(self) -> None:
        try:
            self.cfg.base_inc.update(await self.market.base_increments(COINS))
        except Exception as e:     # sizes fall back to unrounded
            self.say(f"couldn't load size steps: {e}", PINK)
        await self.scan()

    # -------------------------------------------------------------- loop --

    def tick(self) -> None:
        if time.time() >= self.next_scan and not self.busy:
            self.run_scan()
        self.redraw()

    def schedule_next(self) -> None:
        if self.demo:
            self.next_scan = time.time() + 1
            return
        now = time.time()
        self.next_scan = (now // self.tf + 1) * self.tf + SCAN_DELAY

    @work(exclusive=True, group="scan")
    async def run_scan(self) -> None:
        await self.scan()

    async def scan(self) -> None:
        self.busy = True
        self.schedule_next()
        try:
            reads = {}
            for c in COINS:
                candles = await self.market.candles(c, self.tf, 250)
                if len(candles) > 80:
                    reads[c] = read_last(c, candles)
            self.reads = reads
            self.feed = "live"
        except Exception as e:
            self.feed = "error"
            self.say(f"scan failed: {e}", PINK)
            self.next_scan = time.time() + 20
            self.busy = False
            return
        try:
            await self.decide()
        except Exception as e:
            self.say(f"order step failed: {e}", PINK)
        self.busy = False

    async def decide(self) -> None:
        if not self.reads:
            return
        newest = max(r.t for r in self.reads.values())
        fresh = self.demo or time.time() - (newest + self.tf) <= FRESH
        sigs = [r for r in self.reads.values() if r.signal]
        if sigs:
            self.say("signals: " + ", ".join(f"{r.coin} {r.signal} {r.score:.0f}"
                                             for r in sorted(sigs, key=lambda r: -r.score)), DIM)
        if newest <= self.acted_t or not fresh:
            return
        self.acted_t = newest
        p = self.acct.position
        if p and (r := self.reads.get(p.coin)) and exit_now(p.side, r):
            q = await self.quote(p.coin, p.venue)
            self.closed(self.acct.close(q, time.time(), "cross"))
        if self.acct.position or self.paused:
            return
        best = pick(list(self.reads.values()), set(PERPS))
        if not best:
            return
        venue = "spot" if best.signal == "long" else "perp"
        q = await self.quote(best.coin, venue)
        try:
            pos = self.acct.open(best.coin, best.signal, q, best.score, time.time())
        except Skip as e:
            self.say(f"skipped {best.coin} {best.signal}: {e}", DIM)
            return
        what = f"{pos.contracts} contract{'s' * (pos.contracts > 1)} ({pos.qty:,.0f} {pos.coin})" \
            if pos.venue == "perp" else f"{pos.qty:,.4g} {pos.coin}"
        color = GREEN if pos.side == "long" else PINK
        self.say(f"OPEN {pos.side.upper()} {what} @ {px(pos.entry)} on {pos.venue}, "
                 f"stop {px(pos.stop)}, score {pos.score:.0f}, fee {money(pos.fee_in)}", color)

    async def quote(self, coin: str, venue: str) -> Quote:
        q = await (self.market.perp(coin) if venue == "perp" else self.market.spot(coin))
        (self.perp if venue == "perp" else self.spot)[coin] = q
        return q

    @work(exclusive=True, group="poll")
    async def poll(self) -> None:
        try:
            for c in COINS:
                self.spot[c] = await self.market.spot(c)
            for c in PERPS:
                self.perp[c] = await self.market.perp(c)
        except Exception as e:
            self.feed = "error"
            self.say(f"price poll failed: {e}", PINK)
            return
        p = self.acct.position
        if p:
            q = self.pos_quote()
            if q and (why := self.acct.stop_hit(q)):
                self.closed(self.acct.close(q, time.time(), why))

    def pos_quote(self) -> Quote | None:
        p = self.acct.position
        if not p:
            return None
        return (self.perp if p.venue == "perp" else self.spot).get(p.coin)

    def closed(self, t) -> None:
        color = GREEN if t.pnl > 0 else PINK
        self.say(f"CLOSE {t.side} {t.coin} @ {px(t.exit)} ({t.reason}) {money(t.pnl, True)} "
                 f"after fees {money(t.fees)}, held {dur(t.closed - t.opened)}", color)

    # ----------------------------------------------------------- actions --

    def action_pause(self) -> None:
        self.paused = not self.paused
        self.say("paused: no new entries (stops and exits still run)" if self.paused
                 else "resumed: taking new entries", PURPLE)

    @work(exclusive=True, group="manual")
    async def action_close_now(self) -> None:
        p = self.acct.position
        if not p:
            self.say("no open position", DIM)
            return
        q = await self.quote(p.coin, p.venue)
        self.closed(self.acct.close(q, time.time(), "manual"))

    def action_scan_now(self) -> None:
        if not self.busy:
            self.say("scanning now (trades only act on a fresh candle)", DIM)
            self.run_scan()

    def action_reset(self) -> None:
        now = time.time()
        if now - self.reset_armed > 4:
            self.reset_armed = now
            self.say("press R again within 4 seconds to wipe the paper account", PINK)
            return
        self.reset_armed = 0
        self.acct = Paper(self.cfg, self.state_path)
        self.acct.save()
        self.acted_t = 0
        self.say(f"paper account reset to {money(self.cfg.balance)}", PURPLE)

    def action_focus(self, wid: str) -> None:
        self.query_one(f"#{wid}").focus()

    async def on_unmount(self) -> None:
        await self.market.close()

    # ------------------------------------------------------------ output --

    def say(self, msg: str, color: str = "") -> None:
        ts = time.time()
        line = Text(datetime.fromtimestamp(ts).strftime("%H:%M:%S "), style=DIM)
        line.append(msg, style=color or None)
        try:
            self.query_one("#log", RichLog).write(line)
        except Exception:
            pass
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(f"{datetime.fromtimestamp(ts):%Y-%m-%d %H:%M:%S} {msg}\n")
        except OSError:
            pass

    def redraw(self) -> None:
        self.draw_scanner()
        self.draw_position()
        self.draw_trades()
        self.draw_bar()

    def draw_scanner(self) -> None:
        t = Text()
        t.append(f"{'coin':9}{'price':>11}{'wt1':>8}{'wt2':>8}{'flow':>8}  {'cross':7}{'signal'}\n", style=DIM)
        for c in COINS:
            r = self.reads.get(c)
            q = self.spot.get(c)
            held = self.acct.position and self.acct.position.coin == c
            name = c + (" ⇅" if c in PERPS else "")
            t.append(f"{name:9}", style=f"bold {CYAN}" if held else "bold")
            t.append(f"{px(q.mid) if q else (px(r.close) if r else '-'):>11}")
            if not r:
                t.append("   waiting for candles\n", style=DIM)
                continue
            t.append(num(r.wt1, 8), style=GREEN if r.wt1 >= r.wt2 else PINK)
            t.append(num(r.wt2, 8), style=DIM)
            t.append(num(r.mf, 8), style=GREEN if r.mf > 0 else PINK)
            t.append(f"  {r.last_cross or '-':7}", style=DIM)
            if r.signal == "long":
                t.append(f"LONG {r.score:.0f}", style=f"bold {GREEN}")
            elif r.signal == "short":
                ok = c in PERPS
                t.append(f"SHORT {r.score:.0f}" + ("" if ok else " (no perp)"),
                         style=f"bold {PINK}" if ok else DIM)
            elif r.dot:
                t.append(f"{r.dot} dot", style=GREEN if r.dot == "green" else PINK)
            t.append("\n")
        t.append("\nflow = money flow (green above 0)   cross = last wave cross, candles ago   ⇅ = can short",
                 style=DIM)
        w = self.query_one("#scanner", Panel)
        w.update(t)
        if self.reads:
            last = max(r.t for r in self.reads.values()) + self.tf
            w.border_subtitle = f"{self.tf_name} · closed {hm(last)}"

    def draw_position(self) -> None:
        a, p = self.acct, self.acct.position
        t = Text()
        q = self.pos_quote()
        if p:
            col = GREEN if p.side == "long" else PINK
            t.append(f"{p.side.upper()} {p.coin}", style=f"bold {col}")
            t.append(f"  {p.venue}" + (f" ×{p.contracts}" if p.venue == "perp" else "") + "\n", style=DIM)
            t.append(f"size    {p.qty:,.4g} {p.coin}\n")
            t.append(f"entry   {px(p.entry)}   {hm(p.opened)}\n")
            if q:
                now = q.bid if p.side == "long" else q.ask
                u = a.unrealized(q)
                t.append(f"now     {px(now)}   ")
                t.append(f"{(now / p.entry - 1) * 100:+.2f}%\n", style=GREEN if (now > p.entry) == (p.side == "long") else PINK)
                t.append("P&L     ")
                t.append(f"{money(u, True)}", style=f"bold {GREEN if u > 0 else PINK}")
                t.append("  if closed now\n", style=DIM)
            t.append(f"stop    {px(p.stop)}\n")
            if p.venue == "perp":
                t.append(f"margin  {money(p.margin)}  notional {money(p.qty * p.entry)}\n", style=DIM)
            t.append(f"held    {dur(time.time() - p.opened)}\n")
        else:
            t.append("flat", style="bold")
            t.append("  waiting for a signal\n\n", style=DIM)
        eq = a.equity(q)
        t.append("\n")
        t.append(f"equity  {money(eq)}  ", style="bold")
        ret = (eq / a.start - 1) * 100
        t.append(f"{ret:+.2f}%\n", style=GREEN if ret >= 0 else PINK)
        t.append(f"cash    {money(a.cash)}\n")
        wins = sum(1 for x in a.trades if x.pnl > 0)
        t.append(f"trades  {len(a.trades)}  ({wins} won)\n")
        t.append(f"fees    {money(a.fees)}\n", style=DIM)
        t.append(f"since   {datetime.fromtimestamp(a.started):%b %d %H:%M}\n", style=DIM)
        w = self.query_one("#position", Panel)
        w.update(t)
        w.border_subtitle = "PAUSED" if self.paused else ("paper" if not self.demo else "paper · demo")

    def draw_trades(self) -> None:
        t = Text()
        for x in reversed(self.acct.trades[-200:]):
            col = GREEN if x.pnl > 0 else PINK
            t.append(f"{hm(x.closed)} ", style=DIM)
            t.append(f"{x.coin:8}{x.side:6}")
            t.append(f"{money(x.pnl, True):>9}", style=col)
            t.append(f"  {x.reason}\n", style=DIM)
        if not self.acct.trades:
            t.append("no closed trades yet", style=DIM)
        w = self.query_one("#trades", Panel)
        w.update(t)
        total = sum(x.pnl for x in self.acct.trades)
        w.border_subtitle = f"{len(self.acct.trades)} · {money(total, True)}"

    def draw_bar(self) -> None:
        t = Text()
        t.append(" PAPER ", style=f"bold black on {PURPLE}")
        t.append(f" {self.tf_name} ", style=PURPLE)
        left = max(0, self.next_scan - time.time())
        when = "--:--" if math.isinf(left) else f"{int(left // 60):02d}:{int(left % 60):02d}"
        t.append(f" next scan {when} ", style=DIM)
        if self.paused:
            t.append(" PAUSED ", style=f"bold black on {PINK}")
        t.append("  pause: space | close: x | scan: s | reset: R R | panels: 1-4 | quit: q", style=DIM)
        dot = GREEN if self.feed == "live" else PINK
        t.append("   ● ", style=dot)
        t.append(self.feed if not self.demo else "demo", style=DIM)
        self.query_one("#bar", Static).update(t)


def run() -> None:
    ap = argparse.ArgumentParser(prog="cipherdeck", description="Cipher B paper trader for Coinbase")
    ap.add_argument("--tf", default="15m", choices=TF, help="candle size the signals use (default 15m)")
    ap.add_argument("--balance", type=float, default=300.0,
                    help="starting paper money for a new or reset account (default 300)")
    ap.add_argument("--spot-fee", type=float, default=0.012,
                    help="spot taker fee per fill, 0.012 = 1.2%% (check your Coinbase tier)")
    ap.add_argument("--reset", action="store_true", help="wipe the paper account first")
    ap.add_argument("--demo", action="store_true", help="made-up market, fast scans, no network")
    a = ap.parse_args()
    cfg = Config(balance=a.balance, spot_fee=a.spot_fee)
    CipherDeck(tf=a.tf, cfg=cfg, demo=a.demo, reset=a.reset).run()
