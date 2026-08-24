# Backtest runbook

## 1. Download data for an instrument

Use the project virtual environment.  The Breeze SDK has a local import fix
in `breeze_venv`; do not use a system Python installation for this project.

1. In `.env`, set `STOCK_CODE`, `FROM_DATE`, and `TO_DATE`.  Keep API
   credentials (`API_KEY`, `API_SECRET`, `API_SESSION`) private.  Example:

   ```dotenv
   STOCK_CODE=RELIND
   FROM_DATE=2022-04-01T09:00:00.000Z
   TO_DATE=2026-07-31T15:45:00.000Z
   EXCHANGE_CODE=NSE
   PRODUCT_TYPE=cash
   INTERVAL=1minute
   DATA_ROOT=data/raw/breeze
   ```

2. Download twice.  Successful daily files are skipped; empty/error days are
   requested again on the second pass.

   ```powershell
   .\breeze_venv\Scripts\python.exe .\data_pipeline\data_fetch_script.py
   .\breeze_venv\Scripts\python.exe .\data_pipeline\data_fetch_script.py
   ```

3. Validate and process the downloaded data:

   ```powershell
   .\breeze_venv\Scripts\python.exe .\data_pipeline\run_pipeline.py --symbol RELIND --interval 1m
   ```

Raw JSON goes to `data/raw/breeze/<SYMBOL>/1m/`; canonical monthly Parquet
goes to `data/processed/parquet/<SYMBOL>/1m/`.  Check
`data/validation_report_<SYMBOL>.json` before treating missing weekdays as
holidays—Breeze failures and partial sessions can also create gaps.

## 2. Run a backtest for an instrument

1. Confirm the instrument and date range are available as processed Parquet.
2. Edit `backtest/config.py`: choose `STRATEGY`, `SYMBOL`, `START_DATE`,
   `END_DATE`, and a unique `REPORT_NAME`.
3. Run:

   ```powershell
   .\breeze_venv\Scripts\python.exe .\backtest\main.py
   ```

Reports are written to `backtest/<STRATEGY>/reports/<REPORT_NAME>/`:
`trades.csv`, `equity_curve.csv`, `stats.csv`, full `features.csv`, VWAP
accuracy, and an SVG chart.  Use a new `REPORT_NAME` for each meaningful
experiment; it prevents overwriting a comparison run and avoids Excel file
locks.

## 3. Backtest parameters and their significance

### Data and portfolio

| Parameter | Meaning |
|---|---|
| `SYMBOL`, `INTERVAL`, `START_DATE`, `END_DATE` | Instrument and available processed data to test. |
| `TIMEFRAMES` | Base and higher-timeframe VWAP views supplied to strategies. |
| `QUANTITY` | Shares/contracts per trade. It scales gross P&L and slippage, but not a flat per-trade cost. |
| `INITIAL_CAPITAL` | Starting equity used for return and equity-curve reporting. |
| `TRANSACTION_COST_PER_TRADE` | Flat **round-trip** cost per completed trade. Net P&L and win rate include it. |
| `SLIPPAGE_PER_SHARE` | Adverse fill estimate per share **at each side**; total per trade is `2 × quantity × slippage`. |
| `REPORT_NAME` | Separate output folder for this experiment. |

### Drift VWAP strategy

| Parameter | Meaning |
|---|---|
| `MAX_TRADES_PER_DAY` | Caps turnover and cost exposure. |
| `MAX_LOSSES_PER_DAY` | Stops fresh entries after this many stop-loss exits. |
| `MOMENTUM_BARS` | Lookback in completed 5-minute bars; 12 is one hour. |
| `MOMENTUM_THRESHOLD_PCT` | Minimum trend strength for an entry. Higher values trade less often. |
| `NO_NEW_ENTRIES_FROM` | IST cutoff for new entries. Existing positions still close by stop, target, or session end. |
| `LONG_STOP_POINTS`, `LONG_TARGET_POINTS` | Long risk/reward distances from entry. |
| `SHORT_STOP_POINTS`, `SHORT_TARGET_POINTS` | Short risk/reward distances from entry. |

Important: validate each parameter choice out-of-sample. Do not select values
only because they maximize the full historical result; that is overfitting.
