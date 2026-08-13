"""
Backtest engine - everything that is common across strategies:

    Data -> VWAP -> Strategy -> Trades -> P&L -> Statistics

This file has NO strategy-specific logic. It:
  1. Reads backtest/config.py to know which strategy + data to use.
  2. Loads clean OHLCV data via data_loader.get_data().
  3. Computes the VWAP feature via features.vwap.add_vwap() (derived only,
     the underlying Parquet is untouched).
  4. Calls the strategy's generate_target_position(df) to get a 0/1
     target-position series.
  5. Turns changes in target position into a trade list.
  6. Computes P&L per trade and a simple equity curve.
  7. Computes summary statistics.
  8. Writes trades.csv, equity_curve.csv and stats.csv into
     backtest/<STRATEGY>/reports/.

Usage:
    python main.py                     # uses backtest/config.py as-is
    python main.py --strategy other_strategy_name
"""

import argparse
import importlib.util
import os
import sys

import pandas as pd

THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../backtest
ROOT_DIR = os.path.dirname(THIS_DIR)                     # .../data_pipeline

# Root-level modules (data_loader.py, features/vwap.py) live one level up.
sys.path.insert(0, ROOT_DIR)

import data_loader                 # noqa: E402
from features.vwap import add_vwap  # noqa: E402


