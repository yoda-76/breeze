# """Drift VWAP long/short strategy.

# Signals are evaluated only at completed 5-minute candles.  15-minute VWAP
# is calculated from completed 15-minute candles, so no higher-timeframe data
# from a still-forming candle is visible.  Positions are then monitored against
# each underlying one-minute bar for stop/target exits.

# The source pseudocode uses ET times, but this data is NSE/IST and ends near
# 15:30 IST.  The equivalent safe implementation blocks entries from 15:30 IST
# and closes at the final available bar of each session (there is no 15:55 IST
# bar in this dataset).
# """

import pandas as pd

from features.vwap import calculate_session_vwap, resample_ohlcv


MAX_TRADES_PER_DAY = 4
MAX_LOSSES_PER_DAY = 2
MOMENTUM_BARS = 12             # 12 completed 5-minute candles = one hour
MOMENTUM_THRESHOLD_PCT = 0.10
NO_NEW_ENTRIES_FROM = "15:30"
LONG_STOP, LONG_TARGET = 40.0, 80.0
SHORT_STOP, SHORT_TARGET = 50.0, 80.0


def configure(config):
    """Load optional drift-VWAP controls from backtest/config.py."""
    global MAX_TRADES_PER_DAY, MAX_LOSSES_PER_DAY, MOMENTUM_BARS
    global MOMENTUM_THRESHOLD_PCT, NO_NEW_ENTRIES_FROM
    global LONG_STOP, LONG_TARGET, SHORT_STOP, SHORT_TARGET
    MAX_TRADES_PER_DAY = int(getattr(config, "MAX_TRADES_PER_DAY", MAX_TRADES_PER_DAY))
    MAX_LOSSES_PER_DAY = int(getattr(config, "MAX_LOSSES_PER_DAY", MAX_LOSSES_PER_DAY))
    MOMENTUM_BARS = int(getattr(config, "MOMENTUM_BARS", MOMENTUM_BARS))
    MOMENTUM_THRESHOLD_PCT = float(getattr(config, "MOMENTUM_THRESHOLD_PCT", MOMENTUM_THRESHOLD_PCT))
    NO_NEW_ENTRIES_FROM = str(getattr(config, "NO_NEW_ENTRIES_FROM", NO_NEW_ENTRIES_FROM))
    LONG_STOP = float(getattr(config, "LONG_STOP_POINTS", LONG_STOP))
    LONG_TARGET = float(getattr(config, "LONG_TARGET_POINTS", LONG_TARGET))
    SHORT_STOP = float(getattr(config, "SHORT_STOP_POINTS", SHORT_STOP))
    SHORT_TARGET = float(getattr(config, "SHORT_TARGET_POINTS", SHORT_TARGET))


def _completed_bars(df, timeframe):
    """Aggregate and label each candle with its actual final base-bar time."""
    bars = resample_ohlcv(df, timeframe)
    # resample_ohlcv labels [09:15, 09:20) at 09:20.  It is actually known
    # only after the 09:19 base bar closes, so make that availability explicit.
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
    """Return a trade ledger following the supplied drift-VWAP pseudocode."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Drift VWAP strategy requires columns: {sorted(missing)}")

    bars_5m = _completed_bars(df, "5m")
    bars_15m = _completed_bars(df, "15m")
    bars_15m["vwap_15m_ago"] = bars_15m.groupby(bars_15m.index.normalize())["vwap"].shift(1)

    # At every completed 5m candle, use only the last completed 15m bar.
    decision = pd.merge_asof(
        bars_5m.reset_index().rename(columns={bars_5m.index.name or "index": "timestamp"}).sort_values("timestamp"),
        bars_15m[["vwap", "vwap_15m_ago"]].reset_index().rename(
            columns={bars_15m.index.name or "index": "timestamp", "vwap": "vwap15"}
        ).sort_values("timestamp"),
        on="timestamp", direction="backward",
    ).set_index("timestamp")
    decision["previous_close"] = decision.groupby(decision.index.normalize())["close"].shift(1)
    decision["previous_open"] = decision.groupby(decision.index.normalize())["open"].shift(1)
    decision["close_1h_ago"] = decision.groupby(decision.index.normalize())["close"].shift(MOMENTUM_BARS)
    decision["momentum_pct"] = (decision["close"] / decision["close_1h_ago"] - 1) * 100

    trades, position = [], None
    trades_today = losses_today = 0
    current_session = None
    final_times = set(df.groupby(df.index.normalize()).tail(1).index)

    for timestamp, bar in df.iterrows():
        session = timestamp.normalize()
        if session != current_session:
            current_session, trades_today, losses_today = session, 0, 0

        # Monitor every one-minute OHLC bar.  If a bar spans both levels, use
        # the stop first: without tick ordering, this is the conservative fill.
        if position is not None:
            if position["side"] == "LONG" and bar["low"] <= position["stop"]:
                trades.append(_trade(position["time"], position["price"], timestamp, position["stop"], "LONG", quantity))
                losses_today += 1
                position = None
            elif position is not None and position["side"] == "LONG" and bar["high"] >= position["target"]:
                trades.append(_trade(position["time"], position["price"], timestamp, position["target"], "LONG", quantity))
                position = None
            elif position is not None and position["side"] == "SHORT" and bar["high"] >= position["stop"]:
                trades.append(_trade(position["time"], position["price"], timestamp, position["stop"], "SHORT", quantity))
                losses_today += 1
                position = None
            elif position is not None and position["side"] == "SHORT" and bar["low"] <= position["target"]:
                trades.append(_trade(position["time"], position["price"], timestamp, position["target"], "SHORT", quantity))
                position = None

        if position is not None and timestamp in final_times:
            trades.append(_trade(position["time"], position["price"], timestamp, bar["close"], position["side"], quantity))
            position = None
            continue

        if position is not None or timestamp not in decision.index:
            continue
        if timestamp.strftime("%H:%M") >= NO_NEW_ENTRIES_FROM:
            continue
        if trades_today >= MAX_TRADES_PER_DAY or losses_today >= MAX_LOSSES_PER_DAY:
            continue

        signal = decision.loc[timestamp]
        if pd.isna(signal[["vwap15", "vwap_15m_ago", "previous_close", "previous_open", "close_1h_ago"]]).any():
            continue
        red, green = signal["close"] < signal["open"], signal["close"] > signal["open"]
        previous_red = signal["previous_close"] < signal["previous_open"]
        previous_green = signal["previous_close"] > signal["previous_open"]
        closer_to_vwap = abs(signal["close"] - signal["vwap15"]) < abs(signal["previous_close"] - signal["vwap15"])

        side = None
        if (signal["close"] > signal["vwap15"] and signal["vwap15"] > signal["vwap_15m_ago"]
                and signal["momentum_pct"] >= MOMENTUM_THRESHOLD_PCT and red and previous_green and closer_to_vwap):
            side = "LONG"
        elif (signal["close"] < signal["vwap15"] and signal["vwap15"] < signal["vwap_15m_ago"]
                and signal["momentum_pct"] <= -MOMENTUM_THRESHOLD_PCT and green and previous_red and closer_to_vwap):
            side = "SHORT"

        if side == "LONG":
            position = {"side": side, "time": timestamp, "price": bar["close"], "stop": bar["close"] - LONG_STOP, "target": bar["close"] + LONG_TARGET}
            trades_today += 1
        elif side == "SHORT":
            position = {"side": side, "time": timestamp, "price": bar["close"], "stop": bar["close"] + SHORT_STOP, "target": bar["close"] - SHORT_TARGET}
            trades_today += 1

    columns = [
        "entry_time", "entry_price", "exit_time", "exit_price", "side",
        "quantity", "pnl", "pnl_pct", "holding_minutes",
    ]
    return pd.DataFrame(trades, columns=columns)














#--------------------------strategy_vwap_touch_only---------------------------------------------------------------------------------



# """Drift VWAP — CASE 3: VWAP TOUCH ONLY.

