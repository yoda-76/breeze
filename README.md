# Data pipeline: raw JSON -> validated -> processed Parquet -> features -> backtest

```
Data
  |
  v
Validate
  |
  v
Processed OHLCV (Parquet)
  |
  v
VWAP (derived feature)
  |
  v
Strategy -> Trades -> P&L -> Statistics
```

Matches the raw JSON your existing `data_fetch_script.py` writes to
`data/raw/breeze/<SYMBOL>/1m/<YYYY>/<MM>/<YYYY-MM-DD>.json`.

```
data_pipeline/
├── config.py                     <- shared paths/constants
├── validate_raw_data.py
├── build_processed_dataset.py
├── data_loader.py
├── run_pipeline.py                <- validate -> build -> build features, all in one
├── features/
│   ├── __init__.py
│   └── vwap.py                    <- VWAP formula + feature builder
└── backtest/
    ├── main.py                    <- common engine: data -> trades -> P&L -> stats
    ├── config.py                  <- which strategy + symbol/date range to run
    └── vwap_crossover/            <- one folder per strategy
        ├── strategy.py            <- strategy-specific logic only
        └── reports/                <- trades.csv, equity_curve.csv, stats.csv

data/
├── raw/                            <- untouched, read-only from here on
│   └── breeze/
│       └── RELIND/
│           └── 1m/
│               └── 2026/01/2026-01-01.json ...
├── processed/
│   ├── parquet/                    <- canonical OHLCV, never touched by features
│   │   └── RELIND/1m/2026-01.parquet
│   └── features/
│       └── vwap/                   <- derived, separate tree
│           └── RELIND/1m/2026-01.parquet
└── validation_report_RELIND.json   <- written by validate_raw_data.py
```

## Requirements

```
pip install pandas pyarrow
```

## 1. Validate

```bash
python validate_raw_data.py --symbol RELIND --interval 1m
```

Read-only - never touches `data/raw/`. Checks, per day and across the
whole date range:

- **Missing trading days** - any weekday with no file. Optionally pass
  `--holidays data/nse_holidays.json` (a flat JSON list of `"YYYY-MM-DD"`
  strings) so real holidays aren't flagged.
- **Duplicate timestamps** within a day.
- **Candle count** - flags days well below ~375 (the full NSE session) or
  unexpectedly above it.
- **Timestamps** - parseable, chronologically sorted, and consistent with
  an IST trading session (09:15-15:30, with slack for the fetch script's
  wider 09:00-15:45 request window). If timestamps land around 03:xx-10:xx
  instead, that's the tell-tale sign of UTC leaking in instead of IST.
- **OHLC sanity** - values positive, `high >= max(open, close, low)`,
  `low <= min(open, close, high)`.
- **Volume** - null volume, and days where >10% of candles have zero
  volume (flagged as a warning to eyeball, not a hard failure - some
  illiquid names/minutes legitimately have zero volume).

Issues are labeled `CRITICAL` (bad data - duplicates, broken OHLC, unreadable
files) or `WARNING` (needs a human glance - suspected holiday, low liquidity,
short session). The script exits non-zero only on `CRITICAL` issues. A full
JSON report is written alongside the console summary.

## 2. Build the processed dataset

```bash
python build_processed_dataset.py --symbol RELIND --interval 1m
```

Reads every raw daily JSON file, converts to the canonical schema:

| column    | type                      |
|-----------|---------------------------|
| timestamp | tz-aware, `Asia/Kolkata`  |
| symbol    | str                       |
| exchange  | str                       |
| open      | float                     |
| high      | float                     |
| low       | float                     |
| close     | float                     |
| volume    | float                     |

and writes one Parquet file per calendar month to
`data/processed/parquet/<SYMBOL>/<interval>/<YYYY-MM>.parquet`. Idempotent -
each month is fully rebuilt from whatever raw days exist for it, so re-running
after downloading more days just picks them up; it never duplicates rows.

This script intentionally does **not** re-run the validation checks - run
`validate_raw_data.py` (or `run_pipeline.py`, below) first and use your
judgment on `WARNING`s.

## 3. Load data for a backtest

```python
from data_loader import get_data

df = get_data("RELIND", "1m", "2026-01-01", "2026-01-31")
# DataFrame indexed by tz-aware timestamp, columns:
#   open, high, low, close, volume, symbol, exchange
```

Your backtest code only ever imports `data_loader`. It has no idea the data
came from Breeze/JSON - it just asks for a symbol, interval, and date range
and gets a clean DataFrame back. When you add Dhan, NSE bhavcopy, options
chains, etc., you write a new `build_processed_*.py` that lands data in the
same canonical Parquet layout, and `data_loader.py` (and everything built
on top of it) doesn't need to change.

## 4. VWAP (derived feature)

```
OHLCV
  |
  v
feature calculation
  |
  v
VWAP
```

```bash
python -m features.vwap --symbol RELIND --interval 1m
```

Computes session VWAP from the clean OHLCV Parquet and writes it to its
*own* Parquet tree at `data/processed/features/vwap/` - the underlying
OHLCV dataset is never modified.

The entire formula lives in one function,
`features/vwap.py::calculate_session_vwap()`, so tweaking it later
(different price anchor, different session reset rule, etc.) means editing
exactly one place. `features/vwap.py` also exposes `add_vwap(df)`, a
non-mutating helper that returns a copy of an OHLCV DataFrame with a
`vwap` column - this is what the backtest engine uses on the fly, rather
than reading the persisted feature Parquet, so it always reflects the
current formula even if you haven't rebuilt the feature dataset.

## All-in-one

```bash
python run_pipeline.py --symbol RELIND --interval 1m
```

Runs validation -> build the OHLCV dataset (only if no `CRITICAL` issues
were found; pass `--force` to build anyway) -> build the VWAP feature
dataset (pass `--skip-features` to stop after OHLCV).

## 5. Backtest

```
Data -> VWAP -> Strategy -> Trades -> P&L -> Statistics
```

```bash
cd backtest
python main.py                       # runs whatever STRATEGY is set in backtest/config.py
python main.py --strategy vwap_crossover   # override
```

- **`backtest/config.py`** says *which* strategy to run and on what
  symbol/date range - it has no strategy logic itself.
- **`backtest/main.py`** is everything common to every strategy: loading
  data, computing VWAP, turning a strategy's target-position series into
  trades, computing P&L, computing statistics, and writing reports. It
  never contains strategy-specific rules.
- **`backtest/<strategy_name>/strategy.py`** contains only that
  strategy's logic. The contract is a single function:

  ```python
  def generate_target_position(df) -> pd.Series:
      # df has OHLCV + 'vwap' columns
      # return a same-index series of 0 (flat) / 1 (long) per bar
  ```

  main.py turns changes in that series into round-trip trades (filled at
  the bar's close price - no slippage/commission in this first version).

- **`backtest/<strategy_name>/reports/`** gets `trades.csv`,
  `equity_curve.csv`, and `stats.csv` on every run.

The bundled example strategy, `vwap_crossover`, is intentionally trivial:
long whenever `close > vwap`, flat otherwise, always flat by the last bar
of the session. It's meant as a working skeleton to build more interesting
strategies against, not a real trading strategy.

To add a new strategy: create `backtest/<name>/strategy.py` with
`generate_target_position(df)`, set `STRATEGY = "<name>"` in
`backtest/config.py` (or pass `--strategy <name>`), and run `main.py`.