def load_module(module_name, file_path):
    """Load a .py file as a module by path, sidestepping any package/
    sys.path naming collisions (e.g. two different 'config.py' files)."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# STEP: DATA -> VWAP
# ============================================================

def load_data_with_vwap(symbol, interval, start_date, end_date):
    ohlcv = data_loader.get_data(symbol, interval, start_date, end_date)
    return add_vwap(ohlcv)


# ============================================================
# STEP: STRATEGY -> TRADES
# ============================================================

def build_trades(df, target_position, quantity):
    """
    Turn a target-position series (0 = flat, 1 = long) into a list of
    round-trip trades, filled at the bar's close price. Extremely simple
    on purpose: no slippage, no commission, one position at a time.
    """
    trades = []
    open_trade = None
    prev_position = 0

    for ts, position in target_position.items():
        if position == prev_position:
            continue

        price = df.at[ts, "close"]

        # Close whatever was open before opening/flattening to the new target.
        if prev_position != 0 and open_trade is not None:
            open_trade["exit_time"] = ts
            open_trade["exit_price"] = price
            trades.append(open_trade)
            open_trade = None

        if position != 0:
            open_trade = {
                "entry_time": ts,
                "entry_price": price,
                "side": "LONG" if position > 0 else "SHORT",
                "quantity": quantity * abs(position),
            }

        prev_position = position

    # Defensive: if the strategy left a position open at the end of the
    # data, close it at the final bar so P&L is never left dangling.
    if open_trade is not None:
        last_ts = df.index[-1]
        open_trade["exit_time"] = last_ts
        open_trade["exit_price"] = df.at[last_ts, "close"]
        trades.append(open_trade)

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return trades_df

    direction = trades_df["side"].map({"LONG": 1, "SHORT": -1})
    trades_df["pnl"] = (
        (trades_df["exit_price"] - trades_df["entry_price"])
        * direction
        * trades_df["quantity"]
    )
    trades_df["pnl_pct"] = (
        (trades_df["exit_price"] - trades_df["entry_price"])
        / trades_df["entry_price"]
        * direction
        * 100
    )
    trades_df["holding_minutes"] = (
        trades_df["exit_time"] - trades_df["entry_time"]
    ).dt.total_seconds() / 60

    return trades_df[[
        "entry_time", "entry_price", "exit_time", "exit_price",
        "side", "quantity", "pnl", "pnl_pct", "holding_minutes",
    ]]


# ============================================================
# STEP: TRADES -> P&L (equity curve)
# ============================================================

def build_equity_curve(trades_df, initial_capital):
    if trades_df.empty:
        return pd.DataFrame(columns=["timestamp", "trade_pnl", "cumulative_pnl", "equity"])

    curve = trades_df[["exit_time", "pnl"]].rename(columns={"exit_time": "timestamp", "pnl": "trade_pnl"})
    curve["cumulative_pnl"] = curve["trade_pnl"].cumsum()
    curve["equity"] = initial_capital + curve["cumulative_pnl"]
    return curve


# ============================================================
# STEP: P&L -> STATISTICS
# ============================================================

def compute_statistics(trades_df, equity_curve, initial_capital):
    if trades_df.empty:
        return {"total_trades": 0, "note": "No trades were generated."}

    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]

    running_max = equity_curve["equity"].cummax()
    drawdown = equity_curve["equity"] - running_max
    max_drawdown = drawdown.min() if not drawdown.empty else 0.0

    total_pnl = trades_df["pnl"].sum()
    final_equity = initial_capital + total_pnl

    stats = {
        "total_trades": len(trades_df),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round(len(wins) / len(trades_df) * 100, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(trades_df["pnl"].mean(), 2),
        "avg_win": round(wins["pnl"].mean(), 2) if not wins.empty else 0.0,
        "avg_loss": round(losses["pnl"].mean(), 2) if not losses.empty else 0.0,
        "profit_factor": (
            round(wins["pnl"].sum() / abs(losses["pnl"].sum()), 2)
            if not losses.empty and losses["pnl"].sum() != 0 else float("inf")
        ),
        "best_trade": round(trades_df["pnl"].max(), 2),
        "worst_trade": round(trades_df["pnl"].min(), 2),
        "avg_holding_minutes": round(trades_df["holding_minutes"].mean(), 1),
        "max_drawdown": round(max_drawdown, 2),
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "return_pct": round(total_pnl / initial_capital * 100, 2),
    }
    return stats


# ============================================================
# REPORTS
# ============================================================

def write_reports(strategy_dir, trades_df, equity_curve, stats):
    reports_dir = os.path.join(strategy_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    trades_path = os.path.join(reports_dir, "trades.csv")
    equity_path = os.path.join(reports_dir, "equity_curve.csv")
    stats_path = os.path.join(reports_dir, "stats.csv")

    trades_df.to_csv(trades_path, index=False)
    equity_curve.to_csv(equity_path, index=False)
    pd.DataFrame([stats]).to_csv(stats_path, index=False)

    return trades_path, equity_path, stats_path


# ============================================================
# MAIN
# ============================================================

def run(strategy_name=None):
    bt_config = load_module("bt_config", os.path.join(THIS_DIR, "config.py"))
    strategy_name = strategy_name or bt_config.STRATEGY

    strategy_dir = os.path.join(THIS_DIR, strategy_name)
    strategy_path = os.path.join(strategy_dir, "strategy.py")
    if not os.path.exists(strategy_path):
        print(f"No strategy.py found at {strategy_path}")
        return 1

    strategy = load_module("bt_strategy", strategy_path)

    print("=" * 70)
    print(f"BACKTEST: {strategy_name}  |  {bt_config.SYMBOL} / {bt_config.INTERVAL}  "
          f"|  {bt_config.START_DATE} -> {bt_config.END_DATE}")
    print("=" * 70)

    # Data -> VWAP
    df = load_data_with_vwap(bt_config.SYMBOL, bt_config.INTERVAL,
                              bt_config.START_DATE, bt_config.END_DATE)
    print(f"Loaded {len(df)} bars")

    # Strategy
    target_position = strategy.generate_target_position(df)

    # Trades
    trades_df = build_trades(df, target_position, bt_config.QUANTITY)
    print(f"Generated {len(trades_df)} trade(s)")

    # P&L
    equity_curve = build_equity_curve(trades_df, bt_config.INITIAL_CAPITAL)

    # Statistics
    stats = compute_statistics(trades_df, equity_curve, bt_config.INITIAL_CAPITAL)

    # Reports
    trades_path, equity_path, stats_path = write_reports(strategy_dir, trades_df, equity_curve, stats)

    print("-" * 70)
    for key, value in stats.items():
        print(f"{key:>20}: {value}")
    print("-" * 70)
    print(f"Trades       -> {trades_path}")
    print(f"Equity curve -> {equity_path}")
    print(f"Stats        -> {stats_path}")
    print("=" * 70)

    return 0


def main():
    parser = argparse.ArgumentParser(description="Run the backtest for the strategy set in backtest/config.py")
    parser.add_argument("--strategy", default=None,
                         help="Override STRATEGY from config.py (folder name under backtest/)")
    args = parser.parse_args()

    sys.exit(run(args.strategy))


if __name__ == "__main__":
    main()
