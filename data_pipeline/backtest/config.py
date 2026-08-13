"""
Backtest configuration: which strategy to run, and on what data.

main.py reads this file to know:
  - which strategy module to load (backtest/<STRATEGY>/strategy.py)
  - where to write reports (backtest/<STRATEGY>/reports/)
  - what data to feed it
"""

# Folder name under backtest/ - must contain a strategy.py exposing
# generate_target_position(df).
STRATEGY = "vwap_crossover"

SYMBOL = "RELIND"
INTERVAL = "1m"

START_DATE = "2026-01-01"
END_DATE = "2026-01-31"

# Fixed position size in shares, kept simple for the first backtest.
# No leverage, no compounding, no position sizing logic yet.
QUANTITY = 1

INITIAL_CAPITAL = 100_000
