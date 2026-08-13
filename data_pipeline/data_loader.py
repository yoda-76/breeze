"""
Data-loading abstraction layer.

This is the ONLY module your backtest / research code should import to get
market data. It knows nothing beyond "read processed Parquet files and
return a clean DataFrame" - it has no idea Breeze, JSON, or any particular
broker exists. Swapping in Dhan, NSE bhavcopy data, options chains, etc.
later just means writing a new build_processed_*.py that produces the same
canonical Parquet layout; this loader (and everything downstream of it)
doesn't change.

Usage:

    from data_loader import get_data

    df = get_data("RELIND", "1m", "2026-01-01", "2026-01-31")
    # df: DatetimeIndex (tz=Asia/Kolkata) x [open, high, low, close, volume, symbol, exchange]
"""

import glob
import os

import pandas as pd

import config


class DataNotFoundError(FileNotFoundError):
    pass


def _month_range(start_date, end_date):
    months = pd.period_range(start=start_date, end=end_date, freq="M")
    return [str(m) for m in months]  # e.g. "2026-01"


def _available_months(symbol, interval):
    pattern = os.path.join(config.PROCESSED_ROOT, symbol, interval, "*.parquet")
    files = sorted(glob.glob(pattern))
    return {os.path.basename(f).replace(".parquet", ""): f for f in files}


def available_months(symbol, interval):
    """
    Public helper: which 'YYYY-MM' months exist in the processed OHLCV
    dataset for this symbol/interval. Used by feature-building scripts so
    they know which months to (re)compute, without duplicating the glob
    logic used internally by get_data().
    """
    return sorted(_available_months(symbol, interval).keys())


def get_data(symbol, interval, start_date, end_date, exchange=None):
    """
    Give me `symbol` `interval` data from `start_date` to `end_date`
    (inclusive) and get back a clean DataFrame.

    Parameters
    ----------
    symbol : str          e.g. "RELIND"
    interval : str         e.g. "1m"  (matches the processed dataset's folder name)
    start_date, end_date : str or datetime-like, inclusive on both ends.
                            Naive dates/strings are assumed to be IST.
    exchange : str, optional  filter to a specific exchange if a symbol
                               somehow has more than one in the dataset.

    Returns
    -------
    pandas.DataFrame indexed by tz-aware ("Asia/Kolkata") timestamp, with
    columns: open, high, low, close, volume, symbol, exchange.

    Raises
    ------
    DataNotFoundError if no processed Parquet files exist for this
    symbol/interval, or none overlap the requested date range.
    """
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)

    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize(config.IST)
    if end_ts.tzinfo is None:
        # Inclusive end-of-day if only a date was given.
        end_ts = end_ts.tz_localize(config.IST) + pd.Timedelta(hours=23, minutes=59, seconds=59)

    available = _available_months(symbol, interval)
    if not available:
        raise DataNotFoundError(
            f"No processed data found for symbol={symbol!r} interval={interval!r} "
            f"under {os.path.join(config.PROCESSED_ROOT, symbol, interval)}. "
            f"Run build_processed_dataset.py first."
        )

    wanted_months = _month_range(start_ts.date(), end_ts.date())
    relevant_files = [available[m] for m in wanted_months if m in available]

    if not relevant_files:
        raise DataNotFoundError(
            f"Processed data exists for {symbol}/{interval}, but none of it "
            f"covers {start_ts.date()} -> {end_ts.date()}. "
            f"Available months: {sorted(available.keys())}"
        )

    frames = [pd.read_parquet(f) for f in relevant_files]
    df = pd.concat(frames, ignore_index=True)

    df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]

    if exchange is not None:
        df = df[df["exchange"] == exchange]

    if df.empty:
        raise DataNotFoundError(
            f"No candles for {symbol}/{interval} between {start_ts} and {end_ts}."
        )

    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    df = df.set_index("timestamp")

    return df[["open", "high", "low", "close", "volume", "symbol", "exchange"]]


def list_available_range(symbol, interval):
    """Convenience helper: what date range do we actually have on disk?"""
    available = _available_months(symbol, interval)
    if not available:
        return None
    months = sorted(available.keys())
    first_df = pd.read_parquet(available[months[0]])
    last_df = pd.read_parquet(available[months[-1]])
    return first_df["timestamp"].min(), last_df["timestamp"].max()