# Signals are evaluated only at completed 5-minute candles.
# 15-minute VWAP is calculated from completed 15-minute candles.
# Positions are monitored against each underlying one-minute bar
# for stop/target exits.

# This file is a variant of the original Drift VWAP strategy.
# Only the VWAP-entry trigger is changed. All other filters,
# guardrails, stops, targets, and exit handling remain the same.
# """

# import pandas as pd

# from features.vwap import calculate_session_vwap, resample_ohlcv


# MAX_TRADES_PER_DAY = 4
# MAX_LOSSES_PER_DAY = 2
# MOMENTUM_BARS = 12
# MOMENTUM_THRESHOLD_PCT = 0.10
# NO_NEW_ENTRIES_FROM = "15:30"

# LONG_STOP, LONG_TARGET = 40.0, 80.0
# SHORT_STOP, SHORT_TARGET = 50.0, 80.0


# def configure(config):
#     """Load optional Drift-VWAP controls from backtest/config.py."""
#     global MAX_TRADES_PER_DAY, MAX_LOSSES_PER_DAY, MOMENTUM_BARS
#     global MOMENTUM_THRESHOLD_PCT, NO_NEW_ENTRIES_FROM
#     global LONG_STOP, LONG_TARGET, SHORT_STOP, SHORT_TARGET

#     MAX_TRADES_PER_DAY = int(
#         getattr(config, "MAX_TRADES_PER_DAY", MAX_TRADES_PER_DAY)
#     )
#     MAX_LOSSES_PER_DAY = int(
#         getattr(config, "MAX_LOSSES_PER_DAY", MAX_LOSSES_PER_DAY)
#     )
#     MOMENTUM_BARS = int(
#         getattr(config, "MOMENTUM_BARS", MOMENTUM_BARS)
#     )
#     MOMENTUM_THRESHOLD_PCT = float(
#         getattr(config, "MOMENTUM_THRESHOLD_PCT", MOMENTUM_THRESHOLD_PCT)
#     )
#     NO_NEW_ENTRIES_FROM = str(
#         getattr(config, "NO_NEW_ENTRIES_FROM", NO_NEW_ENTRIES_FROM)
#     )
#     LONG_STOP = float(
#         getattr(config, "LONG_STOP_POINTS", LONG_STOP)
#     )
#     LONG_TARGET = float(
#         getattr(config, "LONG_TARGET_POINTS", LONG_TARGET)
#     )
#     SHORT_STOP = float(
#         getattr(config, "SHORT_STOP_POINTS", SHORT_STOP)
#     )
#     SHORT_TARGET = float(
#         getattr(config, "SHORT_TARGET_POINTS", SHORT_TARGET)
#     )


# def _completed_bars(df, timeframe):
#     """Aggregate and label each candle with its actual final base-bar time."""
#     bars = resample_ohlcv(df, timeframe)

#     # resample_ohlcv labels [09:15, 09:20) at 09:20.
#     # It is actually known only after the 09:19 base bar closes.
#     bars.index = bars.index - pd.Timedelta(minutes=1)

#     bars["vwap"] = calculate_session_vwap(bars)
#     return bars


# def _trade(entry_time, entry_price, exit_time, exit_price, side, quantity):
#     direction = 1 if side == "LONG" else -1
#     pnl = (exit_price - entry_price) * direction * quantity

#     return {
#         "entry_time": entry_time,
#         "entry_price": entry_price,
#         "exit_time": exit_time,
#         "exit_price": exit_price,
#         "side": side,
#         "quantity": quantity,
#         "pnl": pnl,
#         "pnl_pct": (exit_price - entry_price) / entry_price * direction * 100,
#         "holding_minutes": (exit_time - entry_time).total_seconds() / 60,
#     }


# def generate_trades(df, quantity):
#     """
#     Generate trades using the selected VWAP-touch trigger.

#     IMPORTANT:
#     Everything except the VWAP trigger is kept the same as the
#     original strategy.
#     """

#     required = {"open", "high", "low", "close", "volume"}
#     missing = required.difference(df.columns)

#     if missing:
#         raise ValueError(
#             f"Drift VWAP strategy requires columns: {sorted(missing)}"
#         )

#     bars_5m = _completed_bars(df, "5m")
#     bars_15m = _completed_bars(df, "15m")

