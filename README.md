# cipherdeck

A paper trader that watches six Coinbase coins (QNT, NEAR, RENDER, AERO, TAO,
VIRTUAL) with the Cipher B signals from VuManChu's open-source Market Cipher B
remake. Every candle close it rescans all six, picks the strongest signal, and
trades it with pretend money at real Coinbase bid/ask prices and fees.

It never places a real order. No API key, no account.

```
╭─[1] Scanner ───────────────────────────────────────╮╭─[2] Position ─────────────╮
│ coin         price     wt1     wt2    flow  cross  ││ LONG RENDER  spot         │
│ QNT         238.25   -30.7   -38.0    -0.3  up 10  ││ size    480.2 RENDER      │
│ NEAR ⇅      5.8422   +48.9   +53.1    +6.1  dn 1   ││ entry   2.0029   17:33    │
│ RENDER      1.9953   +14.3   +17.1    +7.8  dn 0   ││ P&L     -$24.03 if closed │
│ AERO       0.93948   +12.1   +13.0   -19.6  dn 0  SHORT 33 (no perp)            │
╰──────────────────────────────── 15m · closed 17:30 ╯│ equity  $300.00  +0.00%   │
╭─[3] Log ───────────────────────────────────────────╮╭─[4] Trades ───────────────╮
│ 17:33:37 OPEN LONG 480.2 RENDER @ 2.0029 on spot   ││ 17:33 NEAR  long  -$26.66 │
```

## Run it

```sh
cipherdeck                      # 15m candles, $300 paper account, 1.2% spot fee
cipherdeck --tf 1h              # slower: about 2 trades a day instead of 7
cipherdeck --balance 500 --reset
cipherdeck --spot-fee 0.006     # if your Coinbase tier is 0.6% taker
cipherdeck --demo               # made-up market, a scan every second, no network
```

From the repo without installing: `uv run cipherdeck`. Installed as an
editable uv tool, so edits here apply without reinstalling.

The paper account lives in `~/.local/share/cipherdeck/paper.json` and every
event is appended to `events.log` next to it. Quit and start again and it
picks up where it left off, open position included. `--demo` uses its own
files and starts fresh every time.

## Keys

```
space    pause new entries (stops and exits keep running)
x        close the open position now at market
s        rescan now (only a candle that closed in the last 2 minutes can trade)
R R      wipe the paper account back to the starting balance
1-4      focus a panel; the log scrolls with the mouse wheel when focused
q        quit (the account is saved after every fill)
```

## The rules

`src/cipherdeck/strategy.py` is the whole strategy, and the backtest calls
the same functions.

- **Long**: the waves cross up below zero while money flow is green.
- **Short**: the waves cross down above zero while money flow is red. NEAR only.
- **Exit**: the opposite wave cross, or the stop.
- **Stops**: 3% under entry for spot longs, 1.5% over entry for the NEAR short.
- **Pick**: if several coins signal on the same candle, the highest score wins.
  The score is how far from zero the waves crossed plus the money flow strength.
- One position at a time. Spot longs use all the cash.

The scanner also shows the classic Cipher B green and red dots, but nothing
trades on them.

## What Coinbase allows (checked 2026-10-11)

- All six coins trade on Coinbase spot, so any of them can be bought.
- Of the six, only NEAR has a US perp, `NER-20DEC30-CDE` ("NEAR PERP"), so
  NEAR is the only coin that can be shorted. Scanner rows marked `⇅` can short,
  and others show `SHORT (no perp)` greyed out.
- One NEAR PERP contract is 500 NEAR, about $2,950 at $5.90. At the advertised
  10x max leverage, that's about $296 of margin. A $300 account can open
  exactly one contract, and only while it has not lost money. Once it has,
  short signals are skipped and the log says why.
- One contract on $300 is about 10x leverage: a 1% move in NEAR is about $30.
  The bot never opens more than one contract (`max_contracts` in `paper.py`).
- A real short needs a Coinbase Financial Markets derivatives account, which
  is a separate sign-up from spot.

## Estimates, not read from Coinbase

These are in `Config` in `paper.py`:

- Spot taker fee 1.2%, the lowest volume tier. Some sources say 0.6%. Your
  real tier is on your Coinbase fee page.
- Perp taker fee 0.05%. Coinbase advertises "from 0.02%".
- Liquidation when the short's margin falls under 5% of notional.
- Funding payments on the perp are not modelled.

## Backtest

```sh
uv run python scripts/backtest.py                       # 30 days of 15m
uv run python scripts/backtest.py --tf 1h --days 90 --spot-fee 0.006
```

Results on 2026-10-11, $300 start:

| candles | fee | trades | end |
|---|---|---|---|
| 15m, 30 days | 1.2% | 215 | $2 |
| 15m, 30 days | 0.6% | 219 | $23 |
| 15m, 30 days | 0.05% (fees nearly gone) | 222 | $138 |
| 1h, 90 days | 1.2% | 143 | $10 |
| 6h, 365 days | 1.2% | 69 | $44 |

Holding any of the six over the same 30 days made between +18% and +275%.
These rules lose money even with fees taken out, and fees make it much worse.
At 7 trades a day, the 15m default pays about 17% of the account in fees each
day. Treat the paper run as a way to watch the signals work, not as proof
they make money.

## Tests

```sh
uv run python tests/test_paper.py      # account arithmetic against hand-worked numbers
uv run python tests/snap.py demo       # headless: long, short, cross exit, stop, manual close, pause, reset
uv run python tests/snap.py live       # headless against real Coinbase prices, one scan
```

Screenshots and test state go to `.scratch/`, never your real account.

## Credits

The indicator math is VuManChu's Cipher B + Divergences (MPL-2.0), which built
on LazyBear's WaveTrend and others. See `cipher.py`.
