"""Drift VWAP — CASE 3: VWAP TOUCH ONLY.

Signals are evaluated only at completed 5-minute candles.
15-minute VWAP is calculated from completed 15-minute candles.
Positions are monitored against each underlying one-minute bar
for stop/target exits.

This file is a variant of the original Drift VWAP strategy.
Only the VWAP-entry trigger is changed. All other filters,
guardrails, stops, targets, and exit handling remain the same.
"""

import pandas as pd

from features.vwap import calculate_session_vwap, resample_ohlcv


MAX_TRADES_PER_DAY = 4
MAX_LOSSES_PER_DAY = 2
MOMENTUM_BARS = 12
MOMENTUM_THRESHOLD_PCT = 0.10
NO_NEW_ENTRIES_FROM = "15:30"

LONG_STOP, LONG_TARGET = 40.0, 80.0
SHORT_STOP, SHORT_TARGET = 50.0, 80.0


def configure(config):
    """Load optional Drift-VWAP controls from backtest/config.py."""
    global MAX_TRADES_PER_DAY, MAX_LOSSES_PER_DAY, MOMENTUM_BARS
    global MOMENTUM_THRESHOLD_PCT, NO_NEW_ENTRIES_FROM
    global LONG_STOP, LONG_TARGET, SHORT_STOP, SHORT_TARGET

    MAX_TRADES_PER_DAY = int(
        getattr(config, "MAX_TRADES_PER_DAY", MAX_TRADES_PER_DAY)
    )
    MAX_LOSSES_PER_DAY = int(
        getattr(config, "MAX_LOSSES_PER_DAY", MAX_LOSSES_PER_DAY)
    )
    MOMENTUM_BARS = int(
        getattr(config, "MOMENTUM_BARS", MOMENTUM_BARS)
    )
    MOMENTUM_THRESHOLD_PCT = float(
        getattr(config, "MOMENTUM_THRESHOLD_PCT", MOMENTUM_THRESHOLD_PCT)
    )
    NO_NEW_ENTRIES_FROM = str(
        getattr(config, "NO_NEW_ENTRIES_FROM", NO_NEW_ENTRIES_FROM)
    )
    LONG_STOP = float(
        getattr(config, "LONG_STOP_POINTS", LONG_STOP)
    )
    LONG_TARGET = float(
        getattr(config, "LONG_TARGET_POINTS", LONG_TARGET)
    )
    SHORT_STOP = float(
        getattr(config, "SHORT_STOP_POINTS", SHORT_STOP)
    )
    SHORT_TARGET = float(
        getattr(config, "SHORT_TARGET_POINTS", SHORT_TARGET)
    )


def _completed_bars(df, timeframe):
    """Aggregate and label each candle with its actual final base-bar time."""
    bars = resample_ohlcv(df, timeframe)

    # resample_ohlcv labels [09:15, 09:20) at 09:20.
    # It is actually known only after the 09:19 base bar closes.
    bars.index = bars.index - pd.Timedelta(minutes=1)

    bars["vwap"] = calculate_session_vwap(bars)
    return bars


def _trade(entry_time, entry_price, exit_time, exit_price, side, quantity):
    direction = 1 if side == "LONG" else -1
    pnl = (exit_price - entry_price) * direction * quantity

    return {
        "entry_time": entry_time,
        "entry_price": entry_price,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "side": side,
        "quantity": quantity,
        "pnl": pnl,
        "pnl_pct": (exit_price - entry_price) / entry_price * direction * 100,
        "holding_minutes": (exit_time - entry_time).total_seconds() / 60,
    }


