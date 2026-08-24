import argparse
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


# ============================================================
# CONFIG
# ============================================================

DEFAULT_DATA_DIR = Path("data/processed/parquet")
DEFAULT_SYMBOL = "RELIND"

MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"


# ============================================================
# LOAD DATA
# ============================================================

def load_data(data_dir: Path, symbol: str, start: str, end: str):
    """
    Load 1-minute OHLCV data for the requested symbol/date range.

    Expected columns:
        timestamp, open, high, low, close, volume

    The function searches recursively for parquet files.
    """

    symbol_dir = data_dir / symbol

    if not symbol_dir.exists():
        raise FileNotFoundError(
            f"Symbol directory not found:\n{symbol_dir}"
        )

    files = sorted(symbol_dir.rglob("*.parquet"))

    if not files:
        raise FileNotFoundError(
            f"No parquet files found under:\n{symbol_dir}"
        )

    print(f"Found {len(files)} parquet file(s)")

    frames = []

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)

    for file in files:
        try:
            df = pd.read_parquet(file)
        except Exception as e:
            print(f"Skipping {file}: {e}")
            continue

        if df.empty:
            continue

        # ----------------------------------------------------
        # Handle timestamp
        # ----------------------------------------------------

        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")

        elif isinstance(df.index, pd.DatetimeIndex):
            pass

        else:
            print(f"Skipping {file}: no timestamp column/index")
            continue

        # Remove timezone if present so everything is comparable
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # ----------------------------------------------------
        # Required columns
        # ----------------------------------------------------

        required = {"open", "high", "low", "close", "volume"}

        if not required.issubset(df.columns):
            print(f"Skipping {file}: missing OHLCV columns")
            continue

        df = df[list(required)].copy()

        # ----------------------------------------------------
        # Date filter
        # ----------------------------------------------------

        df = df[
            (df.index >= start_ts) &
            (df.index < end_ts)
        ]

        if not df.empty:
            frames.append(df)

    if not frames:
        raise ValueError(
            f"No data found for {symbol} between {start} and {end}"
        )

    df = pd.concat(frames)

    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    # Keep normal NSE session
    df = df.between_time(MARKET_OPEN, MARKET_CLOSE)

    return df


# ============================================================
# 15-MINUTE VWAP
# ============================================================

def calculate_15m_vwap(df):
    """
    Calculate 15-minute session VWAP.

    This follows the same basic logic as the existing vwap.py:

        typical_price = (H + L + C) / 3

        VWAP =
            cumulative(typical_price * volume)
            /
            cumulative(volume)

    Important:
        The 15-minute VWAP is only made available after
        the 15-minute candle has completed.
    """

    frames = []

    # Process each trading session independently
    for session_date, session_df in df.groupby(df.index.date):

        bars = session_df.resample(
            "15min",
            origin="start_day",
            offset="9h15min",
            label="right",
            closed="left",
        ).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })

        bars = bars.dropna(
            subset=["open", "high", "low", "close"]
        )

        if bars.empty:
            continue

        # HLC3
        typical_price = (
            bars["high"]
            + bars["low"]
            + bars["close"]
        ) / 3.0

        pv = typical_price * bars["volume"]

        # Session cumulative VWAP
        cumulative_pv = pv.cumsum()
        cumulative_volume = bars["volume"].cumsum()

        bars["vwap_15m"] = (
            cumulative_pv / cumulative_volume
        )

        frames.append(
            bars[["vwap_15m"]]
        )

    if not frames:
        return pd.Series(
            index=df.index,
            dtype=float,
            name="vwap_15m"
        )

    vwap_15m = pd.concat(frames).sort_index()

    return vwap_15m


# ============================================================
# 5-MINUTE CANDLES
# ============================================================

def create_5m_candles(df):
    """
    Convert 1-minute data into 5-minute candles.
    """

    candles = (
        df.resample(
            "5min",
            origin="start_day",
            offset="9h15min",
            label="right",
            closed="left",
        )
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
    )

    candles = candles.dropna(
        subset=["open", "high", "low", "close"]
    )

    return candles


# ============================================================
# ALIGN 15M VWAP TO 5M CANDLES
# ============================================================

