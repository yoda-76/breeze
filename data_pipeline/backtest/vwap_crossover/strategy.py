"""
Strategy: VWAP Crossover (long-only, intraday, flat overnight)

Rule (as simple as it gets, for a first backtest):
    - Be LONG whenever close > VWAP
    - Be FLAT whenever close <= VWAP
    - Always flat by the last bar of the session (no overnight positions)

Strategy contract (used by backtest/main.py):
    generate_target_position(df) -> pandas.Series
        Same index as df. Value is the TARGET position to hold after each
        bar closes: 1 = long, 0 = flat. main.py turns changes in this
        series into trades - it doesn't know or care what logic produced
        them.

`df` arrives already containing OHLCV + a 'vwap' column (added by
features.vwap.add_vwap upstream in main.py).
"""

import pandas as pd


def generate_target_position(df):
    target = (df["close"] > df["vwap"]).astype(int)

    # Force flat on the last bar of every session - this is an intraday
    # strategy, we never want to carry a position overnight.
    session = df.index.date
    is_last_bar_of_session = pd.Series(session, index=df.index).ne(
        pd.Series(session, index=df.index).shift(-1)
    )
    target[is_last_bar_of_session] = 0

    return target