#     bars_15m["vwap_15m_ago"] = (
#         bars_15m.groupby(bars_15m.index.normalize())["vwap"].shift(1)
#     )

#     # At every completed 5m candle, use only the last completed 15m bar.
#     decision = pd.merge_asof(
#         bars_5m.reset_index()
#         .rename(columns={
#             bars_5m.index.name or "index": "timestamp"
#         })
#         .sort_values("timestamp"),

#         bars_15m[["vwap", "vwap_15m_ago"]]
#         .reset_index()
#         .rename(columns={
#             bars_15m.index.name or "index": "timestamp",
#             "vwap": "vwap15",
#         })
#         .sort_values("timestamp"),

#         on="timestamp",
#         direction="backward",
#     ).set_index("timestamp")

#     decision["previous_close"] = (
#         decision.groupby(decision.index.normalize())["close"].shift(1)
#     )
#     decision["previous_open"] = (
#         decision.groupby(decision.index.normalize())["open"].shift(1)
#     )
#     decision["close_1h_ago"] = (
#         decision.groupby(decision.index.normalize())["close"]
#         .shift(MOMENTUM_BARS)
#     )
#     decision["momentum_pct"] = (
#         decision["close"] / decision["close_1h_ago"] - 1
#     ) * 100

#     trades = []
#     position = None
#     trades_today = 0
#     losses_today = 0
#     current_session = None

#     final_times = set(
#         df.groupby(df.index.normalize()).tail(1).index
#     )

#     for timestamp, bar in df.iterrows():

#         session = timestamp.normalize()

#         if session != current_session:
#             current_session = session
#             trades_today = 0
#             losses_today = 0

#         # ----------------------------------------------------
#         # Existing position: monitor every 1m bar
#         # ----------------------------------------------------

#         if position is not None:

#             # Conservative rule:
#             # if one 1m bar hits both stop and target,
#             # assume stop happened first.
#             if (
#                 position["side"] == "LONG"
#                 and bar["low"] <= position["stop"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["stop"],
#                         "LONG",
#                         quantity,
#                     )
#                 )
#                 losses_today += 1
#                 position = None

#             elif (
#                 position is not None
#                 and position["side"] == "LONG"
#                 and bar["high"] >= position["target"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["target"],
#                         "LONG",
#                         quantity,
#                     )
#                 )
#                 position = None

#             elif (
#                 position is not None
#                 and position["side"] == "SHORT"
#                 and bar["high"] >= position["stop"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["stop"],
#                         "SHORT",
#                         quantity,
#                     )
#                 )
#                 losses_today += 1
#                 position = None

#             elif (
#                 position is not None
#                 and position["side"] == "SHORT"
#                 and bar["low"] <= position["target"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["target"],
#                         "SHORT",
#                         quantity,
#                     )
#                 )
#                 position = None

#         # End-of-session exit
#         if position is not None and timestamp in final_times:
#             trades.append(
#                 _trade(
#                     position["time"],
#                     position["price"],
#                     timestamp,
#                     bar["close"],
#                     position["side"],
#                     quantity,
#                 )
#             )
#             position = None
#             continue

#         if position is not None or timestamp not in decision.index:
#             continue

#         if timestamp.strftime("%H:%M") >= NO_NEW_ENTRIES_FROM:
#             continue

#         if (
#             trades_today >= MAX_TRADES_PER_DAY
#             or losses_today >= MAX_LOSSES_PER_DAY
#         ):
#             continue

#         signal = decision.loc[timestamp]

#         if pd.isna(
#             signal[
#                 [
#                     "vwap15",
#                     "vwap_15m_ago",
#                     "previous_close",
#                     "previous_open",
#                     "close_1h_ago",
#                 ]
#             ]
#         ).any():
#             continue

#         red = signal["close"] < signal["open"]
#         green = signal["close"] > signal["open"]

#         previous_red = (
#             signal["previous_close"] < signal["previous_open"]
#         )
#         previous_green = (
#             signal["previous_close"] > signal["previous_open"]
#         )

#         vwap = signal["vwap15"]

#         # ====================================================
#         # VWAP TOUCH CONDITIONS
#         # ====================================================
#         #
#         # These two booleans are intentionally defined
#         # differently in the two variants.
#         #
#         # LONG:
#         #   Candle low touches/crosses VWAP.
#         #
#         # SHORT:
#         #   Candle high touches/crosses VWAP.
#         #
#         # ====================================================

#         vwap_touched_long = signal["low"] <= vwap
#         vwap_touched_short = signal["high"] >= vwap

#         # ----------------------------------------------------
#         # CASE 3: VWAP TOUCH ONLY
#         # ----------------------------------------------------
#         #
#         # The candle must touch VWAP.
#         #
#         # The existing price-vs-VWAP conditions below are kept:
#         # LONG  -> close above VWAP
#         # SHORT -> close below VWAP
#         #
#         # There is NO additional rejection requirement here.
#         # ----------------------------------------------------

#         side = None

#         if (
#             signal["close"] > vwap
#             and signal["vwap15"] > signal["vwap_15m_ago"]
#             and signal["momentum_pct"] >= MOMENTUM_THRESHOLD_PCT
#             and red
#             and previous_green
#             and vwap_touched_long
#         ):
#             side = "LONG"

#         elif (
#             signal["close"] < vwap
#             and signal["vwap15"] < signal["vwap_15m_ago"]
#             and signal["momentum_pct"] <= -MOMENTUM_THRESHOLD_PCT
#             and green
#             and previous_red
#             and vwap_touched_short
#         ):
#             side = "SHORT"

#         if side == "LONG":
#             position = {
#                 "side": side,
#                 "time": timestamp,
#                 "price": bar["close"],
#                 "stop": bar["close"] - LONG_STOP,
#                 "target": bar["close"] + LONG_TARGET,
#             }
#             trades_today += 1

#         elif side == "SHORT":
#             position = {
#                 "side": side,
#                 "time": timestamp,
#                 "price": bar["close"],
#                 "stop": bar["close"] + SHORT_STOP,
#                 "target": bar["close"] - SHORT_TARGET,
#             }
#             trades_today += 1

