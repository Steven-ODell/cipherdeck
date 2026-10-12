# cipherdeck

Paper trader for six Coinbase coins on Cipher B signals. Python 3.12, Textual,
httpx, run with uv. Read README.md first: it has the rules, the Coinbase facts
and the backtest numbers.

- `cipher.py` Cipher B math ported from VuManChu's Pine v4 (default inputs).
- `strategy.py` long/short/exit/pick rules. scripts/backtest.py uses the same
  functions; keep it that way so backtest numbers describe the bot.
- `paper.py` the account: fills at bid/ask, fees, NEAR perp contracts and margin.
- `coinbase.py` public market data (no key) plus DemoMarket for tests.
- `app.py` Textual UI and the loop (scan on candle close + 8 s, price poll 5 s).

Never place real orders without Sao asking for live mode in so many words.
Live mode does not exist yet. It would use a Coinbase Advanced Trade API key
with trade permission only, never withdraw.

Test with `tests/test_paper.py` and `tests/snap.py demo|live`. Both write only
to `.scratch/`. Never point a test at `~/.local/share/cipherdeck`, which is
his running paper account.

Installed as an editable uv tool (`cipherdeck`), listed in
`~/Omarchy-merge/install.sh` under "uv tools".
