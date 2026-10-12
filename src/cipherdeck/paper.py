"""The paper account. Money moves the way it would on Coinbase: spot buys pay
the ask, sells get the bid, every fill pays the taker fee, and a NEAR short is
whole 500-NEAR perp contracts on margin. One position at a time.

Assumptions that are estimates, not read from Coinbase, live in Config with a
comment saying so. Funding payments on the perp are not modelled."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

COINS = ["QNT", "NEAR", "RENDER", "AERO", "TAO", "VIRTUAL"]
PERPS = {"NEAR": "NER-20DEC30-CDE"}     # the only one of the six with a US perp


@dataclass
class Config:
    balance: float = 300.0
    # Coinbase Advanced, lowest volume tier, market order. Sources disagree
    # between 0.6% and 1.2%; your tier is on coinbase.com/advanced-fees.
    spot_fee: float = 0.012
    # Coinbase US perps advertise taker fees "from 0.02%"; 0.05% leaves room.
    perp_fee: float = 0.0005
    perp_contract: float = 500.0        # NEAR per NEAR PERP contract (from the product listing)
    leverage: float = 10.0              # Coinbase's advertised max for US perps
    max_contracts: int = 1              # one 500-NEAR contract is already ~10x on $300
    maint_frac: float = 0.05            # estimate: liquidated below 5% of notional
    spot_stop: float = 0.03             # long stop, 3% under entry
    perp_stop: float = 0.015            # short stop, 1.5% over entry (~$44 on one contract)
    base_inc: dict[str, float] = field(default_factory=dict)   # spot size step per coin


class Skip(Exception):
    """A signal the account could not take, with the reason for the log."""


@dataclass
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class Position:
    coin: str
    side: str               # "long" or "short"
    venue: str              # "spot" or "perp"
    product: str
    qty: float              # coins (for the perp, contracts * contract size)
    contracts: int
    entry: float
    stop: float
    margin: float           # perp only: cash locked against the position
    cost: float             # what went out of cash on entry, fee included
    fee_in: float
    opened: float
    score: float


@dataclass
class Trade:
    coin: str
    side: str
    venue: str
    qty: float
    entry: float
    exit: float
    opened: float
    closed: float
    pnl: float              # after both fees
    fees: float
    reason: str


def _floor(x: float, step: float) -> float:
    if step <= 0:
        return x
    return math.floor(x / step + 1e-9) * step


class Paper:
    def __init__(self, cfg: Config, path: Path | None = None) -> None:
        self.cfg = cfg
        self.path = path
        self.start = cfg.balance
        self.cash = cfg.balance
        self.position: Position | None = None
        self.trades: list[Trade] = []
        self.fees = 0.0
        self.started = time.time()

    # ------------------------------------------------------------ orders --

    def open(self, coin: str, side: str, q: Quote, score: float, now: float) -> Position:
        if self.position:
            raise Skip(f"already in {self.position.coin}")
        if side == "long":
            pos = self._open_spot(coin, q, now)
        elif coin in PERPS:
            pos = self._open_perp(coin, q, now)
        else:
            raise Skip(f"{coin} has no Coinbase perp to short")
        pos.score = score
        self.position = pos
        self.fees += pos.fee_in
        self.save()
        return pos

    def _open_spot(self, coin: str, q: Quote, now: float) -> Position:
        f = self.cfg.spot_fee
        qty = _floor(self.cash / (q.ask * (1 + f)), self.cfg.base_inc.get(coin, 0))
        if qty * q.ask < 1:
            raise Skip(f"cash ${self.cash:.2f} is under Coinbase's $1 minimum")
        cost, fee = qty * q.ask, qty * q.ask * f
        self.cash -= cost + fee
        return Position(coin, "long", "spot", f"{coin}-USD", qty, 0, q.ask,
                        q.ask * (1 - self.cfg.spot_stop), 0.0, cost + fee, fee, now, 0.0)

    def _open_perp(self, coin: str, q: Quote, now: float) -> Position:
        c = self.cfg
        notional = c.perp_contract * q.bid
        need = notional / c.leverage + notional * c.perp_fee
        n = min(int(self.cash // need), c.max_contracts)
        if n < 1:
            raise Skip(f"one {coin} PERP contract ({c.perp_contract:.0f} {coin}, ${notional:,.0f}) "
                       f"needs ${need:,.2f} at {c.leverage:.0f}x, cash is ${self.cash:,.2f}")
        margin, fee = n * notional / c.leverage, n * notional * c.perp_fee
        self.cash -= margin + fee
        return Position(coin, "short", "perp", PERPS[coin], n * c.perp_contract, n, q.bid,
                        q.bid * (1 + c.perp_stop), margin, margin + fee, fee, now, 0.0)

    def close(self, q: Quote, now: float, reason: str) -> Trade:
        p = self.position
        assert p is not None
        if p.venue == "spot":
            proceeds = p.qty * q.bid
            fee = proceeds * self.cfg.spot_fee
            self.cash += proceeds - fee
            exit_px, pnl = q.bid, proceeds - fee - p.cost
        else:
            gross = p.qty * (p.entry - q.ask)
            fee = p.qty * q.ask * self.cfg.perp_fee
            back = p.margin + gross - fee
            if reason == "liquidated":
                back = max(back, 0.0)
            self.cash += back
            exit_px, pnl = q.ask, back - p.cost
        t = Trade(p.coin, p.side, p.venue, p.qty, p.entry, exit_px, p.opened, now, pnl,
                  p.fee_in + fee, reason)
        self.fees += fee
        self.trades.append(t)
        self.position = None
        self.save()
        return t

    # ----------------------------------------------------------- marking --

    def unrealized(self, q: Quote) -> float:
        """What closing right now would add to the account versus entry cost."""
        p = self.position
        if p is None:
            return 0.0
        if p.venue == "spot":
            return p.qty * q.bid * (1 - self.cfg.spot_fee) - p.cost
        return p.qty * (p.entry - q.ask) - p.qty * q.ask * self.cfg.perp_fee - p.fee_in

    def equity(self, q: Quote | None) -> float:
        p = self.position
        if p is None:
            return self.cash
        if q is None:
            return self.cash + p.cost
        return self.cash + p.cost + self.unrealized(q)

    def stop_hit(self, q: Quote) -> str:
        """'stop' or 'liquidated' if the quote has gone through it, else ''."""
        p = self.position
        if p is None:
            return ""
        if p.side == "long":
            return "stop" if q.bid <= p.stop else ""
        notional = p.qty * q.ask
        if p.margin + p.qty * (p.entry - q.ask) < self.cfg.maint_frac * notional:
            return "liquidated"
        return "stop" if q.ask >= p.stop else ""

    # ------------------------------------------------------- persistence --

    def to_dict(self) -> dict:
        return {"start": self.start, "cash": self.cash, "fees": self.fees,
                "started": self.started,
                "position": asdict(self.position) if self.position else None,
                "trades": [asdict(t) for t in self.trades]}

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1))
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, cfg: Config, path: Path) -> "Paper":
        acct = cls(cfg, path)
        if path.exists():
            d = json.loads(path.read_text())
            acct.start, acct.cash = d["start"], d["cash"]
            acct.fees, acct.started = d.get("fees", 0.0), d.get("started", time.time())
            acct.position = Position(**d["position"]) if d.get("position") else None
            acct.trades = [Trade(**t) for t in d.get("trades", [])]
        return acct