#     columns = [
#         "entry_time",
#         "entry_price",
#         "exit_time",
#         "exit_price",
#         "side",
#         "quantity",
#         "pnl",
#         "pnl_pct",
#         "holding_minutes",
#     ]

#     return pd.DataFrame(trades, columns=columns)





# -----------------------------------------------------------strategy_vwap_touch_rejection-------------------------------------------------------



# """Drift VWAP — CASE 2: VWAP TOUCH THEN REJECTION.

# Signals are evaluated only at completed 5-minute candles.
# 15-minute VWAP is calculated from completed 15-minute candles.
# Positions are monitored against each underlying one-minute bar
# for stop/target exits.

# This file is a variant of the original Drift VWAP strategy.
# Only the VWAP-entry trigger is changed. All other filters,
# guardrails, stops, targets, and exit handling remain the same.
# """

# import pandas as pd

# from features.vwap import calculate_session_vwap, resample_ohlcv


# MAX_TRADES_PER_DAY = 4
# MAX_LOSSES_PER_DAY = 2
# MOMENTUM_BARS = 12
# MOMENTUM_THRESHOLD_PCT = 0.10
# NO_NEW_ENTRIES_FROM = "15:30"

# LONG_STOP, LONG_TARGET = 40.0, 80.0
# SHORT_STOP, SHORT_TARGET = 50.0, 80.0


# def configure(config):
#     """Load optional Drift-VWAP controls from backtest/config.py."""
#     global MAX_TRADES_PER_DAY, MAX_LOSSES_PER_DAY, MOMENTUM_BARS
#     global MOMENTUM_THRESHOLD_PCT, NO_NEW_ENTRIES_FROM
#     global LONG_STOP, LONG_TARGET, SHORT_STOP, SHORT_TARGET

#     MAX_TRADES_PER_DAY = int(
#         getattr(config, "MAX_TRADES_PER_DAY", MAX_TRADES_PER_DAY)
#     )
#     MAX_LOSSES_PER_DAY = int(
#         getattr(config, "MAX_LOSSES_PER_DAY", MAX_LOSSES_PER_DAY)
#     )
#     MOMENTUM_BARS = int(
#         getattr(config, "MOMENTUM_BARS", MOMENTUM_BARS)
#     )
#     MOMENTUM_THRESHOLD_PCT = float(
#         getattr(config, "MOMENTUM_THRESHOLD_PCT", MOMENTUM_THRESHOLD_PCT)
#     )
#     NO_NEW_ENTRIES_FROM = str(
#         getattr(config, "NO_NEW_ENTRIES_FROM", NO_NEW_ENTRIES_FROM)
#     )
#     LONG_STOP = float(
#         getattr(config, "LONG_STOP_POINTS", LONG_STOP)
#     )
#     LONG_TARGET = float(
#         getattr(config, "LONG_TARGET_POINTS", LONG_TARGET)
#     )
#     SHORT_STOP = float(
#         getattr(config, "SHORT_STOP_POINTS", SHORT_STOP)
#     )
#     SHORT_TARGET = float(
#         getattr(config, "SHORT_TARGET_POINTS", SHORT_TARGET)
#     )


# def _completed_bars(df, timeframe):
#     """Aggregate and label each candle with its actual final base-bar time."""
#     bars = resample_ohlcv(df, timeframe)

#     # resample_ohlcv labels [09:15, 09:20) at 09:20.
#     # It is actually known only after the 09:19 base bar closes.
#     bars.index = bars.index - pd.Timedelta(minutes=1)

#     bars["vwap"] = calculate_session_vwap(bars)
#     return bars


# def _trade(entry_time, entry_price, exit_time, exit_price, side, quantity):
#     direction = 1 if side == "LONG" else -1
#     pnl = (exit_price - entry_price) * direction * quantity

#     return {
#         "entry_time": entry_time,
#         "entry_price": entry_price,
#         "exit_time": exit_time,
#         "exit_price": exit_price,
#         "side": side,
#         "quantity": quantity,
#         "pnl": pnl,
#         "pnl_pct": (exit_price - entry_price) / entry_price * direction * 100,
#         "holding_minutes": (exit_time - entry_time).total_seconds() / 60,
#     }


# def generate_trades(df, quantity):
#     """
#     Generate trades using the selected VWAP-touch trigger.

#     IMPORTANT:
#     Everything except the VWAP trigger is kept the same as the
#     original strategy.
#     """

#     required = {"open", "high", "low", "close", "volume"}
#     missing = required.difference(df.columns)

#     if missing:
#         raise ValueError(
#             f"Drift VWAP strategy requires columns: {sorted(missing)}"
#         )

#     bars_5m = _completed_bars(df, "5m")
#     bars_15m = _completed_bars(df, "15m")

#     bars_15m["vwap_15m_ago"] = (
#         bars_15m.groupby(bars_15m.index.normalize())["vwap"].shift(1)
#     )

#     # At every completed 5m candle, use only the last completed 15m bar.
#     decision = pd.merge_asof(
#         bars_5m.reset_index()
#         .rename(columns={
#             bars_5m.index.name or "index": "timestamp"
#         })
#         .sort_values("timestamp"),

#         bars_15m[["vwap", "vwap_15m_ago"]]
#         .reset_index()
#         .rename(columns={
#             bars_15m.index.name or "index": "timestamp",
#             "vwap": "vwap15",
#         })
#         .sort_values("timestamp"),

#         on="timestamp",
#         direction="backward",
#     ).set_index("timestamp")

#     decision["previous_close"] = (
#         decision.groupby(decision.index.normalize())["close"].shift(1)
#     )
#     decision["previous_open"] = (
#         decision.groupby(decision.index.normalize())["open"].shift(1)
#     )
#     decision["close_1h_ago"] = (
#         decision.groupby(decision.index.normalize())["close"]
#         .shift(MOMENTUM_BARS)
#     )
#     decision["momentum_pct"] = (
#         decision["close"] / decision["close_1h_ago"] - 1
#     ) * 100

#     trades = []
#     position = None
#     trades_today = 0
#     losses_today = 0
#     current_session = None

#     final_times = set(
#         df.groupby(df.index.normalize()).tail(1).index
#     )

