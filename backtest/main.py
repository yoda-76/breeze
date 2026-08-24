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
import html
import importlib.util
import os
import sys

import pandas as pd

THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../backtest
ROOT_DIR = os.path.dirname(THIS_DIR)                     # .../data_pipeline

# Root-level modules (data_loader.py, features/vwap.py) live one level up.
sys.path.insert(0, ROOT_DIR)

import data_loader                 # noqa: E402
from features.vwap import add_multi_timeframe_vwap  # noqa: E402


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

def load_data_with_vwap(symbol, interval, start_date, end_date, timeframes):
    ohlcv = data_loader.get_data(symbol, interval, start_date, end_date)
    return add_multi_timeframe_vwap(ohlcv, timeframes)


def compute_vwap_accuracy(df, timeframes):
    """Measure whether above/below-VWAP agrees with the next bar direction.

    VWAP is a benchmark, not an inherently predictive indicator.  This report
    makes the chosen, testable interpretation of "accuracy" explicit and
    excludes overnight transitions.
    """
    next_close = df["close"].shift(-1)
    same_session = pd.Series(df.index.normalize(), index=df.index).eq(
        pd.Series(df.index.normalize(), index=df.index).shift(-1)
    )
    forward_return_pct = (next_close / df["close"] - 1) * 100
    actual_up = forward_return_pct > 0
    rows = []
    for timeframe in timeframes:
        column = "vwap" if timeframe == "1m" else f"vwap_{timeframe}"
        if column not in df:
            continue
        signal_up = df["close"] > df[column]
        valid = same_session & df[column].notna() & forward_return_pct.notna() & (forward_return_pct != 0)
        observations = int(valid.sum())
        correct = int((signal_up[valid] == actual_up[valid]).sum())
        rows.append({
            "timeframe": timeframe,
            "feature": column,
            "observations": observations,
            "correct_direction": correct,
            "directional_accuracy_pct": round(correct / observations * 100, 2) if observations else None,
            "mean_next_bar_return_pct": round(forward_return_pct[valid].mean(), 5) if observations else None,
        })
    return pd.DataFrame(rows)


def write_vwap_chart(df, timeframes, output_path):
    """Write a dependency-free SVG chart for the final session in the run."""
    session = df.index.normalize().max()
    chart = df[df.index.normalize() == session]
    columns = ["close", "vwap"] + [f"vwap_{tf}" for tf in timeframes if tf != "1m" and f"vwap_{tf}" in df]
    chart = chart[columns].dropna(how="all")
    if chart.empty:
        return None

    width, height, pad = 1400, 700, 65
    values = chart.to_numpy(dtype=float)
    low, high = float(pd.Series(values.ravel()).min()), float(pd.Series(values.ravel()).max())
    if high == low:
        high += 1
        low -= 1
    x = lambda i: pad + i * (width - 2 * pad) / max(len(chart) - 1, 1)
    y = lambda value: height - pad - (value - low) * (height - 2 * pad) / (high - low)
    colours = {"close": "#111827", "vwap": "#dc2626", "vwap_5m": "#2563eb", "vwap_15m": "#16a34a", "vwap_30m": "#9333ea"}
    paths = []
    legend = []
    for column in columns:
        series = chart[column]
        points = [f"{x(i):.1f},{y(value):.1f}" for i, value in enumerate(series) if pd.notna(value)]
        colour = colours.get(column, "#6b7280")
        if points:
            paths.append(f'<polyline fill="none" stroke="{colour}" stroke-width="2" points="{" ".join(points)}"/>')
            legend.append(f'<span style="color:{colour}">&#9632;</span> {html.escape(column)}')
    title = f"VWAP multi-timeframe chart — {session.date()}"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/><text x="{pad}" y="30" font-family="Arial" font-size="20">{title}</text>