def generate_trades(df, quantity):
    """
    Generate trades using the selected VWAP-touch trigger.

    IMPORTANT:
    Everything except the VWAP trigger is kept the same as the
    original strategy.
    """

    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)

    if missing:
        raise ValueError(
            f"Drift VWAP strategy requires columns: {sorted(missing)}"
        )

    bars_5m = _completed_bars(df, "5m")
    bars_15m = _completed_bars(df, "15m")

    bars_15m["vwap_15m_ago"] = (
        bars_15m.groupby(bars_15m.index.normalize())["vwap"].shift(1)
    )

    # At every completed 5m candle, use only the last completed 15m bar.
    decision = pd.merge_asof(
        bars_5m.reset_index()
        .rename(columns={
            bars_5m.index.name or "index": "timestamp"
        })
        .sort_values("timestamp"),

        bars_15m[["vwap", "vwap_15m_ago"]]
        .reset_index()
        .rename(columns={
            bars_15m.index.name or "index": "timestamp",
            "vwap": "vwap15",
        })
        .sort_values("timestamp"),

        on="timestamp",
        direction="backward",
    ).set_index("timestamp")

    decision["previous_close"] = (
        decision.groupby(decision.index.normalize())["close"].shift(1)
    )
    decision["previous_open"] = (
        decision.groupby(decision.index.normalize())["open"].shift(1)
    )
    decision["close_1h_ago"] = (
        decision.groupby(decision.index.normalize())["close"]
        .shift(MOMENTUM_BARS)
    )
    decision["momentum_pct"] = (
        decision["close"] / decision["close_1h_ago"] - 1
    ) * 100

    trades = []
    position = None
    trades_today = 0
    losses_today = 0
    current_session = None

    final_times = set(
        df.groupby(df.index.normalize()).tail(1).index
    )

    for timestamp, bar in df.iterrows():

        session = timestamp.normalize()

        if session != current_session:
            current_session = session
            trades_today = 0
            losses_today = 0

        # ----------------------------------------------------
        # Existing position: monitor every 1m bar
        # ----------------------------------------------------

        if position is not None:

            # Conservative rule:
            # if one 1m bar hits both stop and target,
            # assume stop happened first.
            if (
                position["side"] == "LONG"
                and bar["low"] <= position["stop"]
            ):
                trades.append(
                    _trade(
                        position["time"],
                        position["price"],
                        timestamp,
                        position["stop"],
                        "LONG",
                        quantity,
                    )
                )
                losses_today += 1
                position = None

            elif (
                position is not None
                and position["side"] == "LONG"
                and bar["high"] >= position["target"]
            ):
                trades.append(
                    _trade(
                        position["time"],
                        position["price"],
                        timestamp,
                        position["target"],
                        "LONG",
                        quantity,
                    )
                )
                position = None

            elif (
                position is not None
                and position["side"] == "SHORT"
                and bar["high"] >= position["stop"]
            ):
                trades.append(
                    _trade(
                        position["time"],
                        position["price"],
                        timestamp,
                        position["stop"],
                        "SHORT",
                        quantity,
                    )
                )
                losses_today += 1
                position = None

            elif (
                position is not None
                and position["side"] == "SHORT"
                and bar["low"] <= position["target"]
            ):
                trades.append(
                    _trade(
                        position["time"],
                        position["price"],
                        timestamp,
                        position["target"],
                        "SHORT",
                        quantity,
                    )
                )
                position = None

        # End-of-session exit
        if position is not None and timestamp in final_times:
            trades.append(
                _trade(
                    position["time"],
                    position["price"],
                    timestamp,
                    bar["close"],
                    position["side"],
                    quantity,
                )
            )
            position = None
            continue

        if position is not None or timestamp not in decision.index:
            continue

        if timestamp.strftime("%H:%M") >= NO_NEW_ENTRIES_FROM:
            continue

        if (
            trades_today >= MAX_TRADES_PER_DAY
            or losses_today >= MAX_LOSSES_PER_DAY
        ):
            continue

        signal = decision.loc[timestamp]

        if pd.isna(
            signal[
                [
                    "vwap15",
                    "vwap_15m_ago",
                    "previous_close",
                    "previous_open",
                    "close_1h_ago",
                ]
            ]
        ).any():
            continue

        red = signal["close"] < signal["open"]
        green = signal["close"] > signal["open"]

        previous_red = (
            signal["previous_close"] < signal["previous_open"]
        )
        previous_green = (
            signal["previous_close"] > signal["previous_open"]
        )

        vwap = signal["vwap15"]

        # ====================================================
        # VWAP TOUCH CONDITIONS
        # ====================================================
        #
        # These two booleans are intentionally defined
        # differently in the two variants.
        #
        # LONG:
        #   Candle low touches/crosses VWAP.
        #
        # SHORT:
        #   Candle high touches/crosses VWAP.
        #
        # ====================================================

        vwap_touched_long = signal["low"] <= vwap
        vwap_touched_short = signal["high"] >= vwap

        # ----------------------------------------------------
        # CASE 3: VWAP TOUCH ONLY
        # ----------------------------------------------------
        #
        # The candle must touch VWAP.
        #
        # The existing price-vs-VWAP conditions below are kept:
        # LONG  -> close above VWAP
        # SHORT -> close below VWAP
        #
        # There is NO additional rejection requirement here.
        # ----------------------------------------------------

        side = None

        if (
            signal["close"] > vwap
            and signal["vwap15"] > signal["vwap_15m_ago"]
            and signal["momentum_pct"] >= MOMENTUM_THRESHOLD_PCT
            and red
            and previous_green
            and vwap_touched_long
        ):
            side = "LONG"

        elif (
            signal["close"] < vwap
            and signal["vwap15"] < signal["vwap_15m_ago"]
            and signal["momentum_pct"] <= -MOMENTUM_THRESHOLD_PCT
            and green
            and previous_red
            and vwap_touched_short
        ):
            side = "SHORT"

        if side == "LONG":
            position = {
                "side": side,
                "time": timestamp,
                "price": bar["close"],
                "stop": bar["close"] - LONG_STOP,
                "target": bar["close"] + LONG_TARGET,
            }
            trades_today += 1

        elif side == "SHORT":
            position = {
                "side": side,
                "time": timestamp,
                "price": bar["close"],
                "stop": bar["close"] + SHORT_STOP,
                "target": bar["close"] - SHORT_TARGET,
            }
            trades_today += 1

    columns = [
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "side",
        "quantity",
        "pnl",
        "pnl_pct",
        "holding_minutes",
    ]

    return pd.DataFrame(trades, columns=columns)
