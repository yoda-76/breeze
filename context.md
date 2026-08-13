# Breeze backtesting workspace

This repository is a local intraday-market-data pipeline and a small,
strategy-agnostic backtesting engine.  It currently contains Breeze-sourced
RELIND one-minute OHLCV data (IST) and a VWAP crossover example strategy.

## Data flow

`data/raw/breeze` is the immutable source JSON.  Validation checks the raw
files, `build_processed_dataset.py` converts them into canonical monthly
Parquet files, and `data_loader.py` is the one supported way for research or
backtest code to read the processed data.  Derived features live separately
under `data/processed/features`; they must never modify the canonical OHLCV
Parquet files.

## Main components

- `config.py` defines data locations, IST, and expected NSE session details.
- `data_loader.py` returns clean, timezone-aware OHLCV data.
- `features/vwap.py` contains the VWAP formula and multi-timeframe feature
  builder.
- `backtest/main.py` is the common engine: features -> strategy -> trades ->
  P&L -> statistics -> reports.
- `backtest/<strategy>/strategy.py` contains strategy logic only and exports
  `generate_target_position(df)`.  Strategies requiring intrabar fills may
  instead export `generate_trades(df, quantity)`.
- `backtest/config.py` selects symbol, date range, base interval, strategy,
  sizing, and monitored timeframes.

## VWAP and multiple timeframes

The base chart is currently `1m`.  The engine adds `vwap` (base-session
VWAP), plus `vwap_5m`, `vwap_15m`, and `vwap_30m`.  Higher timeframe OHLCV is
resampled from the same base data.  Its VWAP is joined backward to the
one-minute chart only once that higher-timeframe candle is complete, so a
strategy cannot see its future high, low, close, or volume.

Set `TIMEFRAMES` in `backtest/config.py` to change the monitored feature set.
Strategies receive all feature columns on the base-chart index.  The feature
CSV lets research inspect all charts/features together without a separate
loader.

## Reports and interpretation

Each backtest writes to `backtest/<strategy>/reports/`:

- `trades.csv`, `equity_curve.csv`, and `stats.csv` for trading results.
- `features.csv` for every base-chart bar and all VWAP features.
- `vwap_accuracy.csv` for the next-bar directional agreement of each
  above/below-VWAP signal, excluding overnight and unchanged-close bars.
- `vwap_chart.svg`, a dependency-free overlay chart of close and all VWAPs
  for the final session.

VWAP is an execution benchmark and mean-reversion/trend reference, not a
prediction by itself.  “Accuracy” here therefore means a clearly defined
research metric: whether price being above/below a VWAP agrees with the next
one-minute close direction.  It does not establish tradable performance;
consult the trade statistics, costs, slippage assumptions, and out-of-sample
tests before drawing a conclusion.

## Commands

Run the whole data pipeline:

```powershell
.\breeze_venv\Scripts\python.exe run_pipeline.py --symbol RELIND --interval 1m
```

Run the configured backtest:

```powershell
.\breeze_venv\Scripts\python.exe backtest\main.py
```

The engine fills at the bar close.  Set `TRANSACTION_COST_PER_TRADE` in
`backtest/config.py` to apply a flat round-trip cost to every completed trade;
slippage is not modelled.  The
`drift_vwap_long_short` strategy is the exception: it provides its own trade
ledger so stop/target orders are checked against one-minute highs/lows.  It
closes any remaining position on the last available bar.
