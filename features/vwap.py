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
import re
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


def resample_ohlcv(df, timeframe):
    """Return session-aligned OHLCV bars for a higher timeframe.

    The input timestamps identify one-minute bars.  Higher-timeframe bars are
    labelled at their *end* and are later joined backwards to the base chart;
    a 5-minute value therefore never exposes an unfinished 5-minute candle.
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Cannot resample OHLCV; missing columns: {sorted(missing)}")

    # Pandas 3 removed the lowercase ``m`` minute alias, while this project
    # deliberately uses compact market-data names such as ``5m``.
    pandas_timeframe = re.sub(r"(?i)(\d+)m$", r"\1min", timeframe)
    frames = []
    for _, session_df in df.groupby(df.index.normalize(), sort=True):
        bars = session_df.resample(
            pandas_timeframe, origin="start_day", offset="9h15min",
            label="right", closed="left",
        ).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        })
        bars = bars.dropna(subset=["open", "high", "low", "close"])
        frames.append(bars)

    if not frames:
        return df.iloc[0:0][["open", "high", "low", "close", "volume"]].copy()
    return pd.concat(frames).sort_index()


def add_multi_timeframe_vwap(df, timeframes=("1m", "5m", "15m", "30m")):
    """Add session VWAPs from several completed chart timeframes.

    ``vwap`` is always the base-chart VWAP.  ``vwap_5m``, ``vwap_15m`` and
    similar columns are calculated from OHLCV bars aggregated at that
    timeframe, then backward-asof joined to every base bar.  This preserves
    the information available at each minute and avoids higher-timeframe
    look-ahead bias.
    """
    out = add_vwap(df)
    base_index = out.index

    for timeframe in timeframes:
        if timeframe == "1m":
            continue
        bars = resample_ohlcv(df, timeframe)
        if bars.empty:
            out[f"vwap_{timeframe}"] = float("nan")
            continue
        bars["_vwap"] = calculate_session_vwap(bars)
        base = pd.DataFrame({"timestamp": base_index})
        base["session"] = base["timestamp"].dt.normalize()
        higher = bars[["_vwap"]].reset_index()
        higher = higher.rename(columns={higher.columns[0]: "timestamp"})
        higher["session"] = higher["timestamp"].dt.normalize()
        aligned = pd.merge_asof(
            base.sort_values("timestamp"), higher.sort_values("timestamp"),
            on="timestamp", by="session", direction="backward",
        ).set_index("timestamp")["_vwap"]
        aligned.index = base_index
        out[f"vwap_{timeframe}"] = aligned

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