#     for timestamp, bar in df.iterrows():

#         session = timestamp.normalize()

#         if session != current_session:
#             current_session = session
#             trades_today = 0
#             losses_today = 0

#         # ----------------------------------------------------
#         # Existing position: monitor every 1m bar
#         # ----------------------------------------------------

#         if position is not None:

#             # Conservative rule:
#             # if one 1m bar hits both stop and target,
#             # assume stop happened first.
#             if (
#                 position["side"] == "LONG"
#                 and bar["low"] <= position["stop"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["stop"],
#                         "LONG",
#                         quantity,
#                     )
#                 )
#                 losses_today += 1
#                 position = None

#             elif (
#                 position is not None
#                 and position["side"] == "LONG"
#                 and bar["high"] >= position["target"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["target"],
#                         "LONG",
#                         quantity,
#                     )
#                 )
#                 position = None

#             elif (
#                 position is not None
#                 and position["side"] == "SHORT"
#                 and bar["high"] >= position["stop"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["stop"],
#                         "SHORT",
#                         quantity,
#                     )
#                 )
#                 losses_today += 1
#                 position = None

#             elif (
#                 position is not None
#                 and position["side"] == "SHORT"
#                 and bar["low"] <= position["target"]
#             ):
#                 trades.append(
#                     _trade(
#                         position["time"],
#                         position["price"],
#                         timestamp,
#                         position["target"],
#                         "SHORT",
#                         quantity,
#                     )
#                 )
#                 position = None

#         # End-of-session exit
#         if position is not None and timestamp in final_times:
#             trades.append(
#                 _trade(
#                     position["time"],
#                     position["price"],
#                     timestamp,
#                     bar["close"],
#                     position["side"],
#                     quantity,
#                 )
#             )
#             position = None
#             continue

#         if position is not None or timestamp not in decision.index:
#             continue

#         if timestamp.strftime("%H:%M") >= NO_NEW_ENTRIES_FROM:
#             continue

#         if (
#             trades_today >= MAX_TRADES_PER_DAY
#             or losses_today >= MAX_LOSSES_PER_DAY
#         ):
#             continue

#         signal = decision.loc[timestamp]

#         if pd.isna(
#             signal[
#                 [
#                     "vwap15",
#                     "vwap_15m_ago",
#                     "previous_close",
#                     "previous_open",
#                     "close_1h_ago",
#                 ]
#             ]
#         ).any():
#             continue

#         red = signal["close"] < signal["open"]
#         green = signal["close"] > signal["open"]

#         previous_red = (
#             signal["previous_close"] < signal["previous_open"]
#         )
#         previous_green = (
#             signal["previous_close"] > signal["previous_open"]
#         )

#         vwap = signal["vwap15"]

#         # ====================================================
#         # VWAP TOUCH CONDITIONS
#         # ====================================================
#         #
#         # These two booleans are intentionally defined
#         # differently in the two variants.
#         #
#         # LONG:
#         #   Candle low touches/crosses VWAP.
#         #
#         # SHORT:
#         #   Candle high touches/crosses VWAP.
#         #
#         # ====================================================

#         vwap_touched_long = signal["low"] <= vwap
#         vwap_touched_short = signal["high"] >= vwap

#         # ----------------------------------------------------
#         # CASE 2: VWAP TOUCH THEN REJECTION
#         # ----------------------------------------------------
#         #
#         # LONG:
#         #   Low touches/crosses VWAP
#         #   AND candle closes back ABOVE VWAP.
#         #
#         # SHORT:
#         #   High touches/crosses VWAP
#         #   AND candle closes back BELOW VWAP.
#         #
#         # This means the same completed 5m candle contains
#         # both the VWAP touch and the rejection.
#         #
#         # ----------------------------------------------------

#         vwap_rejection_long = (
#             vwap_touched_long
#             and signal["close"] > vwap
#         )

#         vwap_rejection_short = (
#             vwap_touched_short
#             and signal["close"] < vwap
#         )

#         side = None

#         if (
#             signal["close"] > vwap
#             and signal["vwap15"] > signal["vwap_15m_ago"]
#             and signal["momentum_pct"] >= MOMENTUM_THRESHOLD_PCT
#             and red
#             and previous_green
#             and vwap_rejection_long
#         ):
#             side = "LONG"

#         elif (
#             signal["close"] < vwap
#             and signal["vwap15"] < signal["vwap_15m_ago"]
#             and signal["momentum_pct"] <= -MOMENTUM_THRESHOLD_PCT
#             and green
#             and previous_red
#             and vwap_rejection_short
#         ):
#             side = "SHORT"

#         if side == "LONG":
#             position = {
#                 "side": side,
#                 "time": timestamp,
#                 "price": bar["close"],
#                 "stop": bar["close"] - LONG_STOP,
#                 "target": bar["close"] + LONG_TARGET,
#             }
#             trades_today += 1

#         elif side == "SHORT":
#             position = {
#                 "side": side,
#                 "time": timestamp,
#                 "price": bar["close"],
#                 "stop": bar["close"] + SHORT_STOP,
#                 "target": bar["close"] - SHORT_TARGET,
#             }
#             trades_today += 1

#     columns = [
#         "entry_time",
#         "entry_price",
#         "exit_time",
#         "exit_price",
#         "side",
#         "quantity",
#         "pnl",
#         "pnl_pct",
#         "holding_minutes",
#     ]

#     return pd.DataFrame(trades, columns=columns)




# -----------------------------------------------------------vwap hit --------------------------------------



# """Drift VWAP long/short strategy.

# Signals are evaluated only at completed 5-minute candles.  15-minute VWAP
# is calculated from completed 15-minute candles, so no higher-timeframe data
# from a still-forming candle is visible.  Positions are then monitored against
# each underlying one-minute bar for stop/target exits.

# The source pseudocode uses ET times, but this data is NSE/IST and ends near
# 15:30 IST.  The equivalent safe implementation blocks entries from 15:30 IST
# and closes at the final available bar of each session (there is no 15:55 IST
# bar in this dataset).