<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#9ca3af"/><line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#9ca3af"/>
<text x="8" y="{pad}" font-family="Arial" font-size="12">{high:.2f}</text><text x="8" y="{height-pad}" font-family="Arial" font-size="12">{low:.2f}</text>{''.join(paths)}
</svg>'''
    with open(output_path, "w", encoding="utf-8") as file:
        file.write(svg)
    return output_path


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


def validate_trades(trades_df):
    """Validate the optional strategy-owned trade ledger before reporting it."""
    required = {
        "entry_time", "entry_price", "exit_time", "exit_price",
        "side", "quantity", "pnl", "pnl_pct", "holding_minutes",
    }
    missing = required.difference(trades_df.columns)
    if missing:
        raise ValueError(f"Custom trades are missing required columns: {sorted(missing)}")
    if not trades_df.empty and not trades_df["side"].isin(["LONG", "SHORT"]).all():
        raise ValueError("Custom trades must use LONG or SHORT sides.")
    return trades_df[[
        "entry_time", "entry_price", "exit_time", "exit_price",
        "side", "quantity", "pnl", "pnl_pct", "holding_minutes",
    ]].copy()


def apply_transaction_costs(trades_df, cost_per_trade, slippage_per_share=0.0):
    """Apply one fixed round-trip cost to every completed trade.

    ``cost_per_trade`` covers both entry and exit.  Gross P&L is retained in
    the report so the effect of costs stays transparent; all engine metrics
    subsequently use net ``pnl``.
    """
    result = trades_df.copy()
    if result.empty:
        result["gross_pnl"] = pd.Series(dtype=float)
        result["transaction_cost"] = pd.Series(dtype=float)
        result["slippage_cost"] = pd.Series(dtype=float)
        return result
    result["gross_pnl"] = result["pnl"]
    result["transaction_cost"] = float(cost_per_trade)
    # Entry and exit each incur the configured adverse price movement.
    result["slippage_cost"] = 2 * result["quantity"] * float(slippage_per_share)
    result["pnl"] = result["gross_pnl"] - result["transaction_cost"] - result["slippage_cost"]
    notional = result["entry_price"] * result["quantity"]
    result["pnl_pct"] = result["pnl"] / notional * 100
    return result


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

def write_reports(strategy_dir, trades_df, equity_curve, stats, df, accuracy, timeframes, report_name=None):
    reports_dir = os.path.join(strategy_dir, "reports")
    if report_name:
        if os.path.basename(report_name) != report_name:
            raise ValueError("REPORT_NAME must be a single folder name.")
        reports_dir = os.path.join(reports_dir, report_name)
    os.makedirs(reports_dir, exist_ok=True)

    trades_path = os.path.join(reports_dir, "trades.csv")
    equity_path = os.path.join(reports_dir, "equity_curve.csv")
    stats_path = os.path.join(reports_dir, "stats.csv")
    features_path = os.path.join(reports_dir, "features.csv")
    accuracy_path = os.path.join(reports_dir, "vwap_accuracy.csv")
    chart_path = os.path.join(reports_dir, "vwap_chart.svg")

    trades_df.to_csv(trades_path, index=False)
    equity_curve.to_csv(equity_path, index=False)
    pd.DataFrame([stats]).to_csv(stats_path, index=False)
    df.to_csv(features_path, index_label="timestamp")
    accuracy.to_csv(accuracy_path, index=False)
    write_vwap_chart(df, timeframes, chart_path)

    return trades_path, equity_path, stats_path, features_path, accuracy_path, chart_path


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
    timeframes = tuple(getattr(bt_config, "TIMEFRAMES", (bt_config.INTERVAL, "5m", "15m", "30m")))
    df = load_data_with_vwap(bt_config.SYMBOL, bt_config.INTERVAL,
                              bt_config.START_DATE, bt_config.END_DATE, timeframes)
    print(f"Loaded {len(df)} bars")
    print(f"Features: {', '.join(column for column in df if column.startswith('vwap'))}")

    # Strategy.  Most strategies use the simple target-position contract.
    # Strategies with intrabar execution (stops/targets) may instead expose
    # generate_trades(df, quantity), which returns the standard trade ledger.
    if hasattr(strategy, "configure"):
        strategy.configure(bt_config)
    if hasattr(strategy, "generate_trades"):
        trades_df = validate_trades(strategy.generate_trades(df, bt_config.QUANTITY))
    else:
        target_position = strategy.generate_target_position(df)
        trades_df = build_trades(df, target_position, bt_config.QUANTITY)
    print(f"Generated {len(trades_df)} trade(s)")

    transaction_cost = float(getattr(bt_config, "TRANSACTION_COST_PER_TRADE", 0.0))
    slippage = float(getattr(bt_config, "SLIPPAGE_PER_SHARE", 0.0))
    trades_df = apply_transaction_costs(trades_df, transaction_cost, slippage)

    # P&L
    equity_curve = build_equity_curve(trades_df, bt_config.INITIAL_CAPITAL)

    # Statistics
    stats = compute_statistics(trades_df, equity_curve, bt_config.INITIAL_CAPITAL)
    stats["transaction_cost_per_trade"] = transaction_cost
    stats["total_transaction_cost"] = round(trades_df["transaction_cost"].sum(), 2)
    stats["slippage_per_share_per_fill"] = slippage
    stats["total_slippage_cost"] = round(trades_df["slippage_cost"].sum(), 2)
    stats["gross_pnl"] = round(trades_df["gross_pnl"].sum(), 2)
    accuracy = compute_vwap_accuracy(df, timeframes)

        # also add sl, tp, position size, 
        #also add a indicator that shows how much the pnl went into profit before the sl was hit, and how much the pnl went into loss before the tp was hit. This will help to understand if the sl and tp are set correctly


    # Reports
    report_name = getattr(bt_config, "REPORT_NAME", None)
    trades_path, equity_path, stats_path, features_path, accuracy_path, chart_path = write_reports(
        strategy_dir, trades_df, equity_curve, stats, df, accuracy, timeframes, report_name
    )

    print("-" * 70)
    for key, value in stats.items():
        print(f"{key:>20}: {value}")
    print("-" * 70)
    print(f"Trades       -> {trades_path}")
    print(f"Equity curve -> {equity_path}")
    print(f"Stats        -> {stats_path}")
    print(f"Features     -> {features_path}")
    print(f"VWAP accuracy-> {accuracy_path}")
    print(f"VWAP chart   -> {chart_path}")
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
