"""
Shared configuration for the data validation / processing / loading pipeline.

Keeping this in one place means validate_raw_data.py, build_processed_dataset.py
and data_loader.py all agree on where raw JSON lives, where processed Parquet
lives, and what "normal" looks like for a trading day.
"""

import os

# ============================================================
# DIRECTORY LAYOUT
#
# data/
# ├── raw/
# │   └── breeze/
# │       └── RELIND/
# │           └── 1m/
# │               └── 2026/01/2026-01-01.json
# └── processed/
#     └── parquet/
#         └── RELIND/
#             └── 1m/
#                 ├── 2026-01.parquet
#                 └── 2026-02.parquet
# ============================================================

DATA_ROOT = os.getenv("DATA_ROOT", "data")

RAW_ROOT = os.path.join(DATA_ROOT, "raw", "breeze")
PROCESSED_ROOT = os.path.join(DATA_ROOT, "processed", "parquet")

# Derived features (e.g. VWAP) are stored separately from the canonical
# OHLCV Parquet so the underlying processed data is never touched.
#
# data/processed/features/vwap/RELIND/1m/2026-01.parquet
FEATURES_ROOT = os.path.join(DATA_ROOT, "processed", "features")

# ============================================================
# TIMEZONE
#
# Breeze's `datetime` field on each candle (e.g. "2026-01-09 12:39:00")
# is a naive string that represents IST wall-clock time, NOT UTC,
# even though the *request* timestamps sent to the API are formatted
# with a trailing "Z". We localize to IST when parsing.
# ============================================================

IST = "Asia/Kolkata"

# ============================================================
# EXPECTED TRADING SESSION (IST)
#
# NSE cash market: 09:15 -> 15:30 IST = 375 one-minute candles.
# The fetch script requests a slightly wider window (09:00 -> 15:45)
# so a handful of extra/boundary candles outside the official session
# can legitimately show up. We validate against the official session.
# ============================================================

SESSION_START = "09:15"
SESSION_END = "15:30"
EXPECTED_CANDLES_PER_DAY = 375

# Allow some slack for early close / late data / broker quirks before
# flagging a day as suspicious rather than outright broken.
MIN_ACCEPTABLE_CANDLES = 300  # below this -> flagged (possible half day / gap)

# ============================================================
# CANONICAL PROCESSED SCHEMA
# ============================================================

CANONICAL_COLUMNS = [
    "timestamp",
    "symbol",
    "exchange",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

INTERVAL_DIR = "1m"  # matches STOCK_DATA_DIR/1m in the fetch script
