# Futures backtest plan

## Current readiness

The engine is partly reusable for futures: it supports long and short trades,
intraday OHLCV, fixed quantities, intrabar stop/target checks, transaction
costs, and slippage.  It is **not ready for a reliable futures backtest** as
is, because it assumes one continuous equity symbol and shares.

## Required changes

1. **Contract-aware data model (medium change).** Store `underlying`, expiry,
   instrument type, contract symbol, and lot size with every candle. Download
   each expiry separately; do not treat a changing futures symbol as one cash
   equity series.
2. **Rollover / continuous series (medium-to-large).** Either backtest each
   expiry independently and force settlement exits, or build a documented
   continuous series with a roll rule (for example, roll N sessions before
   expiry based on volume/open interest). Preserve the actual tradable
   contract for fills; a simple stitched price series can invent returns.
3. **Lot sizing and margin (medium change).** Replace share `QUANTITY` with
   `LOTS × LOT_SIZE`; calculate exposure, initial/maintenance margin, margin
   utilisation, and margin-call/liquidation behaviour. Lot size changes over
   history must be versioned.
4. **Futures-specific costs (medium change).** Model brokerage, exchange and
   clearing fees, GST, STT/CTT where applicable, stamp duty, and slippage as
   quantity/notional-aware costs rather than only a flat round-trip number.
5. **Settlement and expiry controls (small-to-medium).** Prevent entry after
   a configurable expiry cutoff, settle or roll open positions, and account
   for physical-delivery rules where relevant.

## Suggested implementation sequence

Start with a single near-month, cash-settled index future and explicit expiry
exits. Then add lot/margin and costs. Only after that add an auditable rollover
engine and continuous-contract research view. This is roughly a medium-sized
engine/data-model extension, not a strategy-only configuration change.
