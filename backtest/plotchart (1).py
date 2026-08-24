"""
Plot a 5-minute candlestick chart with the completed 15-minute VWAP
and backtest trades drawn as TradingView-style risk/reward boxes.

Example:
    py plotchart.py --symbol RELIND --start 2026-07-01 --end 2026-07-02

Optional:
    --trades PATH/to/trades.csv

The script reads 1-minute OHLCV data through the project's data_loader,
builds 5-minute candles, calculates the same session VWAP logic used by
the strategy, and overlays trades from trades.csv.

Trade boxes:
    LONG:
        green = entry -> target
        red   = entry -> stop

    SHORT:
        green = entry -> target
        red   = entry -> stop

The horizontal width of each box is from entry time to exit time.
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


# ---------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import data_loader  # noqa: E402
from features.vwap import calculate_session_vwap, resample_ohlcv  # noqa: E402


# ---------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------

def load_5m_data(symbol, start_date, end_date):
    """Load 1m data and aggregate it into completed 5m candles."""

    df = data_loader.get_data(symbol, "1m", start_date, end_date)

    if df.empty:
        raise ValueError(
            f"No 1-minute data found for {symbol} "
            f"between {start_date} and {end_date}."
        )

    bars_5m = resample_ohlcv(df, "5m")

    if bars_5m.empty:
        raise ValueError("Could not create 5-minute candles.")

    return df, bars_5m


def build_15m_vwap(df_1m):
    """
    Build the same completed 15m VWAP used by the strategy.

    The strategy creates completed 15m candles, calculates session VWAP
    from those candles, then makes that value available to subsequent
    5m decision points.
    """

    bars_15m = resample_ohlcv(df_1m, "15m")

    if bars_15m.empty:
        raise ValueError("Could not create 15-minute candles.")

    bars_15m["vwap"] = calculate_session_vwap(bars_15m)

    # Make the availability timestamp explicit.
    # resample_ohlcv labels the 15m bar at its right edge.
    # The completed bar is available at that timestamp.
    vwap_15 = bars_15m[["vwap"]].copy()
    vwap_15.index.name = "timestamp"

    return vwap_15


def align_15m_vwap_to_5m(bars_5m, vwap_15):
    """Backward-asof join the last completed 15m VWAP onto 5m candles."""

    left = (
        bars_5m.reset_index()
        .rename(columns={
            bars_5m.index.name or "index": "timestamp"
        })
        .sort_values("timestamp")
    )

    right = (
        vwap_15.reset_index()
        .rename(columns={
            vwap_15.index.name or "index": "timestamp"
        })
        .sort_values("timestamp")
    )

    result = pd.merge_asof(
        left,
        right,
        on="timestamp",
        direction="backward",
    )

    return result.set_index("timestamp")


# ---------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------

def find_default_trades_file():
    """
    Find the most recently modified backtest trades.csv.

    This lets the command remain simple when the user already has reports.
    Use --trades if a specific report should be plotted.
    """

    candidates = []

    search_roots = [
        SCRIPT_DIR / "backtest",
        SCRIPT_DIR,
    ]

    for root in search_roots:
        if not root.exists():
            continue

        for path in root.rglob("trades.csv"):
            # Prefer actual report files.
            if "reports" in path.parts:
                candidates.append(path)

    if not candidates:
        raise FileNotFoundError(
            "Could not automatically find trades.csv. "
            "Use --trades path\\to\\trades.csv."
        )

    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_trades(path):
    """Load and normalize the backtest trade ledger."""

    trades = pd.read_csv(path)

    required = {
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "side",
    }

    missing = required.difference(trades.columns)

    if missing:
        raise ValueError(
            f"trades.csv is missing columns: {sorted(missing)}"
        )

    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])

    trades["entry_price"] = pd.to_numeric(trades["entry_price"])
    trades["exit_price"] = pd.to_numeric(trades["exit_price"])

    trades["side"] = trades["side"].str.upper()

    return trades


# ---------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------

def add_trade_box(fig, trade):
    """
    Add a TradingView-style risk/reward box.

    The box runs horizontally from entry_time to exit_time.

    For LONG:
        red   : entry -> stop
        green : entry -> target

    For SHORT:
        red   : entry -> stop
        green : target -> entry
    """

    entry_time = trade["entry_time"]
    exit_time = trade["exit_time"]
    entry = float(trade["entry_price"])

    side = trade["side"]

    # Prefer explicit stop/target columns if a future report contains them.
    # Otherwise infer them from the current strategy's configured defaults.
    if "stop_price" in trade.index and pd.notna(trade["stop_price"]):
        stop = float(trade["stop_price"])
    elif "stop" in trade.index and pd.notna(trade["stop"]):
        stop = float(trade["stop"])
    else:
        # Current strategy defaults.
        stop = entry - 40.0 if side == "LONG" else entry + 50.0

    if "target_price" in trade.index and pd.notna(trade["target_price"]):
        target = float(trade["target_price"])
    elif "target" in trade.index and pd.notna(trade["target"]):
        target = float(trade["target"])
    else:
        target = entry + 80.0 if side == "LONG" else entry - 80.0

    # Small transparent fills similar to the attached chart.
    red_fill = "rgba(239, 68, 68, 0.22)"
    green_fill = "rgba(16, 185, 129, 0.22)"

    red_line = "rgba(239, 68, 68, 0.55)"
    green_line = "rgba(16, 185, 129, 0.55)"

    # Loss / stop area
    fig.add_shape(
        type="rect",
        x0=entry_time,
        x1=exit_time,
        y0=min(entry, stop),
        y1=max(entry, stop),
        fillcolor=red_fill,
        line=dict(color=red_line, width=0.5),
        layer="below",
    )

    # Profit / target area
    fig.add_shape(
        type="rect",
        x0=entry_time,
        x1=exit_time,
        y0=min(entry, target),
        y1=max(entry, target),
        fillcolor=green_fill,
        line=dict(color=green_line, width=0.5),
        layer="below",
    )

    # Entry marker
    fig.add_trace(
        go.Scatter(
            x=[entry_time],
            y=[entry],
            mode="markers",
            marker=dict(
                size=7,
                symbol="triangle-up" if side == "LONG" else "triangle-down",
            ),
            name=f"{side} entry",
            legendgroup="trades",
            showlegend=False,
            hovertemplate=(
                f"{side}<br>"
                f"Entry: %{{y:.2f}}<br>"
                f"Time: %{{x|%H:%M}}"
                "<extra></extra>"
            ),
        )
    )


def make_chart(bars_5m, trades, symbol, start_date, end_date):
    fig = go.Figure()

    # Candles
    fig.add_trace(
        go.Candlestick(
            x=bars_5m.index,
            open=bars_5m["open"],
            high=bars_5m["high"],
            low=bars_5m["low"],
            close=bars_5m["close"],
            name="5m",
            increasing_line_color="#10b981",
            increasing_fillcolor="#10b981",
            decreasing_line_color="#ef4444",
            decreasing_fillcolor="#ef4444",
            whiskerwidth=0.5,
        )
    )

    # 15m VWAP
    fig.add_trace(
        go.Scatter(
            x=bars_5m.index,
            y=bars_5m["vwap15"],
            mode="lines",
            line=dict(width=1.4, color="#2563eb"),
            name="15m VWAP",
            hovertemplate=(
                "Time: %{x|%Y-%m-%d %H:%M}"
                "<br>15m VWAP: %{y:.2f}"
                "<extra></extra>"
            ),
        )
    )

    # Trade boxes + entry markers
    for _, trade in trades.iterrows():
        # Only plot trades that overlap the requested chart window.
        if trade["exit_time"] < bars_5m.index.min():
            continue
        if trade["entry_time"] > bars_5m.index.max():
            continue

        add_trade_box(fig, trade)

    # Exit markers are useful, but keep them subtle.
    for _, trade in trades.iterrows():
        if trade["exit_time"] < bars_5m.index.min():
            continue
        if trade["entry_time"] > bars_5m.index.max():
            continue

        fig.add_trace(
            go.Scatter(
                x=[trade["exit_time"]],
                y=[trade["exit_price"]],
                mode="markers",
                marker=dict(size=6, symbol="x"),
                name="Exit",
                legendgroup="exit",
                showlegend=False,
                hovertemplate=(
                    f"{trade['side']} exit"
                    "<br>Price: %{y:.2f}"
                    "<br>Time: %{x|%H:%M}"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title=(
            f"{symbol} — 5m Candles + 15m VWAP + Trades"
            f"<br><sup>{start_date} → {end_date}</sup>"
        ),
        xaxis=dict(
            title="Time",
            rangeslider=dict(visible=False),
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            showline=True,
        ),
        yaxis=dict(
            title="Price",
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            showline=True,
        ),
        hovermode="x",
        template="plotly_dark",
        height=850,
        margin=dict(l=70, r=30, t=85, b=55),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
        ),
    )

    return fig


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Plot 5m candles, 15m VWAP and backtest trade boxes."
    )

    parser.add_argument(
        "--symbol",
        default="RELIND",
        help="Symbol, e.g. RELIND",
    )

    parser.add_argument(
        "--start",
        required=True,
        help="Start date, e.g. 2026-07-01",
    )

    parser.add_argument(
        "--end",
        required=True,
        help="End date, e.g. 2026-07-02",
    )

    parser.add_argument(
        "--data-dir",
        default=None,
        help="Kept for compatibility with the previous plotting script.",
    )

    parser.add_argument(
        "--trades",
        default=None,
        help="Path to a specific trades.csv. If omitted, latest report is used.",
    )

    parser.add_argument(
        "--output",
        default=None,
        help="HTML output path. If omitted, creates plot_<symbol>_<start>_<end>.html.",
    )

    args = parser.parse_args()

    print("Loading data...")

    df_1m, bars_5m = load_5m_data(
        args.symbol,
        args.start,
        args.end,
    )

    print(f"Loaded {len(df_1m):,} one-minute bars.")
    print(f"Created {len(bars_5m):,} five-minute candles.")

    print("Calculating 15-minute VWAP...")

    vwap_15 = build_15m_vwap(df_1m)
    chart = align_15m_vwap_to_5m(bars_5m, vwap_15)

    # Only keep candles with a VWAP available.
    chart = chart.dropna(subset=["vwap"])

    chart = chart.rename(columns={"vwap": "vwap15"})

    if chart.empty:
        raise ValueError("No 5-minute candles with 15-minute VWAP available.")

    print("Loading trades...")

    trades_path = (
        Path(args.trades)
        if args.trades
        else find_default_trades_file()
    )

    trades = load_trades(trades_path)

    print(f"Trades file: {trades_path}")
    print(f"Trades loaded: {len(trades)}")

    fig = make_chart(
        chart,
        trades,
        args.symbol,
        args.start,
        args.end,
    )

    output = (
        Path(args.output)
        if args.output
        else SCRIPT_DIR
        / f"plot_{args.symbol}_{args.start}_to_{args.end}.html"
    )

    fig.write_html(
        output,
        include_plotlyjs=True,
        full_html=True,
        auto_open=True,
    )

    print()
    print("=" * 70)
    print(f"Chart written to: {output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
