"""
Convert raw Breeze daily JSON files into the canonical processed Parquet
dataset. The raw JSON is only ever read, never modified.

Canonical schema (one row per candle):
    timestamp   (tz-aware, Asia/Kolkata)
    symbol
    exchange
    open, high, low, close   (float)
    volume                   (float)

Output layout:
    data/processed/parquet/<SYMBOL>/<interval>/<YYYY-MM>.parquet

Re-running this script is idempotent: each month's file is rebuilt from
whatever raw days now exist for that month, so newly-downloaded days get
picked up automatically and re-running never duplicates rows.

Usage:
    python data_pipeline/build_processed_dataset.py --symbol RELIND [--interval 1m]
                                       [--skip-invalid-days]
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

import pandas as pd

import config


def discover_day_files(symbol, interval):
    symbol_dir = os.path.join(config.RAW_ROOT, symbol, interval)
    pattern = os.path.join(symbol_dir, "*", "*", "*.json")
    files = [f for f in glob.glob(pattern) if not f.endswith(".tmp")]
    files.sort()
    return files


def load_day_candles(file_path):
    """
    Read one raw daily JSON file and return a list of canonical row dicts.
    Returns [] for files that are empty/incomplete/unreadable - these are
    skipped silently here because validate_raw_data.py is the place to
    surface data-quality problems; this script assumes you've already run
    (and reviewed) that validation.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as error:
        print(f"  [skip] {file_path}: could not read JSON ({error})")
        return []

    if payload.get("status") != "data":
        return []

    symbol = payload.get("stock_code")
    exchange = payload.get("exchange_code")

    rows = []
    for candle in payload.get("data", []):
        ts = candle.get("datetime")
        if not ts:
            continue
        try:
            dt = datetime.strptime(str(ts).strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        try:
            rows.append({
                "timestamp": dt,
                "symbol": symbol,
                "exchange": exchange,
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return rows


def group_files_by_month(files):
    """Map 'YYYY-MM' -> [file_path, ...] based on the filename's date."""
    by_month = defaultdict(list)
    for f in files:
        date_str = os.path.basename(f).replace(".json", "")
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        month_key = day.strftime("%Y-%m")
        by_month[month_key].append(f)
    return by_month


def build_month_dataframe(symbol, month_key, files):
    all_rows = []
    for f in files:
        all_rows.extend(load_day_candles(f))

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows, columns=config.CANONICAL_COLUMNS)

    # Localize to IST (the candle timestamps are naive IST wall-clock time).
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(config.IST)

    # Drop exact duplicate timestamps defensively (should already be unique
    # per-file thanks to the fetch script, but different files/re-downloads
    # could in principle overlap).
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def write_month_parquet(symbol, interval, month_key, df):
    out_dir = os.path.join(config.PROCESSED_ROOT, symbol, interval)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{month_key}.parquet")

    df.to_parquet(out_path, engine="pyarrow", index=False)
    return out_path


def run(symbol, interval):
    files = discover_day_files(symbol, interval)
    if not files:
        print(f"No raw files found for {symbol}/{interval} under {config.RAW_ROOT}")
        return 1

    by_month = group_files_by_month(files)

    print("=" * 70)
    print(f"BUILDING PROCESSED DATASET: {symbol} / {interval}")
    print("=" * 70)

    total_rows = 0
    for month_key in sorted(by_month):
        df = build_month_dataframe(symbol, month_key, by_month[month_key])
        if df is None or df.empty:
            print(f"{month_key}: no usable candles, skipping")
            continue

        out_path = write_month_parquet(symbol, interval, month_key, df)
        total_rows += len(df)
        print(f"{month_key}: {len(df):>6} rows "
              f"({df['timestamp'].min()} -> {df['timestamp'].max()}) -> {out_path}")

    print("-" * 70)
    print(f"Total rows written: {total_rows}")
    print(f"Output directory  : {os.path.join(config.PROCESSED_ROOT, symbol, interval)}")
    print("=" * 70)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Build canonical Parquet dataset from raw Breeze JSON.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default=config.INTERVAL_DIR)
    args = parser.parse_args()

    sys.exit(run(args.symbol, args.interval))


if __name__ == "__main__":
    main()