# Trigger condition: the first pullback candle (red for longs, green for
# shorts) whose high/low range actually touches or crosses the 15-minute VWAP
# level. This replaces a "closer to VWAP than the prior candle" proxy with a
# direct VWAP-touch check.
# """

# import pandas as pd

# from features.vwap import calculate_session_vwap, resample_ohlcv


# MAX_TRADES_PER_DAY = 4
# MAX_LOSSES_PER_DAY = 2
# MOMENTUM_BARS = 12             # 12 completed 5-minute candles = one hour
# MOMENTUM_THRESHOLD_PCT = 0.10
# NO_NEW_ENTRIES_FROM = "15:30"
# LONG_STOP, LONG_TARGET = 40.0, 80.0
# SHORT_STOP, SHORT_TARGET = 50.0, 80.0


# def configure(config):
#     """Load optional drift-VWAP controls from backtest/config.py."""
#     global MAX_TRADES_PER_DAY, MAX_LOSSES_PER_DAY, MOMENTUM_BARS
#     global MOMENTUM_THRESHOLD_PCT, NO_NEW_ENTRIES_FROM
#     global LONG_STOP, LONG_TARGET, SHORT_STOP, SHORT_TARGET
#     MAX_TRADES_PER_DAY = int(getattr(config, "MAX_TRADES_PER_DAY", MAX_TRADES_PER_DAY))
#     MAX_LOSSES_PER_DAY = int(getattr(config, "MAX_LOSSES_PER_DAY", MAX_LOSSES_PER_DAY))
#     MOMENTUM_BARS = int(getattr(config, "MOMENTUM_BARS", MOMENTUM_BARS))
#     MOMENTUM_THRESHOLD_PCT = float(getattr(config, "MOMENTUM_THRESHOLD_PCT", MOMENTUM_THRESHOLD_PCT))
#     NO_NEW_ENTRIES_FROM = str(getattr(config, "NO_NEW_ENTRIES_FROM", NO_NEW_ENTRIES_FROM))
#     LONG_STOP = float(getattr(config, "LONG_STOP_POINTS", LONG_STOP))
#     LONG_TARGET = float(getattr(config, "LONG_TARGET_POINTS", LONG_TARGET))
#     SHORT_STOP = float(getattr(config, "SHORT_STOP_POINTS", SHORT_STOP))
#     SHORT_TARGET = float(getattr(config, "SHORT_TARGET_POINTS", SHORT_TARGET))


# def _completed_bars(df, timeframe):
#     """Aggregate and label each candle with its actual final base-bar time."""
#     bars = resample_ohlcv(df, timeframe)
#     # resample_ohlcv labels [09:15, 09:20) at 09:20.  It is actually known
#     # only after the 09:19 base bar closes, so make that availability explicit.
#     bars.index = bars.index - pd.Timedelta(minutes=1)
#     bars["vwap"] = calculate_session_vwap(bars)
#     return bars


# def _trade(entry_time, entry_price, exit_time, exit_price, side, quantity):
#     direction = 1 if side == "LONG" else -1
#     pnl = (exit_price - entry_price) * direction * quantity
#     return {
#         "entry_time": entry_time,
#         "entry_price": entry_price,
#         "exit_time": exit_time,
#         "exit_price": exit_price,
#         "side": side,
#         "quantity": quantity,
#         "pnl": pnl,
#         "pnl_pct": (exit_price - entry_price) / entry_price * direction * 100,
#         "holding_minutes": (exit_time - entry_time).total_seconds() / 60,
#     }


# def generate_trades(df, quantity):
#     """Return a trade ledger following the supplied drift-VWAP pseudocode."""
#     required = {"open", "high", "low", "close", "volume"}
#     missing = required.difference(df.columns)
#     if missing:
#         raise ValueError(f"Drift VWAP strategy requires columns: {sorted(missing)}")

#     bars_5m = _completed_bars(df, "5m")
#     bars_15m = _completed_bars(df, "15m")
#     bars_15m["vwap_15m_ago"] = bars_15m.groupby(bars_15m.index.normalize())["vwap"].shift(1)

#     # At every completed 5m candle, use only the last completed 15m bar.
#     decision = pd.merge_asof(
#         bars_5m.reset_index().rename(columns={bars_5m.index.name or "index": "timestamp"}).sort_values("timestamp"),
#         bars_15m[["vwap", "vwap_15m_ago"]].reset_index().rename(
#             columns={bars_15m.index.name or "index": "timestamp", "vwap": "vwap15"}
#         ).sort_values("timestamp"),
#         on="timestamp", direction="backward",
#     ).set_index("timestamp")
#     decision["close_1h_ago"] = decision.groupby(decision.index.normalize())["close"].shift(MOMENTUM_BARS)
#     decision["momentum_pct"] = (decision["close"] / decision["close_1h_ago"] - 1) * 100
#     # VWAP-touch trigger: does this 5m candle's range cross/reach the 15m VWAP level?
#     decision["vwap_hit"] = (decision["low"] <= decision["vwap15"]) & (decision["vwap15"] <= decision["high"])

#     trades, position = [], None
#     trades_today = losses_today = 0
#     current_session = None
#     final_times = set(df.groupby(df.index.normalize()).tail(1).index)

#     for timestamp, bar in df.iterrows():
#         session = timestamp.normalize()
#         if session != current_session:
#             current_session, trades_today, losses_today = session, 0, 0

#         # Monitor every one-minute OHLC bar.  If a bar spans both levels, use
#         # the stop first: without tick ordering, this is the conservative fill.
#         if position is not None:
#             if position["side"] == "LONG" and bar["low"] <= position["stop"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["stop"], "LONG", quantity))
#                 losses_today += 1
#                 position = None
#             elif position is not None and position["side"] == "LONG" and bar["high"] >= position["target"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["target"], "LONG", quantity))
#                 position = None
#             elif position is not None and position["side"] == "SHORT" and bar["high"] >= position["stop"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["stop"], "SHORT", quantity))
#                 losses_today += 1
#                 position = None
#             elif position is not None and position["side"] == "SHORT" and bar["low"] <= position["target"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["target"], "SHORT", quantity))
#                 position = None

