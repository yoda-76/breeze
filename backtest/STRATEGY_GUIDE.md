# Writing a new strategy

This explains how to write `strategy.py` for a new strategy - the *only*
file you need to touch to test a new idea. `backtest/main.py` (data
loading, VWAP, trade construction, P&L, statistics, reports) never
changes.

## 1. Create the folder

```
backtest/
└── my_new_strategy/
    ├── strategy.py
    └── reports/          <- created automatically on first run
```

## 2. The contract

`strategy.py` must define exactly one function:

```python
def generate_target_position(df):
    """
    df: DataFrame indexed by tz-aware timestamp (Asia/Kolkata), columns:
        open, high, low, close, volume, symbol, exchange, vwap

    Returns a pandas.Series, SAME INDEX as df, of target positions:
        0  -> flat
        1  -> long 1x size
       -1  -> short 1x size
        2  -> long 2x size (or any other magnitude), etc.
    """
    ...
    return target_position
```

That's it. `main.py` reads this series and, every time the value changes
from one bar to the next, opens/closes a trade at that bar's `close`
price. You never touch trade construction, P&L, or report-writing code
yourself - `main.py` (specifically `build_trades()`) does all of that
based only on the series you return.

Strategies that need intrabar execution (for example, stop-loss and target
orders) may instead expose `generate_trades(df, quantity)`.  It must return
the standard reports trade ledger with the columns in `trades.csv`.  The
bundled `drift_vwap_long_short` strategy uses this optional contract so its
stops and targets are tested against each 1-minute bar's high/low rather than
being incorrectly filled at a later close.

- **Sign** of the value decides the side: positive = LONG, negative = SHORT.
- **Magnitude** scales the quantity: a target of `2` opens a position of
  `2 * QUANTITY` (where `QUANTITY` comes from `backtest/config.py`).
- Going from `1` straight to `-1` (long directly to short, no flat bar in
  between) is a single instantaneous flip: `main.py` closes the long and
  opens the short at the same bar/price.

## 3. What's in `df`

Whatever `data_loader.get_data()` returns, plus the VWAP feature set added
by `features.vwap.add_multi_timeframe_vwap()` before your function ever sees
it:

| column | notes |
|---|---|
| `open, high, low, close` | float |
| `volume` | float |
| `symbol`, `exchange` | constant for the whole backtest |
| `vwap` | base-chart session VWAP |
| `vwap_5m`, `vwap_15m`, `vwap_30m` | session VWAP calculated from completed higher-timeframe OHLCV bars, backward-aligned to the base chart |

The index is a tz-aware (`Asia/Kolkata`) `DatetimeIndex`, one row per
1-minute bar, spanning every day in the backtest's date range back to back
(no gaps inserted between sessions).

## 4. A minimal example

The bundled `vwap_crossover` strategy is about as simple as it gets:

```python
import pandas as pd

def generate_target_position(df):
    target = (df["close"] > df["vwap"]).astype(int)

    # Never carry a position overnight: force flat on the last bar of
    # each session.
    session = df.index.date
    is_last_bar_of_session = pd.Series(session, index=df.index).ne(
        pd.Series(session, index=df.index).shift(-1)
    )
    target[is_last_bar_of_session] = 0

    return target
```

Copy this file into your new strategy folder as a starting point and
change the condition.

## 5. A slightly richer example (long AND short)

```python
import pandas as pd

def generate_target_position(df):
    distance_pct = (df["close"] - df["vwap"]) / df["vwap"] * 100

    target = pd.Series(0, index=df.index)
    target[distance_pct > 0.5] = 1    # price well above VWAP -> long
    target[distance_pct < -0.5] = -1  # price well below VWAP -> short

    session = df.index.date
    is_last_bar_of_session = pd.Series(session, index=df.index).ne(
        pd.Series(session, index=df.index).shift(-1)
    )
    target[is_last_bar_of_session] = 0

    return target
```

## 6. Point the backtest at it

Either edit `backtest/config.py`:

```python
STRATEGY = "my_new_strategy"
```

or leave `config.py` alone and pass it on the command line:

```bash
cd backtest
python main.py --strategy my_new_strategy
```

`SYMBOL`, `INTERVAL`, `START_DATE`, `END_DATE`, `QUANTITY`, and
`INITIAL_CAPITAL` in `backtest/config.py` apply to whichever strategy you
run - they aren't per-strategy settings.

## 7. Run it

```bash
python main.py
```

Reports land in `backtest/my_new_strategy/reports/`:
`trades.csv`, `equity_curve.csv`, `stats.csv`.

## Things to watch out for

- **No look-ahead bias.** Only use `df` values at or before the current
  row to decide that row's target position. Don't do anything that
  effectively "knows" a future close/high/low (e.g. don't shift a column
  backwards in time). `vwap` at row `i` is only computed from bars up to
  and including `i`, so it's safe to use directly. Higher-timeframe VWAP
  columns are updated only after their corresponding candle has completed.
- **NaN handling.** `vwap` can be `NaN` on a bar with zero cumulative
  volume so far (rare, but possible right at a session's first tick if
  that tick has zero volume). A comparison like `close > vwap` safely
  evaluates to `False` when `vwap` is `NaN`, so this usually isn't a
  problem, but be aware of it if you do arithmetic on `vwap` directly.
- **One position at a time.** The engine doesn't support scaling in/out
  gradually - only a single target position per bar, fully replaced each
  time it changes.
- **Don't mutate `df`.** Your function receives the same DataFrame
  `main.py` also uses for trade fills - build new Series (`pd.Series(...)`,
  boolean masks, etc.) rather than writing new columns onto `df` itself.
- **Vectorize, don't loop.** `generate_target_position` runs over the
  whole backtest range at once (potentially tens of thousands of 1-minute
  bars) - use pandas boolean/vectorized operations like the examples
  above rather than a Python `for` loop over rows, or it'll be very slow
  on multi-month backtests.
