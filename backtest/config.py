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
REPORT_NAME = "2022-04_to_2026-07_qty50_cost40_configured"

# Fixed position size in shares, kept simple for the first backtest.
# No leverage, no compounding, no position sizing logic yet.
QUANTITY = 700

# Flat round-trip brokerage, fees, and taxes applied once per completed trade.
TRANSACTION_COST_PER_TRADE = 40.0

# Estimated adverse fill per share at each entry and exit. Keep at zero until
# you have a defensible estimate from live fills or bid/ask data.
SLIPPAGE_PER_SHARE = 0.0

# Drift-VWAP strategy controls. Stops/targets are absolute price points.
MAX_TRADES_PER_DAY = 4
MAX_LOSSES_PER_DAY = 2
MOMENTUM_BARS = 12
MOMENTUM_THRESHOLD_PCT = 0.10
NO_NEW_ENTRIES_FROM = "15:30"
LONG_STOP_POINTS = 10
LONG_TARGET_POINTS = 30
SHORT_STOP_POINTS = 10
SHORT_TARGET_POINTS = 30

INITIAL_CAPITAL = 1_000_000

#.\breeze_venv\Scripts\Activate.ps1 