def align_vwap_to_5m(candles_5m, vwap_15m):
    """
    Assign each completed 15-minute VWAP to the 5-minute
    candles that occur after that 15-minute candle has closed.

    Example:

        09:15 -> 09:30 15m candle
        VWAP becomes available at 09:30

        Therefore:
            09:20 -> no 15m VWAP
            09:25 -> no 15m VWAP
            09:30 -> first available 15m VWAP
            09:35 -> same VWAP
            09:40 -> same VWAP
            09:45 -> updated VWAP
    """

    left = candles_5m.reset_index()
    right = vwap_15m.reset_index()

    left = left.rename(columns={
        left.columns[0]: "timestamp"
    })

    right = right.rename(columns={
        right.columns[0]: "timestamp"
    })

    left["session"] = left["timestamp"].dt.normalize()
    right["session"] = right["timestamp"].dt.normalize()

    merged = pd.merge_asof(
        left.sort_values("timestamp"),
        right.sort_values("timestamp"),
        on="timestamp",
        by="session",
        direction="backward",
    )

    merged = merged.set_index("timestamp")

    return merged


# ============================================================
# PLOT
# ============================================================

def plot_chart(df, symbol, start, end):
    fig = go.Figure()

    # --------------------------------------------------------
    # Candlestick
    # --------------------------------------------------------

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=f"{symbol} 5m",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        )
    )

    # --------------------------------------------------------
    # 15m VWAP
    # --------------------------------------------------------

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["vwap_15m"],
            mode="lines",
            name="15m VWAP",
            line=dict(
                width=2,
                color="#2962FF",
            ),
            connectgaps=False,
        )
    )

    # --------------------------------------------------------
    # Layout
    # --------------------------------------------------------

    fig.update_layout(
        title=(
            f"{symbol} — 5 Minute Candles + "
            f"15 Minute Session VWAP"
        ),

        xaxis_title="Time",
        yaxis_title="Price",

        template="plotly_dark",

        hovermode="x unified",

        height=800,

        xaxis=dict(
            rangeslider=dict(
                visible=False
            ),

            showgrid=True,

            # Vertical crosshair
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            spikethickness=1,
            spikecolor="gray",
        ),

        yaxis=dict(
            showgrid=True,

            # Horizontal crosshair
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            spikethickness=1,
            spikecolor="gray",
        ),

        hoverlabel=dict(
            namelength=-1
        ),

        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
    )

    # Remove Plotly's default weekend gaps.
    fig.update_xaxes(
        rangebreaks=[
            dict(bounds=["sat", "mon"])
        ]
    )

    # --------------------------------------------------------
    # Save HTML
    # --------------------------------------------------------

    output = (
        f"{symbol}_5m_15m_vwap_"
        f"{start}_to_{end}.html"
    )

    fig.write_html(
        output,
        include_plotlyjs=True,
    )

    print()
    print("=" * 70)
    print("CHART CREATED")
    print("=" * 70)
    print(f"File: {output}")
    print()
    print("Open the HTML file in your browser.")
    print("Move the mouse over the chart to use the crosshair.")
    print("Scroll to zoom.")
    print("Drag to pan.")
    print("=" * 70)

    fig.show()


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Plot 5m candlesticks with 15m session VWAP"
        )
    )

    parser.add_argument(
        "--symbol",
        default=DEFAULT_SYMBOL,
    )

    parser.add_argument(
        "--start",
        required=True,
        help="Start date, e.g. 2026-08-01",
    )

    parser.add_argument(
        "--end",
        required=True,
        help="End date, e.g. 2026-08-14",
    )

    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
    )

    args = parser.parse_args()

    data_dir = Path(args.data_dir)

    print("=" * 70)
    print("5M CANDLE + 15M VWAP CHART")
    print("=" * 70)
    print(f"Symbol : {args.symbol}")
    print(f"Start  : {args.start}")
    print(f"End    : {args.end}")
    print()

    # --------------------------------------------------------
    # Load 1m data
    # --------------------------------------------------------

    print("Loading 1m data...")

    df = load_data(
        data_dir=data_dir,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
    )

    print(f"Loaded {len(df):,} 1m bars")

    # --------------------------------------------------------
    # Calculate 15m VWAP
    # --------------------------------------------------------

    print("Calculating 15m VWAP...")

    vwap_15m = calculate_15m_vwap(df)

    # --------------------------------------------------------
    # Create 5m candles
    # --------------------------------------------------------

    print("Creating 5m candles...")

    candles_5m = create_5m_candles(df)

    print(f"Created {len(candles_5m):,} 5m candles")

    # --------------------------------------------------------
    # Align completed 15m VWAP
    # --------------------------------------------------------

    print("Aligning completed 15m VWAP...")

    chart_df = align_vwap_to_5m(
        candles_5m,
        vwap_15m,
    )

    # --------------------------------------------------------
    # Plot
    # --------------------------------------------------------

    plot_chart(
        chart_df,
        args.symbol,
        args.start,
        args.end,
    )


if __name__ == "__main__":
    main()