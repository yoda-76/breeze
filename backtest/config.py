"""
Backtest configuration: which strategy to run, and on what data.

main.py reads this file to know:
  - which strategy module to load (backtest/<STRATEGY>/strategy.py)
  - where to write reports (backtest/<STRATEGY>/reports/)
  - what data to feed it
"""

# Folder name under backtest/ - must contain a strategy.py exposing
# generate_target_position(df).
STRATEGY = "drift_vwap_long_short"

SYMBOL = "RELIND"
INTERVAL = "1m"

# The engine keeps this base chart plus completed higher-timeframe VWAP
# features aligned to it.  Strategies receive: vwap, vwap_5m, vwap_15m,
# and vwap_30m.  Do not include timeframes shorter than INTERVAL.
TIMEFRAMES = ("1m", "5m", "15m", "30m")

START_DATE = "2022-04-01"
END_DATE = "2026-07-31"

# Preserve the previous January report, which may be open in Excel.
REPORT_NAME = "2022-04_to_2026-07_qty50_cost40"

# Fixed position size in shares, kept simple for the first backtest.
# No leverage, no compounding, no position sizing logic yet.
QUANTITY = 50

# Flat round-trip brokerage, fees, and taxes applied once per completed trade.
TRANSACTION_COST_PER_TRADE = 40.0

INITIAL_CAPITAL = 100_000
