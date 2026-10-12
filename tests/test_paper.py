"""Paper account arithmetic, checked by hand-worked numbers.

    uv run python tests/test_paper.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cipherdeck.paper import Config, Paper, Quote, Skip  # noqa: E402


def close(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) < eps


def test_spot_round_trip() -> None:
    cfg = Config(balance=300, spot_fee=0.01, base_inc={"QNT": 0.001})
    a = Paper(cfg)
    p = a.open("QNT", "long", Quote(99, 100), 50, 0)
    # 300 / (100 * 1.01) = 2.970297 -> floored to 2.970
    assert close(p.qty, 2.970), p.qty
    assert close(a.cash, 300 - 297 - 2.97), a.cash
    assert close(p.stop, 97.0)
    # sell at the bid of 110: 326.7 proceeds, 3.267 fee
    t = a.close(Quote(110, 111), 1, "cross")
    assert close(a.cash, 0.03 + 326.7 - 3.267), a.cash
    assert close(t.pnl, 326.7 - 3.267 - 299.97), t.pnl
    assert close(a.fees, 2.97 + 3.267)
    assert a.position is None


def test_perp_short_and_skip() -> None:
    cfg = Config(balance=300)
    a = Paper(cfg)
    # 500 NEAR at 6.00 = $3,000 notional: $300 margin + $1.50 fee > $300 cash
    try:
        a.open("NEAR", "short", Quote(6.0, 6.01), 40, 0)
        raise AssertionError("should have skipped")
    except Skip as e:
        assert "needs $301.50" in str(e), e
    a = Paper(Config(balance=700, max_contracts=2))
    p = a.open("NEAR", "short", Quote(6.0, 6.01), 40, 0)
    assert p.contracts == 2 and p.qty == 1000 and close(p.margin, 600) and close(p.fee_in, 3.0)
    assert close(a.cash, 97.0)
    assert close(p.stop, 6.09)
    assert a.stop_hit(Quote(6.05, 6.06)) == ""
    assert a.stop_hit(Quote(6.09, 6.10)) == "stop"
    # buy back at the 5.80 ask: +200 gross, fee 1000*5.8*0.0005 = 2.90
    t = a.close(Quote(5.79, 5.80), 1, "cross")
    assert close(t.pnl, 200 - 2.9 - 3.0), t.pnl
    assert close(a.cash, 700 + t.pnl), a.cash


def test_shorts_only_on_perp_coins() -> None:
    a = Paper(Config(balance=5000))
    try:
        a.open("TAO", "short", Quote(300, 301), 10, 0)
        raise AssertionError("TAO has no perp")
    except Skip as e:
        assert "no Coinbase perp" in str(e)


def test_liquidation_floor_and_persist() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "paper.json"
        a = Paper(Config(balance=400, perp_stop=0.5), path)
        a.open("NEAR", "short", Quote(6.0, 6.0), 40, 0)
        # margin 300; +6% against = -180 -> 120 left, maintenance 5% of 3180 = 159 -> liquidated
        assert a.stop_hit(Quote(6.36, 6.36)) == "liquidated"
        b = Paper.load(Config(balance=400, perp_stop=0.5), path)
        assert b.position and b.position.contracts == 1 and close(b.cash, a.cash)
        t = b.close(Quote(9, 9), 1, "liquidated")
        assert close(t.pnl, -301.5), t.pnl          # lose the margin and the entry fee, no more
        assert Paper.load(Config(), path).position is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
