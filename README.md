# coinbase-portfolio-sync

Fetches my Coinbase portfolio and dumps it to a local SQLite database.
I got tired of clicking through the web UI every week to check positions,
so this runs from cron and keeps history.

Supports both live API calls (with your own key) and a dry-run mode
that replays a cached response so you can test formatting without
hitting rate limits.

## Install

```bash
pip install -r requirements.txt
```

Needs `COINBASE_API_KEY` and `COINBASE_API_SECRET` in your environment.

## Run

```bash
# live sync
python cb_sync.py

# dry run with embedded fixture data
python cb_sync.py --dry-run

# query last 5 snapshots
python cb_sync.py --history 5
```

The SQLite file defaults to `portfolio.db` in the working directory.
Override with `--db-path`.

## Example

```bash
$ python cb_sync.py --dry-run
2024-01-15T09:32:11Z  3 assets  total_usd=14231.07
  BTC  0.34000000  @42711.00  =14521.74
  ETH  1.20000000  @2234.50   =2681.40
  USDC 5000.00000000 @1.00    =5000.00
```

I use this with a weekly cron job and occasionally `sqlite3` to poke at trends.

<!-- updated: 2026-10-10 -->