#         if position is not None and timestamp in final_times:
#             trades.append(_trade(position["time"], position["price"], timestamp, bar["close"], position["side"], quantity))
#             position = None
#             continue

#         if position is not None or timestamp not in decision.index:
#             continue
#         if timestamp.strftime("%H:%M") >= NO_NEW_ENTRIES_FROM:
#             continue
#         if trades_today >= MAX_TRADES_PER_DAY or losses_today >= MAX_LOSSES_PER_DAY:
#             continue

#         signal = decision.loc[timestamp]
#         if pd.isna(signal[["vwap15", "vwap_15m_ago", "close_1h_ago"]]).any():
#             continue
#         red, green = signal["close"] < signal["open"], signal["close"] > signal["open"]
#         vwap_hit = bool(signal["vwap_hit"])

#         side = None
#         if (signal["close"] > signal["vwap15"] and signal["vwap15"] > signal["vwap_15m_ago"]
#                 and signal["momentum_pct"] >= MOMENTUM_THRESHOLD_PCT and red and vwap_hit):
#             side = "LONG"
#         elif (signal["close"] < signal["vwap15"] and signal["vwap15"] < signal["vwap_15m_ago"]
#                 and signal["momentum_pct"] <= -MOMENTUM_THRESHOLD_PCT and green and vwap_hit):
#             side = "SHORT"

#         if side == "LONG":
#             position = {"side": side, "time": timestamp, "price": bar["close"], "stop": bar["close"] - LONG_STOP, "target": bar["close"] + LONG_TARGET}
#             trades_today += 1
#         elif side == "SHORT":
#             position = {"side": side, "time": timestamp, "price": bar["close"], "stop": bar["close"] + SHORT_STOP, "target": bar["close"] - SHORT_TARGET}
#             trades_today += 1

#     columns = [
#         "entry_time", "entry_price", "exit_time", "exit_price", "side",
#         "quantity", "pnl", "pnl_pct", "holding_minutes",
#     ]
#     return pd.DataFrame(trades, columns=columns)







# ---------------------------------------------------------------entry at 1m candel close after vwap is hit -------------------------------------------------





# """Drift VWAP long/short strategy.

# The 15-minute VWAP is calculated from completed 15-minute candles. The
# 5-minute trend/momentum filters use completed 5-minute candles. When the
# current 1-minute candle touches the 15-minute VWAP, the strategy evaluates
# that 1-minute candle at its close and enters at that 1-minute close if the
# entry conditions are satisfied. Positions are then monitored against each
# underlying one-minute bar for stop/target exits.

# The source pseudocode uses ET times, but this data is NSE/IST and ends near
# 15:30 IST.  The equivalent safe implementation blocks entries from 15:30 IST
# and closes at the final available bar of each session (there is no 15:55 IST
# bar in this dataset).

# Trigger condition: the first 1-minute candle whose high/low range actually
# touches or crosses the completed 15-minute VWAP level. The trade is entered
# at the close of that 1-minute candle, provided the completed 5-minute
# trend/momentum filters and the 1-minute direction condition are satisfied.
# """

# import pandas as pd

# from features.vwap import calculate_session_vwap, resample_ohlcv


# MAX_TRADES_PER_DAY = 4
# MAX_LOSSES_PER_DAY = 2
# MOMENTUM_BARS = 12             # 12 completed 5-minute candles = one hour
# MOMENTUM_THRESHOLD_PCT = 0.10
# NO_NEW_ENTRIES_FROM = "15:30"
# LONG_STOP, LONG_TARGET = 40.0, 80.0
# SHORT_STOP, SHORT_TARGET = 50.0, 80.0


# def configure(config):
#     """Load optional drift-VWAP controls from backtest/config.py."""
#     global MAX_TRADES_PER_DAY, MAX_LOSSES_PER_DAY, MOMENTUM_BARS
#     global MOMENTUM_THRESHOLD_PCT, NO_NEW_ENTRIES_FROM
#     global LONG_STOP, LONG_TARGET, SHORT_STOP, SHORT_TARGET
#     MAX_TRADES_PER_DAY = int(getattr(config, "MAX_TRADES_PER_DAY", MAX_TRADES_PER_DAY))
#     MAX_LOSSES_PER_DAY = int(getattr(config, "MAX_LOSSES_PER_DAY", MAX_LOSSES_PER_DAY))
#     MOMENTUM_BARS = int(getattr(config, "MOMENTUM_BARS", MOMENTUM_BARS))
#     MOMENTUM_THRESHOLD_PCT = float(getattr(config, "MOMENTUM_THRESHOLD_PCT", MOMENTUM_THRESHOLD_PCT))
#     NO_NEW_ENTRIES_FROM = str(getattr(config, "NO_NEW_ENTRIES_FROM", NO_NEW_ENTRIES_FROM))
#     LONG_STOP = float(getattr(config, "LONG_STOP_POINTS", LONG_STOP))
#     LONG_TARGET = float(getattr(config, "LONG_TARGET_POINTS", LONG_TARGET))
#     SHORT_STOP = float(getattr(config, "SHORT_STOP_POINTS", SHORT_STOP))
#     SHORT_TARGET = float(getattr(config, "SHORT_TARGET_POINTS", SHORT_TARGET))


# def _completed_bars(df, timeframe):
#     """Aggregate and label each candle with its actual final base-bar time."""
#     bars = resample_ohlcv(df, timeframe)
#     # resample_ohlcv labels [09:15, 09:20) at 09:20.  It is actually known
#     # only after the 09:19 base bar closes, so make that availability explicit.
#     bars.index = bars.index - pd.Timedelta(minutes=1)
#     bars["vwap"] = calculate_session_vwap(bars)
#     return bars


# def _trade(entry_time, entry_price, exit_time, exit_price, side, quantity):
#     direction = 1 if side == "LONG" else -1
#     pnl = (exit_price - entry_price) * direction * quantity
#     return {
#         "entry_time": entry_time,
#         "entry_price": entry_price,
#         "exit_time": exit_time,
#         "exit_price": exit_price,
#         "side": side,
#         "quantity": quantity,
#         "pnl": pnl,
#         "pnl_pct": (exit_price - entry_price) / entry_price * direction * 100,
#         "holding_minutes": (exit_time - entry_time).total_seconds() / 60,
#     }


