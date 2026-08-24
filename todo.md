# System TODO

## 1. F&O Data Foundation

* [ ] Add F&O support to the data download/processing pipeline while keeping the existing Equity system intact
* [ ] Support NIFTY futures historical data
* [ ] Support NIFTY options historical data
* [ ] Store option metadata: expiry, strike, CE/PE, underlying
* [ ] Store OHLCV + Open Interest
* [ ] Update the data loader to support multiple instruments and synchronized timestamps
* [ ] Keep the raw data format extensible for future brokers/data sources

## 2. Live Order-Flow Data

* [ ] Integrate Dhan WebSocket market data
* [ ] Record LTP, LTQ and timestamps
* [ ] Record 200-level bid/ask market depth
* [ ] Build a persistent raw market-data recorder
* [ ] Design storage so the raw tape/depth data can be replayed later
* [ ] Handle feed disconnects, reconnects and duplicate/out-of-order events

## 3. Derived Market Features

### Existing / Basic

* [ ] VWAP
* [ ] Market structure / price action

  * [ ] Swing highs/lows
  * [ ] HH/HL/LH/LL
  * [ ] BOS
  * [ ] CHOCH
  * [ ] Liquidity sweeps / rejection

### Volume Profile

* [ ] POC
* [ ] VAH
* [ ] VAL
* [ ] HVN
* [ ] LVN
* [ ] Session/day profiles
* [ ] Historical profile levels

### Footprint

* [ ] Classify trades as aggressive buy/sell using trade price vs bid/ask
* [ ] Volume-at-price
* [ ] Buy volume
* [ ] Sell volume
* [ ] Delta
* [ ] Delta imbalance
* [ ] Stacked imbalance
* [ ] Absorption
* [ ] Exhaustion

### Big Trades

* [ ] Build dynamic trade-size distribution
* [ ] Detect statistically large trades
* [ ] Classify large aggressive buys/sells
* [ ] Aggregate large-buy/sell volume
* [ ] Build large-trade pressure/imbalance features

### Options / GEX

* [ ] Calculate IV
* [ ] Calculate Delta
* [ ] Calculate Gamma
* [ ] Calculate GEX per strike
* [ ] Calculate total GEX
* [ ] Identify major GEX levels
* [ ] Identify gamma-flip level
* [ ] Identify positive/negative gamma regime
* [ ] Ensure all calculations are strictly point-in-time to prevent look-ahead bias

## 4. Backtesting Engine

* [ ] Update backtesting engine to support F&O and multi-instrument data
* [ ] Synchronize futures, options and order-flow data by timestamp
* [ ] Allow strategies to consume derived features
* [ ] Preserve compatibility with existing Equity strategies
* [ ] Implement realistic candle-close/next-candle execution
* [ ] Handle SL, TP, commissions and slippage
* [ ] Add trade-level MAE/MFE analysis
* [ ] Add robust performance statistics

## 5. GEX Reversal Experiment

* [ ] Define objective criteria for a "major GEX level"
* [ ] Define what constitutes a GEX interaction
* [ ] Define reversal confirmation
* [ ] Define entry timing
* [ ] Define SL/TP methodology
* [ ] Backtest GEX-only reversals
* [ ] Compare against random/non-GEX levels
* [ ] Analyse expectancy, profit factor, drawdown and trade count
* [ ] Test across different market regimes
* [ ] Validate that results survive out-of-sample testing

## 6. Full Discretionary-System Automation

* [ ] Combine market structure + volume profile + footprint + big trades + GEX
* [ ] Convert discretionary observations into measurable features
* [ ] Build a unified market-state representation
* [ ] Define setup → confirmation → entry → SL → TP → invalidation rules
* [ ] Backtest the complete system
* [ ] Compare individual features vs combinations
* [ ] Identify which features actually add predictive value

## 7. Visual Validation

### Local Charts

* [ ] Plot trades over price charts
* [ ] Plot entry, exit, SL and TP
* [ ] Plot VWAP
* [ ] Plot market-structure levels
* [ ] Plot volume-profile levels
* [ ] Plot GEX levels
* [ ] Plot footprint/big-trade information
* [ ] Build a synchronized chart for manually validating algorithm decisions

### TradingView

* [ ] Plot backtest trades over the corresponding TradingView chart
* [ ] Plot custom/in-house GEX levels
* [ ] Plot derived features
* [ ] Create a Pine-based visualization/import mechanism for external backtest results
* [ ] Use TradingView charts to visually compare algorithmic decisions with actual price action

## 8. Live System — Later

* [ ] Real-time feature calculation from Dhan feed
* [ ] Real-time market structure
* [ ] Real-time volume profile
* [ ] Real-time footprint
* [ ] Real-time big-trade detection
* [ ] Real-time GEX
* [ ] Real-time strategy signals
* [ ] Risk management layer
* [ ] Paper-trading mode
* [ ] Live execution only after extensive validation
