"""
VWAP feature calculation.

    OHLCV
      |
      v
  feature calculation
      |
      v
    VWAP

This module only ever READS the clean processed OHLCV Parquet dataset
(via data_loader.get_data) and writes a separate, derived VWAP Parquet
dataset. It never modifies data/processed/parquet/ - VWAP lives in its
own tree under data/processed/features/vwap/.

The actual math lives in exactly one function, calculate_session_vwap(),
so tweaking the formula (different price anchor, resetting rules, etc.)
only ever means editing one place.

CLI usage (also invoked automatically by run_pipeline.py):

    python -m features.vwap --symbol RELIND --interval 1m
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config          # noqa: E402
import data_loader      # noqa: E402


# ============================================================
# THE FORMULA
#
# Session VWAP = cumulative(typical_price * volume) / cumulative(volume),
# with the cumulative sums resetting at the start of each trading session
# (calendar day, since this is intraday 1-minute data).
#
# This is the single place to tweak the formula, e.g.:
#   - use close instead of (H+L+C)/3
#   - anchor on something other than the calendar day
#   - add a rolling/windowed VWAP variant
# ============================================================

def calculate_session_vwap(df):
    """
    df: DataFrame with a tz-aware DatetimeIndex and columns
        ['high', 'low', 'close', 'volume'].

    Returns a pandas.Series (same index as df) of session VWAP values.
    Does NOT modify df.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    price_volume = typical_price * df["volume"]

    session = df.index.date  # one session per calendar day

    cumulative_pv = price_volume.groupby(session).cumsum()
    cumulative_volume = df["volume"].groupby(session).cumsum()

    return cumulative_pv / cumulative_volume


def add_vwap(df):
    """
    Convenience wrapper: returns a COPY of df with a 'vwap' column added.
    Use this in strategy/backtest code rather than mutating the original
    OHLCV DataFrame in place.
    """
    out = df.copy()
    out["vwap"] = calculate_session_vwap(out)
    return out


# ============================================================
# PERSIST AS A SEPARATE, DERIVED PARQUET DATASET
# ============================================================

def build_month_vwap(symbol, interval, month_key):
    """Compute VWAP for one 'YYYY-MM' and return a DataFrame ready to save."""
    start = f"{month_key}-01"
    end = pd.Period(month_key, freq="M").end_time.date().isoformat()

    ohlcv = data_loader.get_data(symbol, interval, start, end)
    vwap = calculate_session_vwap(ohlcv)

    result = pd.DataFrame({
        "timestamp": ohlcv.index,
        "symbol": ohlcv["symbol"].values,
        "vwap": vwap.values,
    })
    return result


def run(symbol, interval):
    months = data_loader.available_months(symbol, interval)
    if not months:
        print(f"No processed OHLCV data found for {symbol}/{interval}. "
              f"Run build_processed_dataset.py first.")
        return 1

    out_dir = os.path.join(config.FEATURES_ROOT, "vwap", symbol, interval)
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 70)
    print(f"BUILDING VWAP FEATURE: {symbol} / {interval}")
    print("=" * 70)

    for month_key in months:
        df = build_month_vwap(symbol, interval, month_key)
        out_path = os.path.join(out_dir, f"{month_key}.parquet")
        df.to_parquet(out_path, engine="pyarrow", index=False)
        print(f"{month_key}: {len(df):>6} rows -> {out_path}")

    print("-" * 70)
    print(f"Output directory: {out_dir}")
    print("=" * 70)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Build the VWAP feature dataset.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default=config.INTERVAL_DIR)
    args = parser.parse_args()

    sys.exit(run(args.symbol, args.interval))


if __name__ == "__main__":
    main()