# def generate_trades(df, quantity):
#     """Return a trade ledger following the supplied drift-VWAP pseudocode."""
#     required = {"open", "high", "low", "close", "volume"}
#     missing = required.difference(df.columns)
#     if missing:
#         raise ValueError(f"Drift VWAP strategy requires columns: {sorted(missing)}")

#     bars_5m = _completed_bars(df, "5m")
#     bars_15m = _completed_bars(df, "15m")
#     bars_15m["vwap_15m_ago"] = bars_15m.groupby(bars_15m.index.normalize())["vwap"].shift(1)

#     # At every completed 5m candle, use only the last completed 15m bar.
#     decision_5m = pd.merge_asof(
#         bars_5m.reset_index().rename(columns={bars_5m.index.name or "index": "timestamp"}).sort_values("timestamp"),
#         bars_15m[["vwap", "vwap_15m_ago"]].reset_index().rename(
#             columns={bars_15m.index.name or "index": "timestamp", "vwap": "vwap15"}
#         ).sort_values("timestamp"),
#         on="timestamp", direction="backward",
#     ).set_index("timestamp")

#     decision_5m["close_1h_ago"] = (
#         decision_5m.groupby(decision_5m.index.normalize())["close"]
#         .shift(MOMENTUM_BARS)
#     )
#     decision_5m["momentum_pct"] = (
#         decision_5m["close"] / decision_5m["close_1h_ago"] - 1
#     ) * 100

#     # Map each 1m candle to the most recent completed 5m candle. At the
#     # exact close of a 5m candle, that 5m candle is available; before it
#     # closes, only the previous completed 5m candle is used.
#     decision = pd.merge_asof(
#         df.reset_index().rename(
#             columns={df.index.name or "index": "timestamp"}
#         ).sort_values("timestamp"),
#         decision_5m[["vwap15", "vwap_15m_ago", "momentum_pct"]]
#         .reset_index()
#         .rename(columns={"timestamp": "decision_time"})
#         .sort_values("decision_time"),
#         left_on="timestamp",
#         right_on="decision_time",
#         direction="backward",
#     ).set_index("timestamp")

#     # The CURRENT 1m candle must actually touch/cross the completed 15m VWAP.
#     decision["vwap_hit"] = (
#         (decision["low"] <= decision["vwap15"])
#         & (decision["vwap15"] <= decision["high"])
#     )

#     trades, position = [], None
#     trades_today = losses_today = 0
#     current_session = None
#     final_times = set(df.groupby(df.index.normalize()).tail(1).index)

#     for timestamp, bar in df.iterrows():
#         session = timestamp.normalize()
#         if session != current_session:
#             current_session, trades_today, losses_today = session, 0, 0

#         # Monitor every one-minute OHLC bar.  If a bar spans both levels, use
#         # the stop first: without tick ordering, this is the conservative fill.
#         if position is not None:
#             if position["side"] == "LONG" and bar["low"] <= position["stop"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["stop"], "LONG", quantity))
#                 losses_today += 1
#                 position = None
#             elif position is not None and position["side"] == "LONG" and bar["high"] >= position["target"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["target"], "LONG", quantity))
#                 position = None
#             elif position is not None and position["side"] == "SHORT" and bar["high"] >= position["stop"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["stop"], "SHORT", quantity))
#                 losses_today += 1
#                 position = None
#             elif position is not None and position["side"] == "SHORT" and bar["low"] <= position["target"]:
#                 trades.append(_trade(position["time"], position["price"], timestamp, position["target"], "SHORT", quantity))
#                 position = None

#         if position is not None and timestamp in final_times:
#             trades.append(_trade(position["time"], position["price"], timestamp, bar["close"], position["side"], quantity))
#             position = None
#             continue

#         if position is not None or timestamp not in decision.index:
#             continue
#         if timestamp.strftime("%H:%M") >= NO_NEW_ENTRIES_FROM:
#             continue
#         if trades_today >= MAX_TRADES_PER_DAY or losses_today >= MAX_LOSSES_PER_DAY:
#             continue

#         signal = decision.loc[timestamp]
#         if pd.isna(signal[["vwap15", "vwap_15m_ago", "momentum_pct"]]).any():
#             continue

#         # The touch is detected on THIS 1m candle.
#         vwap_hit = bool(signal["vwap_hit"])
#         if not vwap_hit:
#             continue

#         vwap15 = float(signal["vwap15"])
#         vwap15_ago = float(signal["vwap_15m_ago"])
#         momentum_pct = float(signal["momentum_pct"])

#         # Direction is based on the 1m candle that actually touched VWAP,
#         # because the entry occurs at this 1m candle's close.
#         red_1m = bar["close"] < bar["open"]
#         green_1m = bar["close"] > bar["open"]

#         side = None
#         if (bar["close"] > vwap15 and vwap15 > vwap15_ago
#                 and momentum_pct >= MOMENTUM_THRESHOLD_PCT and red_1m):
#             side = "LONG"
#         elif (bar["close"] < vwap15 and vwap15 < vwap15_ago
#                 and momentum_pct <= -MOMENTUM_THRESHOLD_PCT and green_1m):
#             side = "SHORT"

#         # Enter at the close of the 1m candle that touched VWAP.
#         if side == "LONG":
#             position = {
#                 "side": side,
#                 "time": timestamp,
#                 "price": bar["close"],
#                 "stop": bar["close"] - LONG_STOP,
#                 "target": bar["close"] + LONG_TARGET,
#             }
#             trades_today += 1
#         elif side == "SHORT":
#             position = {
#                 "side": side,
#                 "time": timestamp,
#                 "price": bar["close"],
#                 "stop": bar["close"] + SHORT_STOP,
#                 "target": bar["close"] - SHORT_TARGET,
#             }
#             trades_today += 1

#     columns = [
#         "entry_time", "entry_price", "exit_time", "exit_price", "side",
#         "quantity", "pnl", "pnl_pct", "holding_minutes",
#     ]
#     return pd.DataFrame(trades, columns=columns)
